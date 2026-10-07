import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from src.publisher.draft_listing import LISTING_SNAPSHOT, classify_listing, resolution_safety_summary, read_hydrated_listing
from src.publisher.worker import PublisherWorker, assess_remote_draft_rows


class EmptyDraftListingTests(unittest.TestCase):
    def listing(self, **changes):
        return dict(rows=[], total=0, draft_tab_present=True, empty_state=True,
                    list_surface=True, loading=False, error=False, pagination=False, **changes)

    def test_complete_empty_needs_neither_save_checkpoint_nor_duration(self):
        result = classify_listing(self.listing())
        self.assertTrue(result['listing_complete'])
        self.assertIsNone(result['blocker_code'])
        match = assess_remote_draft_rows([], 'caption', None, None, listing_complete=True)
        self.assertEqual(match['coverage_status'], 'COMPLETE')
        self.assertEqual(match['outcome'], 'REMOTE_STATE_UNKNOWN')
        self.assertIsNone(match['blocker_code'])
        self.assertEqual(match['candidate_count'], 0)

    def test_loading_zero_is_incomplete(self):
        listing = self.listing()
        listing['loading'] = True
        result = classify_listing(listing)
        self.assertFalse(result['listing_complete'])
        self.assertEqual(result['blocker_code'], 'PARTIAL_LISTING')

    def test_error_zero_is_listing_error(self):
        listing = self.listing()
        listing['error'] = True
        result = classify_listing(listing)
        self.assertFalse(result['listing_complete'])
        self.assertEqual(result['blocker_code'], 'LISTING_ERROR')

    def test_empty_requires_all_structural_signals(self):
        for field in ('draft_tab_present', 'empty_state', 'list_surface'):
            listing = self.listing()
            listing[field] = False
            self.assertFalse(classify_listing(listing)['listing_complete'], field)
        for value in (None, True, '0', 1):
            listing = self.listing()
            listing['total'] = value
            self.assertFalse(classify_listing(listing)['listing_complete'])

    def test_worker_does_not_require_all_computers_for_empty(self):
        page = Mock()
        page.url = 'https://www.tiktok.com/tiktokstudio/content?tab=draft'
        page.locator.return_value.inner_text.return_value = 'Rascunhos 0\nAinda n\u00e3o h\u00e1 rascunhos'
        page.locator.return_value.filter.return_value.count.return_value = 1
        page.locator.return_value.filter.return_value.is_visible.return_value = True
        page.evaluate.return_value = self.listing()
        result = PublisherWorker._read_verified_draft_list(SimpleNamespace(), page, {})
        self.assertTrue(result['listing_complete'])
        self.assertIsNone(result['blocker_code'])
        self.assertEqual(result['page_identity'], {'host': 'www.tiktok.com', 'path': '/tiktokstudio/content'})

    def test_complete_nonempty_no_match_keeps_complete_coverage(self):
        row = dict(caption_text='Another caption', duration_text='00:23', updated_text='29 de set de 2026, 12:00')
        result = assess_remote_draft_rows([row], 'Expected', '2026-09-29T15:00:00+00:00', 23, listing_complete=True)
        self.assertEqual(result['coverage_status'], 'COMPLETE')
        self.assertEqual(result['outcome'], 'REMOTE_STATE_UNKNOWN')
        self.assertIsNone(result['blocker_code'])

    def test_local_not_started_remains_human_only(self):
        summary = resolution_safety_summary({'upload_status': 'NOT_STARTED', 'processing_status': 'NOT_STARTED'}, [])
        self.assertEqual(summary['duplicate_risk'], 'LOW_APPARENT')
        self.assertFalse(summary['automatic_release_allowed'])
        self.assertFalse(summary['automatic_retry_allowed'])

    def test_transfer_unknown_or_checkpoint_retains_duplicate_risk(self):
        for status, steps in [('TRANSFER_UNKNOWN', []), ('TRANSFER_IN_PROGRESS', [{'step': 'UPLOAD_STARTED'}]),
                              ('NOT_STARTED', [{'step': 'UPLOAD_STARTED'}]), ('NOT_STARTED', [{'step': 'SAVE_DRAFT'}])]:
            summary = resolution_safety_summary({'upload_status': status, 'processing_status': 'NOT_STARTED'}, steps)
            self.assertEqual(summary['duplicate_risk'], 'REMAINS')
            self.assertFalse(summary['automatic_release_allowed'])
            self.assertFalse(summary['automatic_retry_allowed'])

    def test_live_hydration_sequence_waits_for_tab_then_stable_empty(self):
        page = Mock(url='https://www.tiktok.com/tiktokstudio/content')
        tab = page.locator.return_value.filter.return_value
        tab.count.return_value, tab.is_visible.return_value = 1, True
        initial = self.listing()
        initial.update(total=None, draft_tab_present=False, list_surface=False, empty_state=False)
        tab_only = {**initial, 'total': 0, 'draft_tab_present': True}
        page.evaluate.side_effect = [initial, tab_only, self.listing(), self.listing()]
        result = read_hydrated_listing(page)
        self.assertTrue(result['listing_complete'])
        self.assertFalse(result['hydration_samples'][0]['complete'])
        self.assertTrue(result['hydration_samples'][-1]['empty_state'])
        tab.click.assert_called_once()
        page.goto.assert_called_once()
        self.assertEqual(page.wait_for_timeout.call_count, 3)

    def test_bounded_loading_never_becomes_empty(self):
        page = Mock(url='https://www.tiktok.com/tiktokstudio/content')
        page.evaluate.return_value = {**self.listing(), 'loading': True}
        result = read_hydrated_listing(page, max_samples=3)
        self.assertEqual(len(result['hydration_samples']), 3)
        self.assertFalse(result['listing_complete'])
        self.assertEqual(page.wait_for_timeout.call_count, 2)
        page.locator.assert_not_called()

    def test_challenge_and_error_stop_without_tab_click(self):
        for changes, blocker in [({'challenge': 'CAPTCHA'}, 'CAPTCHA'), ({'error': True}, 'LISTING_ERROR')]:
            page = Mock(url='https://www.tiktok.com/tiktokstudio/content')
            page.evaluate.return_value = {**self.listing(), **changes}
            result = read_hydrated_listing(page)
            self.assertFalse(result['listing_complete'])
            self.assertEqual(result['blocker_code'], blocker)
            page.wait_for_timeout.assert_not_called()
            page.locator.assert_not_called()

    def test_single_transient_complete_and_header_only_are_not_coverage(self):
        page = Mock(url='https://www.tiktok.com/tiktokstudio/content')
        page.evaluate.return_value = self.listing()
        self.assertFalse(read_hydrated_listing(page, max_samples=1)['listing_complete'])
        page.evaluate.return_value = {**self.listing(), 'empty_state': False, 'list_surface': False}
        self.assertFalse(read_hydrated_listing(page, max_samples=1)['listing_complete'])

    def test_login_redirect_stops_without_parsing_or_login_actions(self):
        page = Mock(url='https://www.tiktok.com/login?redirect_url=hidden')
        result = read_hydrated_listing(page)
        self.assertEqual(result['blocker_code'], 'AUTH_REQUIRED')
        self.assertEqual(result['page_identity']['path'], '/login')
        page.evaluate.assert_not_called()


