"""Windows-only Publisher validation with one short, owned TEMP/SQLite root.

Run with ``python -B -m tests.publisher_windows_gate probe|smoke|affected|full``.
Only test fixtures and this process's environment are changed. Product sources,
operational databases, profiles and the operational server are never initialized.
"""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

from tests.sqlite_isolation_harness import SQLiteIsolationGuard, SQLiteIsolationViolation

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT / 'data/publisher/qa/final-full-local-preparation/short-temp-recovery'
SMOKE = ('tests.test_publisher_local_preparation_browser.LocalPreparationBrowserTests.'
         'test_library_individual_bulk_progress_video_download_and_refresh')
AFFECTED = [
    'tests.test_publisher_headline_fixture_cleanup',
    'tests.test_publisher_headline_calibration_browser',
    'tests.test_publisher_headline_workflow_browser',
    'tests.test_publisher_local_preparation_browser',
]


class GateSafetyViolation(AssertionError):
    pass


def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def process_probe(run_root, guard):
    """Use the same manager/path construction as the blocked real smoke."""
    from src.publisher.local_preparation import LocalPreparationManager
    manager = LocalPreparationManager(ROOT, run_root / 'probe.db')
    cwd = manager._workspace(uuid.uuid4().hex) / 'artifacts' / uuid.uuid4().hex
    cwd.mkdir(parents=True)
    assert cwd.is_relative_to(run_root) and len(str(cwd)) < 240
    guard.require_allowed(run_root / 'probe.db')
    excluded = [ROOT / 'data/agent.db', ROOT / 'data/publisher/qa/unowned.db',
                ROOT.parent / 'New project 10/data/agent.db', run_root.parent / 'unowned.db']
    for path in excluded:
        try:
            guard.require_allowed(path)
        except SQLiteIsolationViolation:
            continue
        raise GateSafetyViolation('OUTSIDE_RUN_ROOT_WAS_ALLOWED')
    result = subprocess.run([sys.executable, '-B', '-c', 'pass'], cwd=cwd,
                            capture_output=True, timeout=10, check=True)
    return dict(root=str(run_root), root_length=len(str(run_root)), encode_cwd=str(cwd),
                encode_cwd_length=len(str(cwd)), returncode=result.returncode,
                approved_sqlite_roots=[str(p) for p in guard.roots],
                excluded_paths=[str(p) for p in excluded], excluded_paths_rejected=True)


