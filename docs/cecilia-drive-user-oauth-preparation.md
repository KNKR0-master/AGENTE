# Cecilia user OAuth preparation

## Estado atual

Esta preparacao foi concluida e o provider ativo da Cecilia usa `user_oauth`.
O principal validado e `ceciliafigueiredo.shop@gmail.com`, com o scope unico
`https://www.googleapis.com/auth/drive`. A credencial de usuario fica em
`data/credentials/cecilia-drive.dpapi`, protegida por Windows DPAPI e excluida
do Git. Lista, metadata, download, capabilities, delete e confirmacao pos-delete
usam essa mesma identidade; nao ha fallback silencioso para service account.

As secoes abaixo preservam o registro historico da preparacao e das etapas
humanas de consentimento. Nenhum segredo e documentado aqui.

## Discovery

No reusable user OAuth credential or installed/web OAuth client was found in
the project's Google modules/configuration, its configured .config/agente
credential directory, or the inspected Downloads client/credential files.
The existing google-drive-ceci.json is a service-account key, not a desktop
OAuth client. Existing clients in Google Cloud were not inventoried and must
be checked before creating anything. No secrets were printed.

## Prepared configuration

config/cecilia_drive_auth.json selects user_oauth for
ceciliafigueiredo.shop@gmail.com only. The sole scope is
https://www.googleapis.com/auth/drive, retained to support pre-existing videos.
No Google integrations for other configured accounts are changed.

GoogleApiDriveInventoryProvider.from_config and GoogleDriveMediaProvider use
the same validated service instance/credential selection. Listing, metadata,
thumbnail metadata, downloads, capabilities, deletion and post-delete checks
do not switch between a service account and a user identity.

The provider never initiates consent. Missing credentials fail closed with
GOOGLE_USER_CONSENT_REQUIRED; no service-account fallback exists in user mode.
No server restart or live Google API call was performed in this preparation.

## Credential storage

data/credentials/cecilia-drive.dpapi is excluded from Git. It stores the Google
authorized-user JSON encrypted using the project's existing Windows DPAPI
TokenCipher. Writes are atomic, and temporary files contain ciphertext only.
The credential is tied to the Windows user/machine; do not commit or distribute
it. No actual user token has been stored yet.

Expired access tokens are refreshed from the encrypted refresh token. The
official Google credential transport also handles expiry during later requests.
Only the Google token endpoint is accepted. Errors omit credential contents.
The Drive about.user response must match the expected email and me=true before
the service is returned or newly authorized credentials are persisted.
Missing/unreadable identity or another account yields GOOGLE_ACCOUNT_MISMATCH.

## Human steps, requiring future authorization

1. Check Google Cloud project banded-chimera-505001-g3 for an existing suitable
   Desktop OAuth client. Supply its downloaded client configuration securely;
   reuse it if available. A service-account numeric client ID is not sufficient.
2. Only if none exists, create a Desktop OAuth client and configure the consent
   screen/test-user access for ceciliafigueiredo.shop@gmail.com as necessary.
3. Explicitly authorize opening consent. Sign in as Cecilia and grant Drive
   access with offline access so a refresh token is returned.
4. Validate about.user, encrypt the credential, and make ONLY a metadata check
   of 1A72hhOtiz0I6DZOz4BIpAjnjKkYCZ4uX: ownedByMe, canDelete and canTrash.
5. Stop. Cleanup requires a separate later authorization, even if capabilities
   are correct. The current preparation contains no consent launcher.

## Preserved lifecycle

Draft 1: READY_FOR_REVIEW + PERSISTED.
Media 207: CONSUMED_PENDING_DELETE. Remote IDs unchanged. TEMP preserved.
No delete, trash, download, reupload, publication or scheduling was performed.
