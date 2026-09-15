import sqlite3
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.test_publisher_remote import PublisherRemoteTests
from src.publisher.service import get_row, log, transition, assert_upload_allowed
from src.publisher.lifecycle import confirm_received, reconcile_received, cleanup_remote
from src.publisher.worker import (
    PublisherWorker,
    PreparationError,
    StudioInterruptionHandler,
    StudioPage,
    recover_sessions,
    select_existing_draft_row,
)


class PersistenceTests(unittest.TestCase):
    setUp = PublisherRemoteTests.setUp
    batch = PublisherRemoteTests.batch
    confirm = PublisherRemoteTests.confirm

    def persisted(self):
        draft = self.batch()[0]
        self.confirm(draft)
        return draft

    def temp_file(self, draft):
        path = self.root / 'data/publisher/temp/controlled/video.mp4'
        path.parent.mkdir(parents=True)
        path.write_bytes(b'fixture')
        with self.conn:
            self.conn.execute('UPDATE publisher_drafts SET temp_path=? WHERE id=?', (str(path), draft))
        return path

    def test_transition_and_idempotence(self):
        draft = self.persisted()
        row = get_row(self.conn, 'publisher_drafts', draft)
        self.assertEqual(row['status'], 'READY_FOR_REVIEW')
        self.assertEqual(row['persistence_status'], 'PERSISTED')
        for key in ('upload_confirmed_at', 'caption_applied_at', 'tiktok_draft_persisted_at'):
            self.assertTrue(row[key])
        self.assertTrue(confirm_received(self.conn, draft, {}))
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        with self.assertRaisesRegex(ValueError, 'DRAFT_ALREADY_PERSISTED'):
            assert_upload_allowed(self.conn, draft)
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start') as start:
            with self.assertRaisesRegex(ValueError, 'DRAFT_ALREADY_PERSISTED'):
                worker.enqueue(self.conn, [draft])
            start.assert_not_called()

    def test_reconcile_received_persists_without_upload(self):
        draft = self.batch()[0]
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?",
                (draft,),
            )
            self.conn.execute(
                "UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1"
            )
            log(self.conn, draft, 'TIKTOK_DRAFT_RECONCILED', 'OK', 'fixture', 'evidence/reconciled.png')
        checks = {
            'file': True, 'upload': True, 'caption': True, 'settings': True, 'durable': True,
            'tiktok_draft_url': 'https://www.tiktok.com/tiktokstudio/content',
        }
        self.assertTrue(reconcile_received(self.conn, draft, checks))
        row = get_row(self.conn, 'publisher_drafts', draft)
        self.assertEqual(row['status'], 'READY_FOR_REVIEW')
        self.assertEqual(row['persistence_status'], 'PERSISTED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertTrue(reconcile_received(self.conn, draft, checks))

    def test_existing_draft_row_uses_checkpoint_time_when_caption_is_duplicated(self):
        rows = [
            {
                'caption_text': 'ESCOVA DE DENTES TESTE',
                'updated_text': '13 de set de 2026, 19:32',
                'duration_text': '00:23',
            },
            {
                'caption_text': 'ESCOVA DE DENTES TESTE',
                'updated_text': '13 de set de 2026, 19:55',
                'duration_text': '00:22',
            },
        ]
        selected = select_existing_draft_row(
            rows,
            'ESCOVA DE DENTES TESTE',
            '2026-09-13T22:54:41+00:00',
        )
        self.assertEqual(selected['updated_text'], '13 de set de 2026, 19:55')

    def test_existing_draft_row_fails_closed_when_duplicate_is_not_disambiguated(self):
        rows = [
            {'caption_text': 'Legenda', 'updated_text': '13 de set de 2026, 19:32'},
            {'caption_text': 'Legenda', 'updated_text': '13 de set de 2026, 19:55'},
        ]
        with self.assertRaisesRegex(PreparationError, 'Associacao do rascunho ambigua'):
            select_existing_draft_row(rows, 'Legenda', None)

    def test_cleanup_success_and_idempotence(self):
        draft = self.persisted(); path = self.temp_file(draft)
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertFalse(path.exists())
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(self.provider.deleted, ['remote-0'])
        self.assertFalse(self.provider.downloads)

    def test_404_after_delete_is_success(self):
        draft = self.persisted(); self.temp_file(draft)
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(self.provider.deleted, ['remote-0'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED')

    def test_404_before_delete_intent_requires_manual_action(self):
        draft = self.persisted()
        self.provider.deleted.append('remote-0')
        with self.assertRaisesRegex(ValueError, 'MANUAL_ACTION_REQUIRED'):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step='REMOTE_DELETE_PENDING'", (draft,)
        ).fetchone())

    def test_404_with_wrong_media_identity_requires_manual_action(self):
        draft = self.persisted()
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET remote_file_id='missing-asset' WHERE id=1")
        self.provider.deleted.append('missing-asset')
        with self.assertRaisesRegex(ValueError, 'MANUAL_ACTION_REQUIRED'):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_404_with_unpersisted_draft_is_not_cleanup_success(self):
        draft = self.batch()[0]
        self.provider.deleted.append('remote-0')
        with self.assertRaisesRegex(ValueError, 'PERSISTED_CHECKPOINT_REQUIRED'):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_sqlite_failure_after_remote_delete_reconciles_without_retry(self):
        draft = self.persisted(); self.temp_file(draft)
        with patch('src.publisher.lifecycle._mark_consumed', side_effect=sqlite3.OperationalError('simulated commit failure')):
            self.assertFalse(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(self.provider.deleted, ['remote-0'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(self.provider.deleted, ['remote-0'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED')

    def test_consumed_asset_with_missing_temp_is_idempotent(self):
        draft = self.persisted()
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertFalse(get_row(self.conn, 'publisher_media_assets', 1)['status'] == 'CONSUMED_PENDING_DELETE')
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(self.provider.deleted, ['remote-0'])

    def test_concurrent_cleanup_deletes_once(self):
        from concurrent.futures import ThreadPoolExecutor
        from src.store import connect
        draft=self.persisted()
        def finish():
            conn=connect(self.root/'test.db')
            try:return cleanup_remote(conn,draft,self.provider,self.root)
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:finish(),range(2)))
        self.assertEqual(results,[True,True])
        self.assertEqual(self.provider.deleted,['remote-0'])

    def test_consumed_checkpoint_precedes_temp_unlink(self):
        draft=self.persisted();path=self.temp_file(draft)
        original=Path.unlink
        def observed(target,*args,**kwargs):
            self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED')
            self.assertEqual(self.provider.deleted,['remote-0'])
            return original(target,*args,**kwargs)
        with patch.object(Path,'unlink',observed):
            self.assertTrue(cleanup_remote(self.conn,draft,self.provider,self.root))
        self.assertFalse(path.exists())

    def test_failure_preserves_temp_pending_and_restart_retry(self):
        draft = self.persisted(); path = self.temp_file(draft)
        self.provider.fail_delete = True
        self.assertFalse(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertTrue(path.exists())
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
        recover_sessions(self.conn)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft)['persistence_status'], 'PERSISTED')
        self.provider.fail_delete = False
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertFalse(self.provider.downloads)

    def test_crash_after_remote_delete_before_database_commit(self):
        draft = self.persisted(); path = self.temp_file(draft)
        def crash(asset):
            self.provider.deleted.append(asset['remote_file_id'])
            raise SystemExit('simulated process crash')
        self.provider.delete_remote = crash
        with self.assertRaises(SystemExit):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertTrue(path.exists())
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
        recover_sessions(self.conn)
        self.assertTrue(cleanup_remote(self.conn, draft, self.provider, self.root))
        self.assertEqual(len(self.provider.deleted), 1)

    def test_crash_after_save_before_local_confirmation_blocks_upload(self):
        draft = self.batch()[0]
        with self.conn:
            transition(self.conn, draft, 'PREPARING')
            log(self.conn, draft, 'SAVE_DRAFT', 'STARTED', 'fixture')
        recover_sessions(self.conn)
        with self.assertRaisesRegex(ValueError, 'UPLOAD_RECONCILIATION_REQUIRED'):
            assert_upload_allowed(self.conn, draft)
        with self.assertRaises(ValueError):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertFalse(self.provider.deleted)

    def test_crash_before_cleanup_preserves_reserved(self):
        draft = self.persisted(); path = self.temp_file(draft)
        recover_sessions(self.conn)
        self.assertTrue(path.exists())
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertFalse(self.provider.deleted)

    def test_cleanup_rejects_upload_in_progress(self):
        draft = self.persisted()
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='PREPARING' WHERE id=?", (draft,))
        with self.assertRaisesRegex(ValueError, 'UPLOAD_IN_PROGRESS'):
            cleanup_remote(self.conn, draft, self.provider, self.root)
        self.assertFalse(self.provider.deleted)

    def test_cache_preservation_survives_orphan_cleanup(self):
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with worker.cache.job(1, 20, preserve=True) as folder:
            path = folder / 'video.mp4'; path.write_bytes(b'fixture')
        worker.cache.clean_orphans()
        self.assertTrue(path.exists())

    def test_save_allowlist_and_real_markers(self):
        page = MagicMock(); page.url = 'https://www.tiktok.com/tiktokstudio/content'
        button = MagicMock(); button.count.return_value = 1
        empty = MagicMock(); empty.count.return_value = 0
        page.locator.return_value.count.return_value = 0
        page.get_by_role.side_effect = lambda role, name, exact: button if name == 'Salvar rascunho' else empty
        page.locator.return_value.inner_text.return_value = 'Rascunhos todos os computadores Legenda'
        page.get_by_text.return_value.count.return_value = 1
        adapter = StudioPage(page, {'durable_confirmation_validated': True})
        result = adapter.confirm_durable({'caption': 'Legenda'}, MagicMock())
        self.assertTrue(result['durable'])
        button.click.assert_called_once()
        page.goto.assert_not_called()
        self.assertNotIn('Publicar', str(page.get_by_role.call_args_list))

    def test_normal_adapter_upload_caption_save_without_publish(self):
        page=MagicMock();page.url='https://www.tiktok.com/tiktokstudio/upload'
        body=MagicMock()
        body.inner_text.return_value='Rascunhos todos os computadores Legenda'
        body.aria_snapshot.return_value='Enviado (8.02MB) Reproduzir 00:00:00 / 00:00:23 Nenhum problema encontrado Nenhum problema encontrado'
        upload=MagicMock();upload.count.return_value=1
        caption=MagicMock();caption.count.return_value=1;caption.evaluate.return_value='Legenda'
        page.locator.side_effect=lambda selector:{'body':body,'input[type=file]':upload,'[contenteditable="true"]':caption,'[role="alertdialog"], [role="dialog"]':empty}[selector]
        save=MagicMock();save.count.return_value=1
        save.click.side_effect=lambda **kw:setattr(page,'url','https://www.tiktok.com/tiktokstudio/content')
        preview=MagicMock();preview.count.return_value=1;preview.is_visible.return_value=True
        empty=MagicMock();empty.count.return_value=0
        page.get_by_role.side_effect=lambda role,name,exact:save if name=='Salvar rascunho' else preview if name=='Substituir' else empty
        page.get_by_text.return_value.count.return_value=1
        mapping={'validated':True,'durable_confirmation_validated':True,
                 'file_input':{'selector':'input[type=file]'},'caption':{'selector':'[contenteditable="true"]'}}
        adapter=StudioPage(page,mapping)
        result=adapter.prepare({'caption':'Legenda','media_asset_id':1},{},Path('fixture.mp4'),MagicMock())
        self.assertTrue(result['durable'])
        upload.set_input_files.assert_called_once_with('fixture.mp4')
        caption.fill.assert_called_once_with('Legenda')
        save.click.assert_called_once()
        page.goto.assert_called_once()
        self.assertNotIn('Publicar',str(page.get_by_role.call_args_list))

    def test_upload_waits_for_form_boot_before_selecting_file(self):
        page=MagicMock();page.url='https://www.tiktok.com/tiktokstudio/upload'
        body=MagicMock();body.inner_text.return_value='Rascunhos todos os computadores Legenda'
        body.aria_snapshot.return_value='Enviado (8.02MB) Reproduzir 00:00:00 / 00:00:23 Nenhum problema encontrado Nenhum problema encontrado'
        upload=MagicMock();upload.count.side_effect=[2,1];upload.set_input_files=MagicMock()
        caption=MagicMock();caption.count.return_value=1;caption.evaluate.return_value='Legenda'
        save=MagicMock();save.count.return_value=1;save.click.side_effect=lambda **kw:setattr(page,'url','https://www.tiktok.com/tiktokstudio/content')
        preview=MagicMock();preview.count.return_value=1;preview.is_visible.return_value=True
        empty=MagicMock();empty.count.return_value=0
        page.locator.side_effect=lambda selector:{'body':body,'input[type=file]':upload,'[contenteditable="true"]':caption,'[role="alertdialog"], [role="dialog"]':empty}[selector]
        page.get_by_role.side_effect=lambda role,name,exact:save if name=='Salvar rascunho' else preview if name=='Substituir' else empty
        page.get_by_text.return_value.count.return_value=1
        mapping={'validated':True,'durable_confirmation_validated':True,
                 'file_input':{'selector':'input[type=file]'},'caption':{'selector':'[contenteditable="true"]'}}
        adapter=StudioPage(page,mapping)
        evidence=MagicMock()
        result=adapter.prepare({'caption':'Legenda','media_asset_id':1},{},Path('fixture.mp4'),evidence)
        self.assertTrue(result['durable'])
        self.assertEqual(upload.count.call_count,2)
        page.wait_for_timeout.assert_called_once_with(500)

    def test_informational_popup_allowlist_clicks_only_entendi_once(self):
        page = MagicMock()
        dialog = MagicMock(); dialog.count.return_value = 1
        dialog.inner_text.return_value = 'Veja a prévia do seu vídeo no seu celular Agora você pode ver como seu vídeo aparecerá no TikTok.'
        button = MagicMock(); button.count.return_value = 1; button.is_visible.return_value = True; button.is_enabled.return_value = True
        dialog.get_by_role.return_value = button
        page.locator.return_value = dialog
        handler = StudioInterruptionHandler(page)
        self.assertTrue(handler.dismiss_allowed())
        button.click.assert_called_once_with(timeout=10000)
        self.assertTrue(handler.dismissed_preview_info)

    def test_blocked_popup_never_auto_accepts(self):
        page = MagicMock(); dialog = MagicMock(); dialog.count.return_value = 1
        dialog.inner_text.return_value = 'Confirmar publicação'
        page.locator.return_value = dialog
        handler = StudioInterruptionHandler(page)
        with self.assertRaisesRegex(PreparationError, 'decisao manual'):
            handler.dismiss_allowed()
        dialog.get_by_role.assert_not_called()

    def test_exact_copyright_save_modal_clicks_save_anyway_once(self):
        page = MagicMock(); dialog = MagicMock(); dialog.count.return_value = 1
        dialog.inner_text.return_value = (
            'Salvar rascunho? A verificação de direitos autorais continua em andamento, '
            'e salvar esta publicação agora interromperá o processo. '
            'Cancelar Salvar mesmo assim'
        )
        button = MagicMock(); button.count.return_value = 1
        button.is_visible.return_value = True; button.is_enabled.return_value = True
        dialog.get_by_role.return_value = button
        page.locator.return_value = dialog
        handler = StudioInterruptionHandler(page)

        self.assertTrue(handler.dismiss_allowed())
        self.assertFalse(handler.dismiss_allowed())
        button.click.assert_called_once_with(timeout=10000)
        self.assertTrue(handler.dismissed_copyright_save)

    def test_near_miss_copyright_modal_stays_manual(self):
        page = MagicMock(); dialog = MagicMock(); dialog.count.return_value = 1
        dialog.inner_text.return_value = (
            'Salvar rascunho? A verificação de direitos autorais está concluída. '
            'Cancelar Salvar mesmo assim'
        )
        page.locator.return_value = dialog
        handler = StudioInterruptionHandler(page)

        with self.assertRaisesRegex(PreparationError, 'na allowlist'):
            handler.dismiss_allowed()
        dialog.get_by_role.assert_not_called()

    def test_media_state_separates_transfer_from_ready_for_edit(self):
        page = MagicMock(); page.url = 'https://www.tiktok.com/tiktokstudio/upload'
        body = MagicMock()
        body.inner_text.return_value = 'Enviado (23,67 MB) Reproduzir 00:00:00 / 00:00:24 Nenhum problema encontrado'
        body.aria_snapshot.return_value = body.inner_text.return_value
        empty = MagicMock(); empty.count.return_value = 0
        preview = MagicMock(); preview.count.return_value = 1; preview.is_visible.return_value = True
        caption = MagicMock(); caption.count.return_value = 1; caption.is_editable.return_value = True
        page.locator.side_effect = lambda selector: body if selector == 'body' else empty if selector == '[role="alertdialog"], [role="dialog"]' else caption
        page.get_by_role.return_value = preview
        adapter = StudioPage(page, {'caption': {'selector': '[contenteditable="true"]'}})
        state = adapter.media_state()
        self.assertTrue(state['transfer_confirmed'])
        self.assertTrue(state['preview_ready'])
        self.assertTrue(state['media_ready_for_edit'])

    def test_current_upload_reconciliation_uses_dom_without_file_selection(self):
        page = MagicMock(); page.url = 'https://www.tiktok.com/tiktokstudio/upload'
        body = MagicMock()
        body.inner_text.return_value = (
            'ESCOVADENTE_G4C2A2_3007_1785433846384.mp4 '
            'Enviado（24.23MB） Reproduzir 00:00:00 / 00:00:23 '
            'Nenhum problema encontrado.'
        )
        body.aria_snapshot.return_value = body.inner_text.return_value
        caption = MagicMock(); caption.count.return_value = 1
        caption.is_editable.return_value = True
        caption.evaluate.return_value = 'ESCOVADENTE_G4C2A2_3007_1785433846384'
        preview = MagicMock(); preview.count.return_value = 1; preview.is_visible.return_value = True
        empty = MagicMock(); empty.count.return_value = 0
        page.locator.side_effect = lambda selector: (
            body if selector == 'body' else
            caption if selector == '[contenteditable="true"]' else empty
        )
        page.get_by_role.return_value = preview
        adapter = StudioPage(page, {'caption': {'selector': '[contenteditable="true"]'}})
        state = adapter.inspect_current_upload('ESCOVADENTE_G4C2A2_3007_1785433846384.mp4')
        self.assertTrue(state['media_identity_confirmed'])
        self.assertTrue(state['upload_sent_marker_present'])
        self.assertTrue(state['media_ready_for_edit'])

    def test_transfer_confirmed_timeout_requires_reconciliation(self):
        page = MagicMock(); page.url = 'https://www.tiktok.com/tiktokstudio/upload'
        body = MagicMock(); body.inner_text.return_value = 'Enviado (23,67 MB) Processando'
        body.aria_snapshot.return_value = body.inner_text.return_value
        empty = MagicMock(); empty.count.return_value = 0
        preview = MagicMock(); preview.count.return_value = 0
        caption = MagicMock(); caption.count.return_value = 1; caption.is_editable.return_value = True
        page.locator.side_effect = lambda selector: body if selector == 'body' else empty if selector == '[role="alertdialog"], [role="dialog"]' else caption
        page.get_by_role.return_value = preview
        adapter = StudioPage(page, {'caption': {'selector': '[contenteditable="true"]'}})
        draft = {}
        with patch('src.publisher.worker.monotonic', side_effect=[0, 1, 181]):
            with self.assertRaisesRegex(PreparationError, 'PROCESSING_TIMEOUT'):
                adapter.wait_processed(draft, timeout=180)
        self.assertEqual(draft['_upload_status'], 'TRANSFER_CONFIRMED')
        self.assertEqual(draft['_processing_status'], 'PROCESSING_TIMEOUT')

    def test_transfer_state_blocks_reupload_after_restart(self):
        draft = self.batch()[0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_CONFIRMED',processing_status='PROCESSING_TIMEOUT' WHERE id=?", (draft,))
        with self.assertRaisesRegex(ValueError, 'UPLOAD_RECONCILIATION_REQUIRED'):
            assert_upload_allowed(self.conn, draft)

    def test_upload_marker_alone_is_not_completion(self):
        from src.publisher.worker import PreparationError
        page=MagicMock();page.url='https://www.tiktok.com/tiktokstudio/upload'
        page.locator.return_value.inner_text.return_value='Enviado (8MB)'
        page.locator.return_value.aria_snapshot.return_value='Enviado (8MB)'
        with patch('src.publisher.worker.monotonic',side_effect=[0,1,181]):
            with self.assertRaises(PreparationError):StudioPage(page,{}).wait_processed()

    def test_http_requires_explicit_cleanup_and_probe_retired(self):
        import io
        import json
        from src.publisher.http import PublisherAPI
        draft=self.persisted()
        api=PublisherAPI(self.root,self.root/'test.db')
        handler=MagicMock()
        body=json.dumps({'id':draft}).encode()
        handler.headers={'Content-Length':str(len(body)),'Content-Type':'application/json'}
        handler.rfile=io.BytesIO(body)
        with patch.object(api.worker,'submit') as submit:
            with self.assertRaisesRegex(ValueError,'EXPLICIT_MEDIA_CONSUMPTION_CONFIRMATION_REQUIRED'):
                api.post(handler,'/api/publisher/cleanup')
            submit.assert_not_called()
            body=json.dumps({'id':draft,'human_confirmed':True}).encode()
            handler.headers['Content-Length']=str(len(body));handler.rfile=io.BytesIO(body)
            api.post(handler,'/api/publisher/cleanup')
            submit.assert_called_once_with('cleanup',draft)
        from src.publisher.functional_probe import run_probe
        with self.assertRaisesRegex(ValueError, 'PROBE_MUTATIONS_RETIRED'):
            run_probe(None, None, 'upload', {})


if __name__ == '__main__':
    unittest.main()