class DraftListingDOMTests(unittest.TestCase):
    def test_real_empty_structure_and_header_are_not_media_rows(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            try:
                page = browser.new_page()
                header = '''<div data-tt="Header_HeaderTabBar_Container">Rascunhos 0</div>
                <div data-tt="components_DraftRowLayout_FlexRow">
                <div data-tt="components_DraftRowLayout_FlexRow_2">Rascunhos</div>
                <div data-tt="components_DraftRowLayout_FlexRow_3">\u00daltima atualiza\u00e7\u00e3o</div></div>'''
                empty = '''<div data-tt="components_DraftTable_FlexColumn">
                <span data-tt="components_DraftTable_TUXText">Ainda n\u00e3o h\u00e1 rascunhos</span>
                <span data-tt="components_DraftTable_TUXText">Seus rascunhos aparecer\u00e3o aqui e s\u00f3 poder\u00e3o ser vistos por voc\u00ea.</span></div>'''
                for suffix, expected in [('', None), ('<div role="progressbar">Loading</div>', 'PARTIAL_LISTING'),
                                         ('<div role="alert">Network error</div>', 'LISTING_ERROR')]:
                    page.set_content(header + empty + suffix)
                    result = classify_listing(page.evaluate(LISTING_SNAPSHOT))
                    self.assertEqual(result['rows'], [])
                    self.assertEqual(result['blocker_code'], expected)
                    self.assertEqual(result['listing_complete'], expected is None)
                page.set_content(header + '<p>Ainda n\u00e3o h\u00e1 rascunhos</p><p>Seus rascunhos aparecer\u00e3o aqui</p>')
                self.assertFalse(classify_listing(page.evaluate(LISTING_SNAPSHOT))['listing_complete'])
                page.set_content(header + empty.replace('Ainda n\u00e3o h\u00e1 rascunhos', 'No hay borradores').replace(
                    'Seus rascunhos aparecer\u00e3o aqui e s\u00f3 poder\u00e3o ser vistos por voc\u00ea.', 'Tus borradores apareceran aqui.'))
                self.assertTrue(classify_listing(page.evaluate(LISTING_SNAPSHOT))['listing_complete'])
                page.set_content(header.replace('Rascunhos 0', 'Rascunhos 1') + '''<p>Todos os computadores</p>
                <div data-tt="components_DraftRowLayout_FlexRow">
                <div data-tt="components_DraftRowLayout_FlexRow_2"><div>00:23</div><div>Caption</div></div>
                <div data-tt="components_DraftRowLayout_FlexRow_3">29 de set de 2026, 12:00</div></div>''')
                result = classify_listing(page.evaluate(LISTING_SNAPSHOT))
                self.assertTrue(result['listing_complete'])
                self.assertEqual(len(result['rows']), 1)
                self.assertEqual(result['rows'][0]['caption_text'], 'Caption')
                self.assertEqual(page.evaluate(LISTING_SNAPSHOT)['header_row_count'], 1)
                page.set_content(header + empty + '<p>Verifique se voc\u00ea \u00e9 humano</p>')
                self.assertEqual(classify_listing(page.evaluate(LISTING_SNAPSHOT))['blocker_code'], 'CAPTCHA')
            finally:
                browser.close()
