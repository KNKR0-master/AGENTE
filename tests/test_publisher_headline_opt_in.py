"""Per-video authorization fixtures. No browser, Drive or operational DB."""
import copy
import threading
from unittest.mock import Mock, patch
import unittest

from src.publisher import headlines, headline_workflow as workflow
from src.publisher.service import get_row
from src.publisher.upload_preparation import prepare_upload_source
from tests import test_publisher_library_batch_preflight as library_fixture
from tests import test_publisher_remote as remote_fixture


class HeadlineOptInTests(unittest.TestCase):
    setUp_fixture = library_fixture.LibraryBatchPreflightTests.setUp
    _folder = library_fixture.LibraryBatchPreflightTests._folder
    _file = library_fixture.LibraryBatchPreflightTests._file

    def setUp(self):
        self.setUp_fixture()
        self.preset = headlines.save_preset(self.conn, {'name':'Fixture', 'spec':{
            **headlines.DEFAULT_SPEC, 'font_ref':'fixture', 'font_sha256':'a'*64}})['id']
        for product, text in ((self.product_a,'ALPHA'), (self.product_b,'BETA')):
            self.service.save_product({'id':product, 'default_headline_enabled':True,
                'headline_required':True, 'default_headline_text':text,
                'default_headline_preset_id':self.preset})
        for file_id, folder in (('a1','folder-a'), ('a2','folder-a'), ('b1','folder-b')):
            self._file(file_id, folder)

    def create(self, choices=None):
        data = {'account_id':self.account_id, 'drive_file_ids':['a1','a2','b1']}
        if choices is not None: data['headline_selections'] = choices
        with patch('src.publisher.headline_configuration.machine_readiness', return_value={'ready':True}):
            result = self.service.create_library_batch(data)
        return result['draft_ids']

    def snapshots(self, ids):
        return [headlines.read_snapshot(get_row(self.conn,'publisher_drafts',i)) for i in ids]

    def test_same_product_only_explicit_first_video_enabled(self):
        snapshots = self.snapshots(self.create({'a1':{'mode':'product_default'}}))
        self.assertEqual([s['enabled'] for s in snapshots], [True,False,False])
        self.assertEqual(snapshots[0]['text'], 'ALPHA')
        self.assertFalse(any(s['required'] for s in snapshots))

    def test_configured_products_without_choices_do_not_render(self):
        ids = self.create()
        renderer = Mock(side_effect=AssertionError('No render authorization'))
        source = self.service.root/'original.mp4'
        source.write_bytes(b'local fixture; original requires no transcoding')
        for draft_id in ids:
            prepared = prepare_upload_source(self.conn, get_row(self.conn,'publisher_drafts',draft_id),
                                             source, renderer, threading.Event())
            self.assertEqual(prepared.kind,'ORIGINAL')
            self.assertEqual(prepared.path,source.resolve())
        renderer.assert_not_called()
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_derived_media_artifacts').fetchone()[0],0)

    def test_multi_product_choices_are_independent(self):
        snapshots = self.snapshots(self.create({'a1':{'mode':'none'}, 'b1':{'mode':'product_default'}}))
        self.assertEqual([s['enabled'] for s in snapshots], [False,False,True])
        self.assertEqual(snapshots[2]['text'],'BETA')

    def test_explicit_product_suggestion_works_even_if_legacy_enabled_flag_false(self):
        self.service.save_product({'id':self.product_a,'default_headline_enabled':False})
        snapshots = self.snapshots(self.create({'a1':{'mode':'product_default'}}))
        self.assertTrue(snapshots[0]['enabled'])
        self.assertEqual(snapshots[0]['provenance'],'PRODUCT_DEFAULT')

    def test_saved_template_and_custom_are_individual(self):
        template = workflow.save_template(self.conn, {'name':'Saved','text':'SAVED'})['id']
        snapshots = self.snapshots(self.create({'a1':{'mode':'template','template_id':template},
                                               'b1':{'mode':'custom','text':'CUSTOM'}}))
        self.assertEqual([s['text'] for s in snapshots],['SAVED','','CUSTOM'])
        self.assertEqual(snapshots[0]['template']['id'],template)

    def test_bulk_changes_selected_drafts_only(self):
        ids = self.create()
        before = copy.deepcopy(self.snapshots(ids))
        self.service.bulk_headline({'ids':[ids[0],ids[2]],'patch':{'mode':'product_default'}})
        after = self.snapshots(ids)
        self.assertEqual(after[1],before[1])
        self.assertEqual([s['enabled'] for s in after],[True,False,True])

    def test_style_only_bulk_does_not_enable_any_video(self):
        ids = self.create()
        self.service.bulk_headline({'ids':ids,'patch':{'preset_id':self.preset}})
        self.assertFalse(any(s['enabled'] for s in self.snapshots(ids)))

    def test_frozen_draft_blocks_bulk_atomically(self):
        ids = self.create()
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?",(ids[1],))
        before = self.snapshots(ids)
        with self.assertRaisesRegex(ValueError,'QUEUE_EDIT_BLOCKED'):
            self.service.bulk_headline({'ids':ids,'patch':{'mode':'product_default'}})
        self.assertEqual(self.snapshots(ids),before)

    def test_product_edit_does_not_change_existing_snapshots(self):
        ids = self.create({'a1':{'mode':'product_default'}})
        before = self.snapshots(ids)
        self.service.save_product({'id':self.product_a,'default_headline_text':'NEW'})
        self.assertEqual(self.snapshots(ids),before)
        self.assertEqual(headlines.render_hash(before[0]),before[0]['render_spec_hash'])

    def test_creation_does_not_schedule_upload_retry_or_execution(self):
        self.create({'a1':{'mode':'custom','text':'EXPLICIT'}})
        for table in ('publisher_batch_executions','publisher_execution_items','account_mutation_ledger'):
            self.assertEqual(self.conn.execute('SELECT count(*) FROM '+table).fetchone()[0],0)
        self.assertFalse(self.conn.execute("SELECT 1 FROM publisher_execution_logs WHERE step='UPLOAD_STARTED'").fetchone())

    def test_missing_mode_does_not_infer_opt_in_from_preset_or_text(self):
        snapshot = workflow.selection_snapshot(self.conn,self.service.get_product(self.product_a),
                                                {'preset_id':self.preset,'text':'NOT AUTHORIZATION'})
        self.assertFalse(snapshot['enabled'])
        self.assertEqual(snapshot['selection_mode'],'none')

    def test_none_does_not_require_valid_product_suggestion(self):
        product = {**self.service.get_product(self.product_a), 'default_headline_preset_id':999999,
                   'default_headline_template_id':999999}
        snapshot = workflow.selection_snapshot(self.conn,product)
        self.assertFalse(snapshot['enabled'])
        self.assertEqual(headlines.readiness(snapshot),[])