def execute(mode, run_root, summary):
    passed, outcomes, safety, fixture_errors, expected_errors, browser_requests, encodes = (
        [] for _ in range(7))
    active = None
    current_test = None
    audit_active = True
    guard = SQLiteIsolationGuard([run_root])

    def unsafe(message):
        safety.append(message)
        if active:
            active.stop()
        raise GateSafetyViolation(message)

    def audit(event, args):
        if not audit_active:
            return
        if event in ('socket.connect', 'socket.bind'):
            address = args[1]
            if isinstance(address, tuple) and (
                    address[0] not in ('127.0.0.1', 'localhost', '::1') or address[1] == 8765):
                unsafe('NON_FIXTURE_NETWORK_OR_OPERATIONAL_PORT_FORBIDDEN')
        elif event == 'subprocess.Popen':
            command, cwd = args[1], args[2]
            if cwd and '-filter_complex' in command:
                path = Path(cwd).resolve()
                if not path.is_relative_to(run_root) or len(str(path)) >= 240:
                    unsafe('ENCODE_CWD_OUTSIDE_SHORT_RUN_ROOT')
                encodes.append(dict(cwd=str(path), length=len(str(path)), test=current_test))

    sys.addaudithook(audit)
    original_thread_hook = threading.excepthook
    original_handle_error = socketserver.BaseServer.handle_error

    def thread_hook(args):
        fixture_errors.append(dict(kind='thread', test=current_test,
            trace=''.join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))))
        if isinstance(args.exc_value, (SQLiteIsolationViolation, GateSafetyViolation)):
            safety.append('THREAD_ISOLATION_VIOLATION')
            if active:
                active.stop()
        original_thread_hook(args)

    def handle_error(server, request, address):
        error_type, error, _ = sys.exc_info()
        controlled = (
            str(current_test).removeprefix('tests.') ==
            'test_publisher_headline_fixture_cleanup.HeadlineFixtureCleanupTests.'
            'test_handler_failure_still_closes_sqlite_and_removes_directory'
            and type(server).__module__.removeprefix('tests.') == 'test_publisher_headline_fixture_cleanup'
            and type(server).__name__ == 'ObservedFixtureServer'
            and server.RequestHandlerClass.__module__.removeprefix('tests.') == 'test_publisher_headline_fixture_cleanup'
            and server.RequestHandlerClass.__name__ == 'HoldingSQLiteHandler'
            and getattr(server, 'fail_request', False) is True
            and error_type is ValueError and str(error) == 'Fixture handler failure')
        (expected_errors if controlled else fixture_errors).append(
            dict(kind='server', test=current_test, trace=traceback.format_exc()))
        if isinstance(error, (SQLiteIsolationViolation, GateSafetyViolation)):
            safety.append('SERVER_ISOLATION_VIOLATION')
            if active:
                active.stop()
        original_handle_error(server, request, address)

    def check_safety(test, err):
        message, name = str(err[1]), test.id()
        security_test = any(module in name for module in (
            'test_publisher_security_hardening.', 'test_publisher_trusted_persistence.',
            'test_publisher_media_history_safety.', 'test_publisher_p0_integration.',
            'test_publisher_headline_p0_integration.', 'test_publisher_local_preparation.',
            'test_publisher_batch_consumption.', 'test_publisher_persistence.'))
        negative = any(word in name for word in (
            'reject', 'block', 'missing', 'legacy', 'ambiguous', 'unknown',
            'unreconciled', 'without', 'no_delete', 'no_blind'))
        bypass = security_test and negative and any(text in message for text in (
            'not raised', 'is not false', "'PERSISTED' != 'UNCONFIRMED'"))
        if (isinstance(err[1], (SQLiteIsolationViolation, GateSafetyViolation))
                or 'REMOTE_ACTION_FORBIDDEN' in message or bypass):
            safety.append('ISOLATION_OR_SECURITY_ASSERTION_FAILURE:' + name)
            if active:
                active.stop()

    class GateResult(unittest.TextTestResult):
        def startTest(self, test):
            nonlocal current_test
            current_test = test.id()
            super().startTest(test)
            print('GATE_PROGRESS=' + json.dumps(dict(mode=mode, started=self.testsRun,
                                                    test=test.id())), flush=True)

        def addSuccess(self, test):
            super().addSuccess(test)
            passed.append(test.id())
            outcomes.append(dict(test=test.id(), outcome='passed'))

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            outcomes.append(dict(test=test.id(), outcome='skipped', reason=reason))

        def addFailure(self, test, err):
            super().addFailure(test, err)
            check_safety(test, err)

        def addError(self, test, err):
            super().addError(test, err)
            check_safety(test, err)

        def addSubTest(self, test, subtest, err):
            super().addSubTest(test, subtest, err)
            if err:
                check_safety(test, err)

    try:
        with ExitStack() as stack:
            stack.enter_context(guard.installed())
            stack.enter_context(patch.object(threading, 'excepthook', thread_hook))
            stack.enter_context(patch.object(socketserver.BaseServer, 'handle_error', handle_error))
            summary['probe'] = process_probe(run_root, guard)
            if mode == 'probe':
                summary.update(passed_gate=True, discovered=0, run=0, passed=0, failed=0, errors=0, skipped=[])
                return
            from playwright.sync_api import BrowserType
            launch_context = BrowserType.launch_persistent_context

            def isolated_context(browser_type, user_data_dir, **kwargs):
                profile = Path(user_data_dir).resolve()
                if profile == run_root or not profile.is_relative_to(run_root):
                    unsafe('BROWSER_PROFILE_OUTSIDE_RUN_ROOT')
                context = launch_context(browser_type, user_data_dir, **kwargs)

                def local_route(route):
                    url = urlsplit(route.request.url)
                    if url.scheme in ('about', 'data', 'blob') or (
                            url.hostname in ('127.0.0.1', 'localhost', '::1') and url.port != 8765):
                        route.continue_()
                    else:
                        browser_requests.append(route.request.url.split('?')[0])
                        route.abort()
                        safety.append('NONLOCAL_BROWSER_REQUEST_BLOCKED')
                        if active:
                            active.stop()
                context.route('**/*', local_route)
                return context

            stack.enter_context(patch.object(BrowserType, 'launch_persistent_context', isolated_context))
            loader = unittest.defaultTestLoader
            if mode == 'full':
                suite = loader.discover(str(ROOT / 'tests'), pattern='test_publisher*.py')
            else:
                suite = loader.loadTestsFromNames([SMOKE] if mode == 'smoke' else AFFECTED)
            ids = [test.id() for test in flatten(suite)]
            summary.update(discovered=len(ids), distinct=len(set(ids)), discovered_ids=ids)
            print('GATE_DISCOVERED=' + json.dumps(dict(mode=mode, count=len(ids))), flush=True)
            runner = unittest.TextTestRunner(verbosity=2, resultclass=GateResult, failfast=False)
            active = runner._makeResult()
            runner._makeResult = lambda: active
            result = runner.run(suite)
            summary.update(run=result.testsRun, passed=len(passed), passed_test_ids=passed,
                outcomes=outcomes, skipped=[dict(test=t.id(), reason=r) for t, r in result.skipped],
                failed=len(result.failures), errors=len(result.errors),
                failures=[dict(test=t.id(), trace=s) for t, s in result.failures],
                error_details=[dict(test=t.id(), trace=s) for t, s in result.errors],
                complete=result.testsRun == len(ids),
                passed_gate=result.wasSuccessful() and result.testsRun == len(ids))
    finally:
        audit_active = False
        summary.update(sqlite_connections=guard.accepted_connections,
            sqlite_blocked=guard.blocked_connections, safety_violations=safety,
            unexpected_fixture_errors=fixture_errors, expected_fixture_errors=expected_errors,
            blocked_browser_requests=browser_requests, encode_cwds=encodes)
        summary['passed_gate'] = (summary.get('passed_gate', False) and not safety
                                  and not fixture_errors and not guard.blocked_connections)


