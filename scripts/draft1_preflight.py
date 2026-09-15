import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import load_env_file
from src.store import connect
from src.publisher.service import get_row, draft_retry_safe, transition, log
from src.publisher.media import media_provider, verify_identity

if __name__=='__main__':
    load_env_file(ROOT/'.env')
    conn=connect(ROOT/'data/agent.db')
    try:
        draft=get_row(conn,'publisher_drafts',1)
        assert draft['account_id']==1 and draft['product_id'] and draft['media_asset_id']==207
        if not draft['caption'].strip(): raise ValueError('CAPTION_MISSING')
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            assert draft_retry_safe(conn,1)
            transition(conn,1,'DRAFT',explicit_reset=True)
            log(conn,1,'AUTHORIZED_RESET','DRAFT','Retry/reset autorizado pelo usuario apos validacao de ausencia de side effects; somente Draft 1')
        asset=get_row(conn,'publisher_media_assets',207)
        provider=media_provider(asset)
        metadata=provider.get_metadata(asset['remote_file_id'])
        verify_identity(asset,metadata)
        with conn:
            assert not conn.execute('SELECT 1 FROM publisher_drafts WHERE media_asset_id=207 AND id<>1').fetchone()
            assert asset['status'] in {'RESERVED','AVAILABLE'} and not asset.get('consumed_by_draft_id') and not asset.get('remote_deleted_at')
            conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=207")
            log(conn,1,'TEST_MEDIA_VALIDATED','AVAILABLE','Arquivo remoto validado; reserva anterior liberada localmente para o teste unico')
        print(json.dumps({'draft_id':1,'reset':True,'media_status':'AVAILABLE','filename':asset['filename'],
            'remote_file_id':asset['remote_file_id'],'size':asset['size_bytes'],'checksum':asset['checksum']}))
    finally:conn.close()
