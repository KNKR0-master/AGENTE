# Cecilia Drive deletion diagnosis

## Estado atual

Este arquivo registra o diagnostico historico que identificou a limitacao da
service account. A integracao operacional atual foi migrada para o OAuth de
usuario da Cecilia e o cleanup permanente de midia foi validado separadamente.
Nao usar os valores antigos de `ownedByMe`/`canDelete` da service account para
descrever o provider atual.

Metadata-only inspection on 2026-09-12. No consent, deletion, trash,
download, TikTok action, or lifecycle mutation was performed.

## Identity and scope

- Configured Drive owner: ceciliafigueiredo.shop@gmail.com
- Actual API principal, confirmed by about.user:
  tiktok-drive-inventory@banded-chimera-505001-g3.iam.gserviceaccount.com
- Credentials: google.oauth2.service_account.Credentials
- Publisher scope: https://www.googleapis.com/auth/drive
- The inventory default drive.readonly is overridden by the Publisher.
- This is not a user OAuth session authenticated as Cecilia.

## Draft 1

- File: 1A72hhOtiz0I6DZOz4BIpAjnjKkYCZ4uX
- Owner: ceciliafigueiredo.shop@gmail.com
- ownedByMe=false (relative to the service account)
- shared=true; driveId absent (not a Shared Drive)
- canDelete=false; canTrash=false; canEdit=false; canDownload=true
- FLOW root, active CECI FIGUEIREDO - PERFIL 1 folder, and AGAPIS KIT 3
  parent have the same owner and capabilities.
- permissions.list failed with HttpError for file and root. Exact role was
  not observable; do not claim a specific reader/commenter role.

## Scope versus ownership

The existing drive scope permits the required API methods. Increasing scopes
will not make the service account the owner of Cecilia's My Drive files.
files.delete requires ownership in My Drive (or organizer authority for Shared
Drive files). The provider's canDelete guard is appropriate, not too strict.
Changing delete to trash is neither authorized nor a remedy here (canTrash=false).

ROOT_CAUSE=FILE_NOT_OWNED_BY_API_PRINCIPAL / INSUFFICIENT_ROLE
REAUTH_REQUIRED_FOR_SCOPE_EXPANSION=false
PROVIDER_CHANGE_REQUIRED_FOR_OWNER_AUTHENTICATION=true

A future, separately authorized change can adapt the existing provider to user
OAuth as Cecilia, preserving the same catalog and lifecycle. This would be an
authentication-model change, not a scope repair or a parallel media integration.
No such change or consent was initiated. Existing unrelated integrations were
not modified.

## Future files

Bounded breadth-first sample: 20 folders inspected, 41 eligible stock videos.
All 41 are owned by Cecilia; all have canDelete=false and canTrash=false;
none belongs to a Shared Drive. Draft 1 is consistent with this sample.
Future files with the same ownership/access model are expected to be affected;
permissions of files not yet created cannot be guaranteed.

## Preserved state

READY_FOR_REVIEW + PERSISTED; media CONSUMED_PENDING_DELETE; TEMP present.

## Official references

- https://developers.google.com/workspace/drive/api/reference/rest/v3/files/delete
- https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- https://developers.google.com/workspace/drive/api/guides/delete