def main():
    mode = sys.argv[1] if len(sys.argv) == 2 else ''
    if mode not in ('probe', 'smoke', 'affected', 'full'):
        raise SystemExit('Expected probe, smoke, affected or full')
    QA.mkdir(parents=True, exist_ok=True)
    summary = dict(mode=mode, failfast=False)
    started = time.monotonic()
    old_tempdir = tempfile.tempdir
    # No drive-root folders, shared TEMP allowlists or global Windows settings.
    base = Path(os.environ['LOCALAPPDATA']) / 'Temp'
    base = base.resolve(strict=True)
    owned = tempfile.TemporaryDirectory(prefix='pg-', dir=base)
    run_root = Path(owned.name).resolve(strict=True)
    assert run_root.parent == base and len(str(run_root)) < 90
    with (QA / f'{mode}.log').open('w', encoding='utf-8', buffering=1) as log:
        with redirect_stdout(log), redirect_stderr(log):
            try:
                tempfile.tempdir = str(run_root)
                with patch.dict(os.environ, TEMP=str(run_root), TMP=str(run_root),
                        PUBLISHER_BROWSER_PROFILE_ROOT=str(run_root / 'profiles'),
                        PUBLISHER_QUEUE_OPERATIONAL_SMOKE='0',
                        PUBLISHER_THUMBNAIL_OPERATIONAL_SMOKE='0',
                        RUN_PUBLISHER_BATCH_CONSUMPTION_BROWSER='1'):
                    execute(mode, run_root, summary)
            except BaseException:
                summary.update(passed_gate=False, fatal_error=traceback.format_exc())
                traceback.print_exc()
            finally:
                tempfile.tempdir = old_tempdir
                try:
                    if run_root.parent != base or not run_root.name.startswith('pg-'):
                        raise GateSafetyViolation('UNOWNED_TEMP_CLEANUP_FORBIDDEN')
                    owned.cleanup()
                    summary['temp_cleanup_passed'] = not run_root.exists()
                except Exception:
                    summary.update(temp_cleanup_passed=False, cleanup_error=traceback.format_exc())
                summary['passed_gate'] = (summary.get('passed_gate', False)
                                          and summary['temp_cleanup_passed'])
                summary.update(temp_root=str(run_root), elapsed_seconds=round(time.monotonic()-started, 3))
                (QA / f'{mode}-result.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
                print('GATE_SUMMARY=' + json.dumps({k: v for k, v in summary.items() if k not in (
                    'discovered_ids', 'passed_test_ids', 'outcomes', 'failures', 'error_details', 'encode_cwds')}), flush=True)
    print(json.dumps({k: summary.get(k) for k in ('mode', 'discovered', 'run', 'passed',
          'failed', 'errors', 'passed_gate', 'temp_cleanup_passed', 'elapsed_seconds')}))
    return 0 if summary['passed_gate'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
