import unittest
import json
import tempfile
from pathlib import Path

from src.publisher.auth import classify_auth_layers, evaluate_studio_snapshot
from src.publisher.diagnostics import diagnostic_json, write_diagnostic_snapshot


def fixture(handle='cecilia.figueired65'):
    return {'session_cookie_present': True, 'pages': [{'is_auth_page': True,
        'url':'https://www.tiktok.com/tiktokstudio', 'dom_collected': True,
        'login_control_visible': False, 'studio_dom_inventory': [
            {'tag':'a','text':handle},
            {'tag':'button','text':'Métricas principais'},
            {'tag':'button','text':'Visualizações de vídeo 810,5 mil'},
            {'tag':'button','text':'Publicações recentes'}]}]}


class AuthLayerTests(unittest.TestCase):
    def test_layer_classification_is_independent_from_unicode_console(self):
        result = classify_auth_layers(
            'https://www.tiktok.com/tiktokstudio', True,
            login_ui_present=False, authenticated_dom_marker_present=True,
            dom_collected=True, expected_handle='cecilia.figueired65',
            observed_handles=['cecilia.figueired65'],
            auth_dom_markers=['Métricas principais', 'Visualizações de vídeo',
                              'Publicações recentes', 'Rascunhos',
                              'Todos os computadores', '✅'])
        self.assertEqual(result['auth_result'], 'AUTHENTICATED')
        self.assertEqual(result['account_identity'], 'ACCOUNT_CONFIRMED')
        encoded = diagnostic_json(result).encode('ascii')
        self.assertTrue(encoded)

    def test_unicode_snapshot_is_written_as_utf8_without_affecting_result(self):
        snapshot = fixture()
        snapshot['pages'][0]['studio_dom_inventory'].append({'tag': 'button', 'text': '✅ Prévia carregada'})
        result = evaluate_studio_snapshot(snapshot, 'cecilia.figueired65')
        with tempfile.TemporaryDirectory() as folder:
            target = write_diagnostic_snapshot(Path(folder) / 'snapshot.json', snapshot)
            self.assertEqual(target.read_text(encoding='utf-8'), json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
        self.assertEqual(result['auth_result'], 'AUTHENTICATED')

    def test_comment_author_is_not_account_identity(self):
        s=fixture();s['pages'][0]['studio_dom_inventory'].append({'tag':'a','text':'different.commenter'})
        self.assertEqual(evaluate_studio_snapshot(s,'cecilia.figueired65')['account_identity'],'ACCOUNT_CONFIRMED')
    def test_real_markers_confirm_auth_and_identity(self):
        r=evaluate_studio_snapshot(fixture(), ' CECILIA.FIGUEIRED65 ')
        self.assertEqual(r['auth_result'], 'AUTHENTICATED')
        self.assertEqual(r['account_identity'], 'ACCOUNT_CONFIRMED')

    def test_different_handle_not_accepted(self):
        r=evaluate_studio_snapshot(fixture(), 'cecilia.figueiredo65')
        self.assertEqual(r['auth_result'], 'AUTHENTICATED')
        self.assertEqual(r['account_identity'], 'ACCOUNT_MISMATCH')

    def test_bound_identity_survives_absent_handle_on_analytics(self):
        s=fixture();p=s['pages'][0]
        p['url']='https://www.tiktok.com/tiktokstudio/analytics/content'
        p['studio_dom_inventory']=[{'tag':'button','text':x} for x in ['Baixar dados','Últimos 7 dias','Ver dados']]+[
            {'test_id':x} for x in ['House','Folder','ChartRise']]
        r=evaluate_studio_snapshot(s,'cecilia.figueired65',profile='profile-a',confirmed_profile='profile-a',confirmed_handle='cecilia.figueired65')
        self.assertEqual(r['auth_result'],'AUTHENTICATED')
        self.assertEqual(r['account_identity'],'ACCOUNT_CONFIRMED')

    def test_authenticated_content_route_uses_studio_navigation_shell(self):
        s = fixture()
        page = s['pages'][0]
        page['url'] = 'https://www.tiktok.com/tiktokstudio/content'
        page['studio_dom_inventory'] = [
            {'test_id': name} for name in ['House', 'Folder', 'ChartRise']
        ]
        result = evaluate_studio_snapshot(
            s, 'cecilia.figueired65', profile='profile-a',
            confirmed_profile='profile-a', confirmed_handle='cecilia.figueired65',
        )
        self.assertEqual(result['auth_result'], 'AUTHENTICATED')
        self.assertEqual(result['account_identity'], 'ACCOUNT_CONFIRMED')
        self.assertEqual(result['auth_dom_markers'], ['studio_navigation'])

    def test_binding_is_profile_specific(self):
        s=fixture();s['pages'][0]['studio_dom_inventory'].pop(0)
        r=evaluate_studio_snapshot(s,'cecilia.figueired65',profile='profile-b',confirmed_profile='profile-a',confirmed_handle='cecilia.figueired65')
        self.assertEqual(r['account_identity'],'ACCOUNT_IDENTITY_UNKNOWN')

    def test_contradiction_overrides_binding(self):
        r=evaluate_studio_snapshot(fixture('different.user'),'cecilia.figueired65',profile='a',confirmed_profile='a',confirmed_handle='cecilia.figueired65')
        self.assertEqual(r['account_identity'],'ACCOUNT_MISMATCH')

    def test_identity_absence_does_not_break_auth(self):
        s=fixture();s['pages'][0]['studio_dom_inventory'].pop(0)

        r=evaluate_studio_snapshot(s, 'cecilia.figueired65')
        self.assertEqual(r['auth_result'], 'AUTHENTICATED')
        self.assertEqual(r['account_identity'], 'ACCOUNT_IDENTITY_UNKNOWN')

    def test_each_required_layer(self):
        for mutation in ('cookie','url','dom','login','challenge'):
            s=fixture();p=s['pages'][0]
            if mutation=='cookie':s['session_cookie_present']=False
            if mutation=='url':p['url']='https://www.tiktok.com/login'
            if mutation=='dom':p['studio_dom_inventory']=[]
            if mutation=='login':p['login_control_visible']=True
            if mutation=='challenge':p['semantic_signals']={'visible':['CHALLENGE']}
            self.assertEqual(evaluate_studio_snapshot(s)['auth_result'], 'UNKNOWN', mutation)

    def test_single_text_insufficient(self):
        s=fixture();s['pages'][0]['studio_dom_inventory']=s['pages'][0]['studio_dom_inventory'][:1]
        self.assertEqual(evaluate_studio_snapshot(s)['auth_result'], 'UNKNOWN')


if __name__ == '__main__': unittest.main()
