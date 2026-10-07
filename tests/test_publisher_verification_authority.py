import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from src.publisher.verification_authority import select_verification_authority, classify_verification_auth
from src.publisher.worker import PublisherWorker


HANDLE = 'cecilia.figueired65'
GOOD = {'auth_result': 'AUTHENTICATED', 'account_identity': 'ACCOUNT_CONFIRMED',
        'observed_handles': [HANDLE], 'blocker_code': None}


class Page:
    def __init__(self, url, auth=None):
        self.url = url
        self.auth = auth or dict(GOOD)
        self.closed = False
        self.navigations = []
        self.waits = []

    def is_closed(self):
        return self.closed

    def goto(self, url, **kwargs):
        self.navigations.append(url)
        self.url = url

    def close(self):
        self.closed = True

    def wait_for_timeout(self, delay):
        self.waits.append(delay)


class Context:
    def __init__(self, pages, fresh=None):
        self.pages = pages
        self.fresh = fresh or Page('about:blank')
        self.created = 0

    def new_page(self):
        self.created += 1
        self.pages.append(self.fresh)
        return self.fresh


class VerificationAuthorityTests(unittest.TestCase):
    def test_multiple_tabs_prefers_authenticated_studio_without_navigation(self):
        studio = Page('https://www.tiktok.com/tiktokstudio')
        context = Context([Page('https://www.tiktok.com/'), Page('about:blank'), studio])
        selected, owned, auth = select_verification_authority(context, lambda page: page.auth)
        self.assertIs(selected, studio)
        self.assertFalse(owned)
        self.assertEqual(auth['auth_result'], 'AUTHENTICATED')
        self.assertEqual(context.created, 0)
        self.assertTrue(all(not page.navigations for page in context.pages))

    def test_first_tab_logged_out_does_not_override_authenticated_studio(self):
        studio = Page('https://www.tiktok.com/tiktokstudio')
        context = Context([Page('https://www.tiktok.com/login'), studio])
        inspect = Mock(side_effect=lambda page: page.auth)
        self.assertIs(select_verification_authority(context, inspect)[0], studio)
        inspect.assert_called_once_with(studio)

    def test_no_studio_tab_navigates_only_new_page_in_same_context(self):
        old = Page('https://www.tiktok.com/')
        context = Context([old, Page('about:blank')])
        page, owned, auth = select_verification_authority(context, lambda page: page.auth)
        self.assertIs(page, context.fresh)
        self.assertTrue(owned)
        self.assertEqual(context.created, 1)
        self.assertEqual(page.navigations, ['https://www.tiktok.com/tiktokstudio'])
        self.assertFalse(old.navigations)
        self.assertEqual(auth['auth_result'], 'AUTHENTICATED')

    def test_genuine_login_requires_human_without_login_automation(self):
        context = Context([Page('about:blank')])
        def inspect(page):
            page.url = 'https://www.tiktok.com/login?redirect_url=fixture'
            auth = {'auth_result': 'UNKNOWN', 'login_ui_found': True}
            return {**auth, 'blocker_code': classify_verification_auth({}, auth, page)}
        page, owned, auth = select_verification_authority(context, inspect)
        self.assertEqual(auth['blocker_code'], 'AUTH_REQUIRED')
        self.assertEqual(len(page.navigations), 1)
        self.assertEqual(context.created, 1)

    def test_unknown_is_not_needs_login_and_hydration_is_bounded(self):
        context = Context([])
        unknown = {'auth_result': 'UNKNOWN', 'blocker_code': 'LISTING_ERROR'}
        page, _, auth = select_verification_authority(context, lambda page: unknown)
        self.assertEqual(auth['blocker_code'], 'LISTING_ERROR')
        self.assertEqual(len(page.waits), 4)
        self.assertEqual(len(page.navigations), 1)
        self.assertEqual(classify_verification_auth({}, unknown, page), 'LISTING_ERROR')

    def test_challenge_and_mismatch_fail_closed(self):
        for blocker in ('CAPTCHA', 'TWO_FACTOR', 'SECURITY_CHALLENGE', 'WRONG_ACCOUNT'):
            with self.subTest(blocker=blocker):
                context = Context([Page('https://www.tiktok.com/tiktokstudio', {**GOOD, 'blocker_code': blocker})])
                page, _, auth = select_verification_authority(context, lambda page: page.auth)
                self.assertIsNone(page)
                self.assertEqual(auth['blocker_code'], blocker)
                self.assertEqual(context.created, 0)
        snapshot = {'pages': [{'is_auth_page': True, 'semantic_signals': {'visible': ['CHALLENGE']}}]}
        self.assertEqual(classify_verification_auth(snapshot, GOOD, Page('https://www.tiktok.com/tiktokstudio')), 'SECURITY_CHALLENGE')

    def test_editor_is_never_navigated_even_with_good_studio_tab(self):
        context = Context([Page('https://www.tiktok.com/tiktokstudio/upload'), Page('https://www.tiktok.com/tiktokstudio')])
        inspect = Mock()
        self.assertEqual(select_verification_authority(context, inspect)[2]['blocker_code'], 'UNSAVED_EDITOR')
        inspect.assert_not_called()
        self.assertEqual(context.created, 0)

    def worker(self, context):
        worker = PublisherWorker.__new__(PublisherWorker)
        worker.contexts = {1: context}
        worker.account_profile = Mock(return_value=Path('fixture-profile'))
        worker.assert_actions_allowed = Mock()
        worker.browser_runtime = Mock()
        worker.browser_runtime.session.return_value = SimpleNamespace(context=context)
        worker._inspect_verification_auth = Mock(return_value=dict(GOOD))
        worker._read_verified_draft_list = Mock(side_effect=lambda page, result: result)
        return worker

    def test_worker_uses_owned_runtime_context_and_preserves_user_page(self):
        studio = Page('https://www.tiktok.com/tiktokstudio')
        context = Context([studio])
        worker = self.worker(context)
        result = worker._inspect_draft_list_read_only(Mock(), {'id': 1, 'tiktok_username': HANDLE})
        self.assertIsNone(result['blocker_code'])
        worker.browser_runtime.get_context.assert_not_called()
        worker._inspect_verification_auth.assert_called_once_with({'id': 1, 'tiktok_username': HANDLE}, context, studio)
        worker._read_verified_draft_list.assert_called_once_with(context.fresh, result)
        self.assertTrue(context.fresh.closed)
        self.assertFalse(studio.closed)
        self.assertFalse(studio.navigations)

    def test_missing_or_different_runtime_never_launches_browser(self):
        for runtime in (None, SimpleNamespace(context=Context([]))):
            worker = self.worker(Context([]))
            worker.browser_runtime.session.return_value = runtime
            result = worker._inspect_draft_list_read_only(Mock(), {'id': 1})
            self.assertEqual(result['blocker_code'], 'LISTING_ERROR')
            worker.browser_runtime.get_context.assert_not_called()
            worker._read_verified_draft_list.assert_not_called()

    def test_owned_page_closed_on_listing_failure(self):
        context = Context([])
        worker = self.worker(context)
        worker._read_verified_draft_list.side_effect = RuntimeError('fixture')
        with self.assertRaises(RuntimeError):
            worker._inspect_draft_list_read_only(Mock(), {'id': 1, 'tiktok_username': HANDLE})
        self.assertTrue(context.fresh.closed)

    def test_worker_unknown_cannot_reach_listing(self):
        context = Context([])
        worker = self.worker(context)
        worker._inspect_verification_auth.return_value = {'auth_result': 'UNKNOWN', 'blocker_code': 'LISTING_ERROR'}
        result = worker._inspect_draft_list_read_only(Mock(), {'id': 1, 'tiktok_username': HANDLE})
        self.assertEqual(result['blocker_code'], 'LISTING_ERROR')
        worker._read_verified_draft_list.assert_not_called()
        self.assertTrue(context.fresh.closed)

    def test_auth_uses_exact_selected_page_not_generic_body_login_phrase(self):
        page = Page('https://www.tiktok.com/tiktokstudio')
        page.locator = Mock(return_value=SimpleNamespace(inner_text=lambda **kwargs: 'Ajuda: entrar no TikTok'))
        context = Context([Page('https://www.tiktok.com/login'), page])
        worker = self.worker(context)
        worker.auth_diagnostics = Mock()
        worker.auth_diagnostics.collect.return_value = {
            'session_cookie_present': True,
            'pages': [{'is_auth_page': True, 'url': page.url, 'dom_collected': True,
                       'login_control_visible': False, 'studio_dom_inventory': [
                           {'tag': 'a', 'text': HANDLE, 'identity_scope': 'account'},
                           *[{'test_id': value} for value in ('House', 'Folder', 'ChartRise')]]}]}
        account = {'id': 1, 'tiktok_username': HANDLE}
        auth = PublisherWorker._inspect_verification_auth(worker, account, context, page)
        self.assertEqual(auth['auth_result'], 'AUTHENTICATED')
        self.assertIsNone(auth['blocker_code'])
        worker.auth_diagnostics.collect.assert_called_once_with(1, context, page)

    def test_authenticated_wrong_handle_never_reaches_draft_list(self):
        worker = self.worker(Context([Page('https://www.tiktok.com/tiktokstudio')]))
        worker._inspect_verification_auth.return_value = {**GOOD, 'observed_handles': ['someone_else']}
        result = worker._inspect_draft_list_read_only(Mock(), {'id': 1, 'tiktok_username': HANDLE})
        self.assertEqual(result['blocker_code'], 'WRONG_ACCOUNT')
        worker._read_verified_draft_list.assert_not_called()


if __name__ == '__main__':
    unittest.main()