class LegacyCreationOptInTests(unittest.TestCase):
    setUp = remote_fixture.PublisherRemoteTests.setUp

    def configure(self):
        preset = headlines.save_preset(self.conn, {'name':'Fixture','spec':{
            **headlines.DEFAULT_SPEC,'font_ref':'fixture','font_sha256':'a'*64}})['id']
        self.service.save_product({'id':self.product,'default_headline_enabled':True,
            'default_headline_text':'SUGGESTION','default_headline_preset_id':preset})

    def test_remote_reference_creation_defaults_off_and_accepts_per_asset_choice(self):
        self.configure()
        ids = self.service.create_remote_batch({'product_id':self.product,'account_id':self.account,
            'media_asset_ids':[1,2], 'headline_selections':{'1':{'mode':'product_default'}}})['draft_ids']
        self.assertEqual([headlines.read_snapshot(get_row(self.conn,'publisher_drafts',i))['enabled']
                          for i in ids],[True,False])
        self.assertFalse(self.provider.downloads or self.provider.deleted)

    def test_local_creation_defaults_off_and_accepts_per_filename_choice(self):
        self.configure()
        directory = self.root/'data/publisher/uploads';directory.mkdir(parents=True)
        paths = [(name,directory/name) for name in ('one.mp4','two.mp4')]
        for _,path in paths: path.write_bytes(b'fixture only')
        ids = self.service.create_batch({'product_id':self.product,'account_id':self.account,
            'headline_selections':{'one.mp4':{'mode':'product_default'}}},paths)['draft_ids']
        self.assertEqual([headlines.read_snapshot(get_row(self.conn,'publisher_drafts',i))['enabled']
                          for i in ids],[True,False])

    def test_unknown_or_ambiguous_media_choices_rejected_without_creation(self):
        self.configure()
        with self.assertRaisesRegex(ValueError,'HEADLINE_SELECTION_INVALID'):
            self.service.create_remote_batch({'product_id':self.product,'account_id':self.account,
                'media_asset_ids':[1], 'headline_selections':{'2':{'mode':'product_default'}}})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_drafts').fetchone()[0],0)
