"""Record the observed single-draft result without consuming media."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.store import connect
from src.publisher.service import get_row, log

if __name__ == '__main__':
    conn = connect(ROOT / 'data/agent.db')
    try:
        with conn:
            if not conn.execute("SELECT 1 FROM publisher_execution_logs WHERE draft_id=1 AND step='TEST_FINAL_RESULT'").fetchone():
                log(conn, 1, 'TEST_FINAL_RESULT', 'READY_FOR_REVIEW_PERSISTED', json.dumps({
                    'url': 'https://www.tiktok.com/tiktokstudio/content',
                    'evidence': 'draft1-functional-review.png',
                    'caption': 'BODY SPLASH TESTE', 'duration_seconds': 23,
                    'observed_updated': '11 de set de 2026, 20:56',
                    'upload_marker': 'Enviado (8.02MB)',
                    'persistence': 'Listed under Rascunhos after automatic navigation away from upload',
                    'remote_deleted': False, 'media_consumed': False,
                }))
        account = get_row(conn, 'publisher_accounts', 1)
        asset = get_row(conn, 'publisher_media_assets', 207)
        draft = get_row(conn, 'publisher_drafts', 1)
        print(json.dumps({
            'auth_status': account['auth_status'],
            'session_verified': account['session_verified'],
            'account_identity': account['account_identity'],
            'draft_database_status': draft['status'],
            'media_status': asset['status'],
            'remote_deleted_at': asset.get('remote_deleted_at'),
            'consumed_by_draft_id': asset.get('consumed_by_draft_id'),
            'temp_exists': (ROOT / 'data/publisher/temp/controlled_draft_1' / asset['filename']).is_file(),
            'profile_ownership': [dict(row) for row in conn.execute('SELECT state,owner_pid,mode FROM publisher_profile_ownership')],
        }))
    finally:
        conn.close()
