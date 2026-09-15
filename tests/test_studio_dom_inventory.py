import unittest
from src.publisher.diagnostics import STUDIO_DOM_INVENTORY, sanitized_dom_inventory


class StudioInventoryTests(unittest.TestCase):
    def test_read_only_script(self):
        for forbidden in ['.click(', '.focus(', '.reload(', 'fetch(', 'localStorage', '.setAttribute(', 'location =']:
            self.assertNotIn(forbidden, STUDIO_DOM_INVENTORY)

    def test_redaction_and_semantic_labels(self):
        result = sanitized_dom_inventory([{'tag':'a', 'role':'link', 'ariaLabel':'Analytics',
            'text':'Analytics token=SECRET', 'href':'/tiktokstudio/analytics?token=SECRET', 'testId':'studio-nav'}])
        self.assertEqual(result[0]['aria_label'], 'Analytics')
        self.assertEqual(result[0]['test_id'], 'studio-nav')
        self.assertNotIn('SECRET', str(result))

    def test_bounded_and_missing_fields(self):
        self.assertEqual(len(sanitized_dom_inventory([{'tag':'button'}]*300)), 200)

    def test_invalid_result_is_explicit(self):
        with self.assertRaises(ValueError): sanitized_dom_inventory({})


if __name__ == '__main__': unittest.main()
