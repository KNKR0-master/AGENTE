import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.publisher.queue_policy import draft_queue_presentation
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db


ROOT = Path(__file__).resolve().parents[1]


class QueueIdentityContractTests(unittest.TestCase):
    def test_snapshot_and_composite_history_use_local_draft_relationships(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(Path(directory) / 'test.db')
            try:
                init_db(conn)
                init_publisher(conn)
                service = PublisherService(conn, Path(directory))
                account = service.save_account({'name': 'Cecilia'})
                product = service.save_product({'name': 'Perfume', 'short_name': 'Kit', 'default_caption': 'Legenda'})
                with conn:
                    conn.execute("INSERT INTO drive_folders(drive_folder_id,name,relative_path,created_at,updated_at) VALUES ('folder','Perfumes','Root/Perfumes','now','now')")
                    conn.execute("INSERT INTO drive_files(drive_file_id,drive_folder_id,name,last_seen_at,created_at,updated_at) VALUES ('file','folder','canonical.mp4','now','now','now')")
                    asset = conn.execute("INSERT INTO publisher_media_assets(provider,source_account,remote_file_id,filename,mime_type,size_bytes,checksum,status,created_at,updated_at) VALUES ('google_drive','cecilia','file','video.mp4','video/mp4',100,'test-hash','AVAILABLE','now','now')").lastrowid
                batch = service.create_remote_batch({'name': 'Perfumes 05/10', 'media_asset_ids': [asset], 'product_id': product, 'account_id': account})
                draft_id = batch['draft_ids'][0]
                with conn:
                    conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED' WHERE id=?", (draft_id,))
                    for _ in range(2):
                        execution = conn.execute("INSERT INTO publisher_batch_executions(batch_id,total,status,started_at) VALUES (?,1,'COMPLETED','now')", (batch['batch_id'],)).lastrowid
                        conn.execute("INSERT INTO publisher_execution_items(execution_id,draft_id,execution_position,status) VALUES (?,?,1,'SUCCEEDED')", (execution, draft_id))
                before = conn.total_changes
                snapshot = service.snapshot()
                draft = next(d for d in snapshot['drafts'] if d['id'] == draft_id)
                expected = {'batch_name': 'Perfumes 05/10', 'product_name': 'Perfume', 'product_short_name': 'Kit',
                            'product_id': product, 'filename': 'video.mp4', 'remote_file_id': 'file',
                            'media_asset_id': asset, 'folder_id': 'folder', 'folder_name': 'Perfumes'}
                for key, value in expected.items():
                    self.assertEqual(draft[key], value, key)
                self.assertEqual(draft['queue_display']['label'], 'Salvo no TikTok')
                self.assertEqual(draft['queue_display']['persistence_label'], 'Persistência confirmada')
                history = snapshot['history']['executions']
                self.assertEqual(len(history), 2)
                self.assertEqual(len({(e['id'], e['items'][0]['draft_id']) for e in history}), 2)
                for execution in history:
                    item = execution['items'][0]
                    for key, value in expected.items():
                        self.assertEqual(item[key], value, key)
                    self.assertEqual(item['queue_display']['label'], 'Salvo no TikTok')
                self.assertEqual(conn.total_changes, before)
            finally:
                conn.close()

    def test_primary_status_and_persistence_are_current_not_historical_inferences(self):
        cases = [
            ({'status': 'READY_FOR_REVIEW'}, None, None, 'Pronto para revisão', 'ready'),
            ({'status': 'READY_FOR_REVIEW', 'persistence_status': 'PERSISTED'}, None, None, 'Salvo no TikTok', 'ready'),
            ({'status': 'MANUAL_ACTION_REQUIRED'}, None, None, 'Ação manual necessária', 'attention'),
            ({'status': 'DRAFT'}, {'status': 'NEEDS_RECONCILIATION'}, None, 'Requer verificação', 'attention'),
            ({'status': 'DRAFT', 'upload_status': 'TRANSFER_UNKNOWN'}, None, None, 'Envio a verificar', 'attention'),
            ({'status': 'FAILED'}, None, None, 'Preparação interrompida', 'attention'),
            ({'status': 'DRAFT'}, None, 'FAILED', 'Falhou nesta execução', 'attention'),
            ({'status': 'DRAFT'}, None, 'SUCCEEDED', 'Concluído nesta execução', 'neutral'),
        ]
        for draft, media, item_status, label, kind in cases:
            with self.subTest(label=label):
                display = draft_queue_presentation(draft, media, item_status=item_status)
                self.assertEqual((display['label'], display['kind']), (label, kind))
        for draft_id in (13, 25):
            display = draft_queue_presentation({'id': draft_id, 'status': 'MANUAL_ACTION_REQUIRED',
                'persistence_status': 'UNCONFIRMED', 'upload_status': 'NOT_STARTED', 'processing_status': 'NOT_STARTED'})
            self.assertEqual(display['persistence_label'], 'Persistência não confirmada')
            self.assertNotIn('upload', json.dumps(display).lower())
            self.assertNotIn('iniciado', json.dumps(display).lower())

    @unittest.skipUnless(shutil.which('node'), 'node required')
    def test_js_identity_fallbacks_and_safe_thumbnail_endpoint(self):
        script = f"const ui=require({json.dumps(str(ROOT / 'dashboard/publisher_state.js'))});" + """
          const items=[{}, {draft_id:13,remote_file_id:'file / ? #',thumbnail_link:'https://evil.example/image'},
            {id:25,filename:'long'.repeat(150)+'.mp4',product_short_name:'Kit',batch_name:'Perfumes',folder_name:'Pasta',
              queue_display:{label:'Ação manual necessária',kind:'attention',persistence_label:'Persistência não confirmada'}}];
          console.log(JSON.stringify(items.map(item=>ui.queueIdentity(item))));
        """
        result = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, encoding='utf-8', check=True)
        rows = json.loads(result.stdout)
        self.assertIsNone(rows[0]['thumbnailUrl'])
        self.assertEqual(rows[0]['product'], 'Produto não atribuído')
        self.assertEqual(rows[0]['batch'], 'Lote não informado')
        self.assertEqual(rows[1]['thumbnailUrl'], '/api/publisher/library/thumbnail/file%20%2F%20%3F%20%23')
        self.assertNotIn('evil', json.dumps(rows))
        self.assertGreater(len(rows[2]['filename']), 600)
        self.assertEqual(rows[2]['display']['kind'], 'attention')


if __name__ == '__main__':
    unittest.main()
