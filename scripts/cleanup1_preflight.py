"""Read-only preconditions for the explicitly authorized Draft 1 cleanup."""
import hashlib
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import load_env_file
from src.store import connect
from src.publisher.service import get_row, assert_upload_allowed
from src.publisher.media import media_provider, verify_identity
from src.publisher.manual_profile import profile_processes, process_identity

if __name__=='__main__':
    load_env_file(ROOT/'.env')
    conn=connect(ROOT/'data/agent.db')
    try:
        draft=get_row(conn,'publisher_drafts',1)
        asset=get_row(conn,'publisher_media_assets',draft['media_asset_id'])
        account=get_row(conn,'publisher_accounts',draft['account_id'])
        assert draft['account_id']==1 and asset['id']==207
        assert asset['remote_file_id']=='1A72hhOtiz0I6DZOz4BIpAjnjKkYCZ4uX'
        assert asset['filename']=='AGAPIS_KIT3_G2C2A2_2808_1787957225576.mp4'
        assert draft['status']=='READY_FOR_REVIEW' and draft['persistence_status']=='PERSISTED'
        assert all(draft[k] for k in ('upload_confirmed_at','caption_applied_at','tiktok_draft_persisted_at'))
        assert asset['status']=='RESERVED' and not asset['remote_deleted_at']
        assert account['session_verified'] and account['account_identity']=='ACCOUNT_CONFIRMED'
        assert not conn.execute("SELECT 1 FROM publisher_execution_items WHERE status IN ('QUEUED','PROCESSING')").fetchone()
        profile=(ROOT/account['browser_profile']).resolve()
        locks=[dict(r) for r in conn.execute('SELECT * FROM publisher_profile_ownership')]
        lock=next(r for r in locks if Path(r['profile']).resolve()==profile)
        assert lock['state']=='PLAYWRIGHT_ACTIVE'
        assert process_identity(lock['owner_pid'])==lock['owner_process_identity']
        processes=profile_processes(profile)
        assert processes
        try:assert_upload_allowed(conn,1)
        except ValueError as exc:
            assert str(exc)=='DRAFT_ALREADY_PERSISTED'
        else:raise AssertionError('REUPLOAD_NOT_BLOCKED')
        temp=Path(draft['temp_path']).resolve()
        assert temp.is_relative_to((ROOT/'data/publisher/temp').resolve()) and temp.is_file()
        assert temp.stat().st_size==asset['size_bytes']
        assert hashlib.md5(temp.read_bytes()).hexdigest()==asset['checksum']
        provider=media_provider(asset)
        exists=provider.exists(asset['remote_file_id'])
        assert exists, 'REMOTE_ABSENT_REQUIRES_SEPARATE_RECONCILIATION'
        metadata=provider.get_metadata(asset['remote_file_id'])
        verify_identity(asset,metadata)
        print(json.dumps({'preconditions':True,'profile_mutex_state':'HEALTHY',
          'server_pid':lock['owner_pid'],'idempotency':'DRAFT_ALREADY_PERSISTED',
          'remote_existed_before':exists,'can_delete':metadata.get('capabilities',{}).get('canDelete'),
          'temp_existed_before':True,'size':temp.stat().st_size,
          'max_log_id':conn.execute('SELECT MAX(id) FROM publisher_execution_logs').fetchone()[0]}))
    finally:conn.close()
