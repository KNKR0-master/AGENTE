"""Explicit targeted gate with SQLite/network isolation; never full discovery."""
import hashlib
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
QA = ROOT/'data/publisher/qa/local-preparation-targeted'
MODULES = [
    'test_publisher_local_preparation', 'test_publisher_local_preparation_browser',
    'test_publisher_headline_renderer', 'test_publisher_headline_timing',
    'test_publisher_headline_p0_integration', 'test_publisher_security_hardening',
    'test_publisher_trusted_persistence', 'test_publisher_batch_consumption',
    'test_publisher_headline_opt_in', 'test_publisher_remote',
]


def git(*args, root=ROOT):
    return subprocess.check_output(['git','-C',str(root),*args],text=True,encoding='utf-8').strip()


def protected_snapshot():
    preflight=json.loads((ROOT/'data/publisher/qa/final-full-integration/preflight.json').read_text(encoding='utf-8-sig'))
    result={}
    for name,record in preflight['protected'].items():
        path=Path(record['path'])
        result[name]=dict(head=git('rev-parse','HEAD',root=path),
                         branch=git('branch','--show-current',root=path),
                         status=git('status','--porcelain=v1',root=path),
                         hashes={p:hashlib.sha256((path/p).read_bytes()).hexdigest() for p in record['hashes']})
    return dict(worktrees=result,main=git('rev-parse','main'),head=git('rev-parse','HEAD'))


def executable_hashes():
    files=set(git('ls-files').splitlines())|set(git('ls-files','--others','--exclude-standard').splitlines())
    return {p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in sorted(files)
            if Path(p).suffix in {'.py','.js','.html','.css','.json'} and (ROOT/p).is_file()}


def main():
    QA.mkdir(parents=True,exist_ok=True)
    before=protected_snapshot();sources=executable_hashes()
    audit=dict(sqlite_allowed=0,sqlite_blocked=[],network_blocked=[])
    allowed=[(ROOT/'data/publisher/qa').resolve(),Path(tempfile.gettempdir()).resolve()]
    def hook(event,args):
        if event=='sqlite3.connect':
            value=str(args[0])
            if value==':memory:' or value.startswith('file:') and 'mode=memory' in value:
                audit['sqlite_allowed']+=1;return
            if value.startswith('file:'):
                value=unquote(urlsplit(value).path)
                if len(value)>2 and value[0]=='/' and value[2]==':':value=value[1:]
            path=Path(value).resolve()
            if not any(path.is_relative_to(root) for root in allowed):
                audit['sqlite_blocked'].append('OUTSIDE_ISOLATED_ROOT')
                raise AssertionError('OPERATIONAL_SQLITE_FORBIDDEN')
            audit['sqlite_allowed']+=1
        elif event=='socket.connect':
            address=args[1]
            if isinstance(address,tuple) and (address[0] not in {'127.0.0.1','::1','localhost'} or address[1]==8765):
                audit['network_blocked'].append('NON_FIXTURE_NETWORK')
                raise AssertionError('NON_FIXTURE_NETWORK_FORBIDDEN')
    sys.addaudithook(hook)
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName('tests.'+name) for name in MODULES)
    selected=suite.countTestCases()
    started=time.monotonic()
    with (QA/'targeted-tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    after=protected_snapshot();final_sources=executable_hashes()
    summary=dict(modules=MODULES,selected=selected,executed=result.testsRun,
        passed=result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
        failures=[name.id() for name,_ in result.failures],errors=[name.id() for name,_ in result.errors],
        skipped=result.skipped,seconds=round(time.monotonic()-started,3),isolation=audit,
        protected_preserved=before==after,executable_hashes_preserved=sources==final_sources,
        source_hashes=sources,full_suite_run=False,operational_db_writes=0,
        tiktok_remote_actions=0,drive_remote_writes=0)
    summary['passed_gate']=(result.wasSuccessful() and not result.skipped and before==after
                           and sources==final_sources and not audit['sqlite_blocked'] and not audit['network_blocked'])
    (QA/'targeted-result.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k not in {'source_hashes','modules'}},ensure_ascii=True))
    return 0 if summary['passed_gate'] else 1


if __name__=='__main__':
    raise SystemExit(main())
