"""One local read-only browser snapshot, then authorized account metadata update."""
import json
import sqlite3
import sys
from pathlib import Path
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.publisher.auth import evaluate_studio_snapshot, persist_auth

if __name__=='__main__':
    with urlopen('http://127.0.0.1:8765/api/publisher/accounts/1/auth-diagnostics',timeout=15) as response:
        snapshot=json.load(response)
    result=evaluate_studio_snapshot(snapshot,'cecilia.figueired65')
    if snapshot['context_count']!=1 or snapshot['page_count']!=1:
        raise RuntimeError('Unexpected topology; nothing persisted')
    if result['auth_result']!='AUTHENTICATED' or result['account_identity']!='ACCOUNT_CONFIRMED':
        raise RuntimeError('Positive session and identity required; nothing persisted')
    conn=sqlite3.connect(ROOT/'data/agent.db')
    try:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT name,browser_profile FROM publisher_accounts WHERE id=1').fetchone()
            assert row==('CECILIA FIGUEIREDO','data/publisher/browser_profiles/a9a3acebc275495a8271b74e73faeab6')
            columns={r[1] for r in conn.execute('PRAGMA table_info(publisher_accounts)')}
            if 'tiktok_username' not in columns:
                conn.execute("ALTER TABLE publisher_accounts ADD COLUMN tiktok_username TEXT NOT NULL DEFAULT ''")
            if 'account_identity' not in columns:
                conn.execute("ALTER TABLE publisher_accounts ADD COLUMN account_identity TEXT NOT NULL DEFAULT 'ACCOUNT_IDENTITY_UNKNOWN'")
            for column in ('identity_confirmed_profile', 'identity_confirmed_handle'):
                if column not in columns:
                    conn.execute(f"ALTER TABLE publisher_accounts ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            conn.execute("UPDATE publisher_accounts SET tiktok_username=?,account_identity=? WHERE id=1",
                         ('cecilia.figueired65','ACCOUNT_CONFIRMED'))
            conn.execute('UPDATE publisher_accounts SET identity_confirmed_profile=browser_profile,identity_confirmed_handle=tiktok_username WHERE id=1')
            persist_auth(conn,1,'AUTHENTICATED','Studio, cookie, metricas, publicacoes recentes e identidade confirmados por snapshot read-only')
        stored=conn.execute('SELECT tiktok_username,session_verified,session_verified_at,auth_status,account_identity FROM publisher_accounts WHERE id=1').fetchone()
        print(json.dumps({'verification':result,'stored':stored,'snapshot_at':snapshot['captured_at'], 'final_url':snapshot['pages'][0]['url']},ensure_ascii=True))
    finally:
        conn.close()
