"""Metadata-only inspection. No consent, download, deletion or DB mutation."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from src.config import load_env_file
from src.store import connect
from src.publisher.media import media_provider
from src.publisher.service import get_row
from src.drive_inventory import is_stock_video_name

FIELDS='id,name,mimeType,parents,ownedByMe,owners(emailAddress),driveId,shared,trashed,capabilities(canDelete,canTrash,canDownload,canEdit,canShare)'

if __name__=='__main__':
    load_env_file(ROOT/'.env')
    conn=connect(ROOT/'data/agent.db')
    try:
        draft=get_row(conn,'publisher_drafts',1)
        asset=get_row(conn,'publisher_media_assets',draft['media_asset_id'])
        provider=media_provider(asset)
        service=provider.service
        credentials=service._http.credentials
        result={'configured_account':provider.config.account_email,
          'credential_type':type(credentials).__module__+'.'+type(credentials).__name__,
          'service_account_email':getattr(credentials,'service_account_email',None),
          'scopes':list(credentials.scopes or []),
          'api_identity':service.about().get(fields='user(emailAddress,displayName,me)').execute()}
        def meta(file_id):
            return service.files().get(fileId=file_id,supportsAllDrives=True,fields=FIELDS).execute()
        result['target']=meta(asset['remote_file_id'])
        result['root']=meta(provider.config.root_folder_id)
        result['active_root']=meta(provider.root_id)
        result['parents']=[meta(i) for i in result['target'].get('parents',[])]
        for name,item in [('target',result['target']),('root',result['root'])]:
            try:
                result[name+'_permissions']=service.permissions().list(fileId=item['id'],supportsAllDrives=True,
                  fields='permissions(type,role,emailAddress,permissionDetails(inherited,inheritedFrom,role,permissionType))').execute()
            except Exception as exc:
                result[name+'_permissions_error']=type(exc).__name__
        # Bounded breadth-first sample of the actual active tree, not an unrelated account.
        pending=[provider.root_id];visited=set();sample=[]
        while pending and len(visited)<20 and len(sample)<100:
            folder=pending.pop(0)
            if folder in visited:continue
            visited.add(folder)
            response=service.files().list(q=f"'{folder}' in parents and trashed=false",pageSize=100,
                fields='nextPageToken,files('+FIELDS+')',supportsAllDrives=True,includeItemsFromAllDrives=True).execute()
            for item in response.get('files',[]):
                if item['mimeType']=='application/vnd.google-apps.folder':
                    if item['id'] not in provider.config.ignored_folder_ids and item['name'].lower() not in provider.config.administrative_names:
                        pending.append(item['id'])
                elif item['mimeType']=='video/mp4' and is_stock_video_name(item['name']):
                    sample.append(item)
            if response.get('nextPageToken'):
                result['sample_pagination_truncated']=True
        result['sample']={'folders_inspected':len(visited),'videos':len(sample),
          'owners':dict(Counter(o.get('emailAddress','UNKNOWN') for i in sample for o in i.get('owners',[]))),
          'can_delete':dict(Counter(str(i.get('capabilities',{}).get('canDelete')) for i in sample)),
          'can_trash':dict(Counter(str(i.get('capabilities',{}).get('canTrash')) for i in sample)),
          'shared_drive_count':sum(bool(i.get('driveId')) for i in sample),
          'examples':sample[:5], 'bounded_sample':True}
        result['preserved_state']={'draft':draft['status'],'persistence':draft['persistence_status'],
          'media':asset['status'],'temp_exists':Path(draft['temp_path']).is_file()}
        print(json.dumps(result,ensure_ascii=False))
    finally:conn.close()
