"""Explicit local reconciliation of an approved, evidenced historical probe."""
import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.store import connect, now_iso
from src.publisher.service import init_publisher, get_row, log


def consolidate(conn, root, draft_id):
    draft = get_row(conn, 'publisher_drafts', draft_id)
    if draft.get('persistence_status') == 'PERSISTED':
        return
    def event(step):
        row = conn.execute('SELECT * FROM publisher_execution_logs WHERE draft_id=? AND step=? ORDER BY id DESC LIMIT 1', (draft_id, step)).fetchone()
        if not row:
            raise ValueError('MISSING_CHECKPOINT_' + step)
        return dict(row)
    result = event('TEST_FINAL_RESULT')
    proof = json.loads(result['message'])
    if result['status'] != 'READY_FOR_REVIEW_PERSISTED' or proof['caption'] != draft['caption']:
        raise ValueError('UNCONFIRMED_PROBE')
    evidence = (root / 'data/publisher/evidence' / proof['evidence']).resolve()
    if not evidence.is_relative_to((root / 'data/publisher/evidence').resolve()) or not evidence.is_file():
        raise ValueError('EVIDENCE_MISSING')
    upload, caption, download = event('TEST_CAPTION'), event('CAPTION'), event('DOWNLOADED')
    temp = Path(json.loads(download['message'])['temp_path']).resolve()
    if not temp.is_relative_to((root / 'data/publisher/temp').resolve()) or not temp.is_file():
        raise ValueError('TEMP_MISSING')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute("SELECT 1 FROM publisher_execution_items WHERE draft_id=? AND status IN ('QUEUED','PROCESSING')", (draft_id,)).fetchone():
            raise ValueError('UPLOAD_IN_PROGRESS')
        conn.execute("""UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED',
          confirmed_at=?,upload_confirmed_at=?,caption_applied_at=?,tiktok_draft_persisted_at=?,
          tiktok_draft_url=?,temp_path=?,error_code=NULL,checklist_json=?,updated_at=? WHERE id=?""",
          (result['created_at'], upload['created_at'], caption['created_at'], result['created_at'],
           proof['url'], str(temp), json.dumps(dict.fromkeys(('file','upload','caption','settings','durable'), True)), now_iso(), draft_id))
        log(conn, draft_id, 'TIKTOK_DRAFT_CONFIRMED', 'OK', 'Consolidacao autorizada de evidencia historica; sem nova acao TikTok', evidence.relative_to(root))
        log(conn, draft_id, 'LOCAL_RECONCILIATION', 'READY_FOR_REVIEW_PERSISTED', 'Timestamps de observacao preservados; remoto e TEMP intactos')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--draft-id', type=int, required=True)
    args = parser.parse_args()
    conn = connect(ROOT / 'data/agent.db')
    try:
        backup = ROOT / 'data/publisher' / ('pre-persistence-' + now_iso().replace(':', '-') + '.db')
        with sqlite3.connect(backup) as target:
            conn.backup(target)
        init_publisher(conn)
        consolidate(conn, ROOT, args.draft_id)
        row = get_row(conn, 'publisher_drafts', args.draft_id)
        asset = get_row(conn, 'publisher_media_assets', row['media_asset_id'])
        print(json.dumps({key: row[key] for key in ('status','persistence_status','upload_confirmed_at','caption_applied_at','tiktok_draft_persisted_at','temp_path')}))
        print(json.dumps({'media_status':asset['status'],'remote_deleted_at':asset['remote_deleted_at'],'backup':str(backup)}))
    finally:
        conn.close()
