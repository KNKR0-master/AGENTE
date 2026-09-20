import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_MODULE = ROOT / "dashboard" / "publisher_state.js"


@unittest.skipUnless(shutil.which("node"), "node is required for browser-state tests")
class PublisherUIStateTests(unittest.TestCase):
    def run_node(self, script):
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def test_library_selection_is_single_state_and_supports_individual_and_page_actions(self):
        result = self.run_node(
            f"""
            const ui = require({json.dumps(str(STATE_MODULE))}).create();
            const individual = ui.setLibrarySelection('video-a', true);
            const afterDeselect = ui.setLibrarySelection('video-a', false);
            const page = ui.setLibraryPageSelection([
              {{drive_file_id: 'video-a', filename: 'a.mp4', product_name: 'Produto A'}},
              {{drive_file_id: 'video-b', filename: 'b.mp4', product_name: 'Produto B'}},
            ], true);
            const mixedProductPage = ui.setLibraryPageSelection([
              {{drive_file_id: 'video-c', filename: 'c.mp4', product_name: 'Produto C'}},
            ], true);
            console.log(JSON.stringify({{
              individual, afterDeselect, page, mixedProductPage,
              size: ui.librarySelected.size,
              selected: [...ui.librarySelected].sort(),
              details: ui.selectedLibraryDetails().map(item => item.product_name).sort(),
            }}));
            """
        )
        self.assertEqual(result["individual"], 1)
        self.assertEqual(result["afterDeselect"], 0)
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["mixedProductPage"], 3)
        self.assertEqual(result["size"], 3)
        self.assertEqual(result["selected"], ["video-a", "video-b", "video-c"])
        self.assertEqual(result["details"], ["Produto A", "Produto B", "Produto C"])

    def test_clear_selection_resets_counter_state(self):
        result = self.run_node(
            f"""
            const ui = require({json.dumps(str(STATE_MODULE))}).create();
            ui.setLibraryPageSelection(['video-a', 'video-b'], true);
            console.log(JSON.stringify({{before: ui.librarySelected.size, after: ui.clearLibrarySelection()}}));
            """
        )
        self.assertEqual(result, {"before": 2, "after": 0})

    def test_selection_summary_blocks_unknown_or_attention_items(self):
        result = self.run_node(
            f"""
            const ui = require({json.dumps(str(STATE_MODULE))}).create();
            ui.setLibraryPageSelection([
              {{drive_file_id: 'ready', filename: 'ready.mp4', product_name: 'Produto A', status: 'AVAILABLE'}},
              {{drive_file_id: 'attention', filename: 'attention.mp4', product_name: '', status: 'ATTENTION'}},
            ], true);
            const blocked = ui.librarySelectionSummary();
            ui.setLibrarySelection('attention', false);
            const ready = ui.librarySelectionSummary();
            console.log(JSON.stringify({{blocked: {{canCreate: blocked.canCreate, ready: blocked.ready, withoutProduct: blocked.withoutProduct, attention: blocked.attention}}, ready: {{canCreate: ready.canCreate, productCount: ready.productCount}}}}));
            """
        )
        self.assertEqual(result["blocked"], {"canCreate": False, "ready": 1, "withoutProduct": 1, "attention": 1})
        self.assertEqual(result["ready"], {"canCreate": True, "productCount": 1})

    def test_error_is_scoped_and_success_clears_only_same_operation(self):
        result = self.run_node(
            f"""
            const ui = require({json.dumps(str(STATE_MODULE))}).create();
            ui.setMessage('library', 'Falha ao consultar Biblioteca', true);
            const first = ui.visibleMessage();
            ui.setMessage('health', 'Drive indisponível', true);
            const both = ui.visibleMessage();
            ui.clearMessage('library');
            const afterLibraryRetry = ui.visibleMessage();
            ui.clearMessage('health');
            console.log(JSON.stringify({{
              first: first.scope,
              both: both.scope,
              afterLibraryRetry: afterLibraryRetry.scope,
              final: ui.visibleMessage(),
            }}));
            """
        )
        self.assertEqual(result["first"], "library")
        self.assertEqual(result["both"], "health")
        self.assertEqual(result["afterLibraryRetry"], "health")
        self.assertIsNone(result["final"])

    def test_product_search_is_normalized_limited_and_excludes_archived(self):
        result = self.run_node(
            f"""
            const state = require({json.dumps(str(STATE_MODULE))});
            const products = [
              {{id: 1, name: 'NIVEA Kit 04 Hidratantes Milk Lotion', short_name: 'Nivea Kit 4 Hidratantes', aliases: [], active: 1}},
              {{id: 2, name: 'Body Splash Coroa Sereia Brilho Bhaskar', short_name: 'Body Splash Kit 5', aliases: [], active: 1}},
              {{id: 3, name: 'Hidratação corporal', short_name: '', aliases: [], active: 1}},
              {{id: 4, name: 'Produto arquivado', short_name: 'Nivea antigo', aliases: [], active: 0}},
              ...Array.from({{length: 15}}, (_, index) => ({{id: index + 10, name: 'Produto ' + index, short_name: '', aliases: [], active: 1}})),
            ];
            console.log(JSON.stringify({{
              accent: state.searchProducts(products, 'hidratacao', '', 10).map(p => p.id),
              plural: state.searchProducts(products, 'hidratante', '', 10).map(p => p.id),
              context: state.searchProducts(products, '', 'BS KIT 5 COROA SEREIA', 10).map(p => p.id),
              initialCount: state.searchProducts(products, '', '', 10).length,
              archived: state.searchProducts(products, 'nivea', '', 10).map(p => p.id),
            }}));
            """
        )
        self.assertIn(3, result["accent"])
        self.assertIn(1, result["plural"])
        self.assertEqual(result["context"][0], 2)
        self.assertEqual(result["initialCount"], 10)
        self.assertEqual(result["archived"], [1])

    def test_rate_limited_with_cooldown_renders_release_time(self):
        result = self.run_node(
            f"""
            const state = require({json.dumps(str(STATE_MODULE))});
            const view = state.protectionPresentation({{
              state:'RATE_LIMITED', reason_code:'RATE_LIMIT_EXCEEDED',
              cooldown_until:'2026-09-20T21:40:00Z'
            }}, new Date('2026-09-20T18:00:00Z'));
            console.log(JSON.stringify(view));
            """
        )
        self.assertIn("pausada temporariamente", result["message"])
        self.assertIn("Hoje", result["nextAction"])

    def test_rate_limited_without_cooldown_requires_review(self):
        result = self.run_node(
            f"""
            const state = require({json.dumps(str(STATE_MODULE))});
            console.log(JSON.stringify(state.protectionPresentation({{state:'RATE_LIMITED'}}, new Date('2026-09-20T18:00:00Z'))));
            """
        )
        self.assertIn("possui hor", result["nextAction"])
        self.assertIn("Revise", result["nextAction"])

    def test_manual_clear_context_contains_friendly_complete_fields(self):
        result = self.run_node(
            f"""
            const state = require({json.dumps(str(STATE_MODULE))});
            console.log(JSON.stringify(state.protectionPresentation({{
              state:'SPAM_RISK',reason_code:'SPAM_RISK',observed_at:'2026-09-20T19:42:00Z',
              evidence_summary:'spam risk fixture'
            }},new Date('2026-09-20T20:00:00Z'))));
            """
        )
        self.assertEqual(result["stateLabel"], "Risco de spam detectado")
        self.assertIn("restri", result["reasonLabel"])
        self.assertNotIn("informado", result["detectedAt"])
        self.assertEqual(result["evidence"], "spam risk fixture")

    def test_transfer_unknown_has_safe_message_and_no_retry_language(self):
        result = self.run_node(
            f"""
            const state=require({json.dumps(str(STATE_MODULE))});
            console.log(JSON.stringify(state.ambiguousDraftPresentation({{upload_status:'TRANSFER_UNKNOWN'}})));
            """
        )
        self.assertIn("pode ter recebido", result["message"])
        self.assertIn("tente novamente", result["message"])
        self.assertEqual(result["action"], "Verificar status")
        self.assertNotIn("Reenviar", result["action"])

    def test_needs_reconciliation_has_safe_message_and_no_reupload(self):
        result = self.run_node(
            f"""
            const state=require({json.dumps(str(STATE_MODULE))});
            console.log(JSON.stringify(state.ambiguousDraftPresentation({{media_status:'NEEDS_RECONCILIATION'}})));
            """
        )
        self.assertIn("envie novamente", result["message"])
        self.assertEqual(result["action"], "Verificar status")
        self.assertNotIn("upload", result["action"].lower())

    def test_manual_clear_dialog_is_explicit_and_cancel_is_non_submit(self):
        html = (ROOT / "dashboard" / "publisher.html").read_text(encoding="utf-8")
        script = (ROOT / "dashboard" / "publisher.js").read_text(encoding="utf-8")
        self.assertIn('id="accountProtectionDialog"', html)
        self.assertIn('name="human_confirmed"', html)
        self.assertIn('data-close="accountProtectionDialog">Cancelar', html)
        self.assertIn('<button type="submit">Limpar proteção</button>', html)
        self.assertNotIn("data-clear-protection", script)


if __name__ == "__main__":
    unittest.main()
