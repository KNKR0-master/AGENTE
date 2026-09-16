import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.google_drive_auth import (CECILIA, DRIVE_SCOPE, DriveAuthError,
    DriveCredentialStore, save_authorized_user, user_drive_service, validate_account)
from src.drive_inventory import DriveInventoryConfig, GoogleApiDriveInventoryProvider


class UserOAuthTests(unittest.TestCase):
    def setUp(self):
        self.store=MagicMock()
        self.info={'refresh_token':'fixture-refresh','client_id':'fixture-client',
                   'client_secret':'fixture-secret','scopes':[DRIVE_SCOPE]}
        self.store.load.return_value=self.info
        self.creds=MagicMock();self.creds.valid=True
        self.creds.granted_scopes=None
        self.creds.to_json.return_value=json.dumps(self.info)
        self.service=MagicMock()
        self.service.about.return_value.get.return_value.execute.return_value={'user':{'emailAddress':CECILIA,'me':True}}
        self.builder=MagicMock(return_value=self.service)

    def load(self):
        return user_drive_service('unused',CECILIA,store=self.store,
            credential_factory=MagicMock(return_value=self.creds),builder=self.builder,
            request_factory=MagicMock(return_value='mock-request'))

    def test_existing_credential_reused_and_identity_checked(self):
        self.assertIs(self.load(),self.service)
        self.creds.refresh.assert_not_called()
        self.service.about.return_value.get.assert_called_with(fields='user(emailAddress,me)')
        self.store.save.assert_called_once()

    def test_expired_token_refreshes_without_browser(self):
        self.creds.valid=False
        self.load()
        self.creds.refresh.assert_called_once_with('mock-request')

    def test_refresh_failure_sanitized(self):
        self.creds.valid=False
        self.creds.refresh.side_effect=RuntimeError('fixture-secret fixture-refresh')
        with self.assertRaisesRegex(DriveAuthError,'^GOOGLE_USER_CREDENTIAL_REFRESH_FAILED$'):
            self.load()
        self.store.save.assert_not_called()

    def test_wrong_identity_does_not_save_or_enable(self):
        self.service.about.return_value.get.return_value.execute.return_value={'user':{'emailAddress':'other@example.com','me':True}}
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_ACCOUNT_MISMATCH'):
            self.load()
        self.store.save.assert_not_called()

    def test_unknown_identity_fails_closed(self):
        self.service.about.return_value.get.return_value.execute.side_effect=RuntimeError('private')
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_ACCOUNT_MISMATCH'):
            self.load()

    def test_scope_not_silently_expanded(self):
        self.info['scopes']=['https://www.googleapis.com/auth/drive.file']
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_OAUTH_SCOPE_INSUFFICIENT'):
            self.load()
        self.builder.assert_not_called()

    def test_partial_grant_rejected(self):
        self.creds.granted_scopes=['https://www.googleapis.com/auth/drive.readonly']
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_OAUTH_SCOPE_INSUFFICIENT'):
            self.load()
        self.store.save.assert_not_called()

    def test_missing_refresh_requires_consent(self):
        self.info.pop('refresh_token')
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_USER_CONSENT_REQUIRED'):
            self.load()

    def test_foreign_endpoint_rejected(self):
        self.info['token_uri']='https://example.com/token'
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_CREDENTIAL_ENDPOINT_INVALID'):
            self.load()

    def test_new_consent_not_saved_until_account_confirmed(self):
        self.service.about.return_value.get.return_value.execute.return_value={}
        with self.assertRaisesRegex(DriveAuthError,'GOOGLE_ACCOUNT_MISMATCH'):
            save_authorized_user(self.creds,'unused',builder=self.builder,store=self.store)
        self.store.save.assert_not_called()

    def test_dpapi_store_uses_cipher_no_plaintext(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credential.dpapi'
            cipher=MagicMock();cipher.encrypt.return_value='encrypted-fixture'
            cipher.decrypt.return_value=json.dumps(self.info)
            store=DriveCredentialStore(path,cipher)
            store.save(self.info)
            self.assertEqual(path.read_text(),'encrypted-fixture')
            self.assertEqual(store.load(),self.info)
            self.assertFalse(list(path.parent.glob('.credential-*')))

    def test_dpapi_diagnostic_never_returns_secret_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credential.dpapi'
            cipher=MagicMock();cipher.decrypt.return_value=json.dumps(self.info)
            path.write_text('encrypted-fixture', encoding='utf-8')
            diagnostic=DriveCredentialStore(path,cipher).diagnostic()
            self.assertTrue(diagnostic['dpapi_load_ok'])
            self.assertTrue(diagnostic['has_refresh_token'])
            self.assertNotIn('refresh_token', diagnostic)
            self.assertNotIn('client_secret', diagnostic)

    def test_missing_store_never_uses_service_account(self):
        with tempfile.TemporaryDirectory() as directory:
            config=DriveInventoryConfig('root',CECILIA,'old-service-key',auth_mode='user_oauth',
                expected_google_account=CECILIA,oauth_credential_store=str(Path(directory)/'missing.dpapi'))
            with patch('src.drive_inventory.GoogleApiDriveInventoryProvider.__init__') as legacy:
                with self.assertRaisesRegex(Exception,'GOOGLE_USER_CONSENT_REQUIRED'):
                    GoogleApiDriveInventoryProvider.from_config(config)
                legacy.assert_not_called()

    def test_inventory_and_publisher_share_service(self):
        config=DriveInventoryConfig('root',CECILIA,None,auth_mode='user_oauth',
            expected_google_account=CECILIA,oauth_credential_store='encrypted-store')
        with patch('src.google_drive_auth.user_drive_service',return_value=self.service) as load:
            provider=GoogleApiDriveInventoryProvider.from_config(config,scopes=(DRIVE_SCOPE,))
            self.assertIs(provider._service,self.service)
            load.assert_called_once_with('encrypted-store',CECILIA)

    def test_other_account_keeps_service_account_mode(self):
        with patch.dict('os.environ',{'GOOGLE_DRIVE_ACCOUNT_EMAIL':'old@example.com'},clear=True):
            config=DriveInventoryConfig.from_env()
            self.assertEqual(config.auth_mode,'service_account')
            self.assertIsNone(config.oauth_credential_store)

    def test_cecilia_config_selects_only_user_oauth(self):
        with patch.dict('os.environ',{'GOOGLE_DRIVE_ACCOUNT_EMAIL':CECILIA},clear=True):
            config=DriveInventoryConfig.from_env()
            self.assertEqual(config.auth_mode,'user_oauth')
            self.assertEqual(config.expected_google_account,CECILIA)


if __name__=='__main__':unittest.main()
