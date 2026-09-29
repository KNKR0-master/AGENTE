import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.publisher.library import list_library
from src.publisher.service import ProductFolderError, PublisherService, init_publisher
from src.store import connect, init_db, now_iso


class ProductCreateFoldersTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = connect(Path(self.temp.name) / 'test.db')
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.temp.name)
        stamp = now_iso()
        with self.conn:
            for folder_id, status in [('folder-a', 'ACTIVE'), ('folder-b', 'ACTIVE'), ('folder-offline', 'OUT_OF_SCOPE')]:
                self.conn.execute(
                    "INSERT INTO drive_folders(drive_folder_id,name,relative_path,operational_status,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                    (folder_id, folder_id, f'/Cecilia/{folder_id}', status, stamp, stamp),
                )

    def _counts(self):
        return (
            self.conn.execute("SELECT COUNT(*) FROM publisher_products").fetchone()[0],
            self.conn.execute("SELECT COUNT(*) FROM drive_folders WHERE product_id IS NOT NULL").fetchone()[0],
            self.conn.execute("SELECT COUNT(*) FROM drive_folder_mapping_history").fetchone()[0],
        )

    def test_zero_one_and_multiple_folders(self):
        plain = self.service.save_product({'name': 'Sem pasta', 'folder_ids': []})
        self.assertIsNotNone(plain)
        self.assertEqual(self._counts(), (1, 0, 0))
        single = self.service.save_product({'name': 'Com pasta', 'folder_ids': ['folder-a']})
        self.assertEqual(self._counts(), (2, 1, 1))
        self.assertEqual(self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='folder-a'").fetchone()[0], single)
        self.assertEqual(self.service.get_product(single)['name_source'], 'MANUAL')
        multiple = self.service.save_product({'name': 'Outra pasta', 'folder_ids': ['folder-b']})
        self.assertEqual(self._counts(), (3, 2, 2))
        self.assertNotEqual(multiple, single)

    def test_multiple_folders_share_one_product(self):
        product = self.service.save_product({'name': 'Duas pastas', 'folder_ids': ['folder-a', 'folder-b']})
        self.assertEqual(self._counts(), (1, 2, 2))
        self.assertEqual({row[0] for row in self.conn.execute('SELECT product_id FROM drive_folders WHERE product_id IS NOT NULL')}, {product})

    def test_duplicate_missing_and_unavailable_are_atomic(self):
        for folders, code in [
            (['folder-a', 'folder-a'], 'PRODUCT_FOLDER_DUPLICATE'),
            (['folder-a', 'missing'], 'PRODUCT_FOLDER_NOT_FOUND'),
            (['folder-a', 'folder-offline'], 'PRODUCT_FOLDER_UNAVAILABLE'),
        ]:
            with self.subTest(code=code):
                with self.assertRaises(ProductFolderError) as raised:
                    self.service.save_product({'name': 'Nao criar', 'folder_ids': folders})
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(self._counts(), (0, 0, 0))

    def test_existing_product_conflict_does_not_transfer(self):
        owner = self.service.save_product({'name': 'Dono', 'folder_ids': ['folder-b']})
        with self.assertRaises(ProductFolderError) as raised:
            self.service.save_product({'name': 'Novo', 'folder_ids': ['folder-a', 'folder-b']})
        self.assertEqual(raised.exception.code, 'PRODUCT_FOLDER_CONFLICT')
        self.assertIn('Dono', str(raised.exception))
        self.assertEqual(self._counts(), (1, 1, 1))
        self.assertEqual(self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='folder-b'").fetchone()[0], owner)

    def test_second_mapping_failure_rolls_back_everything(self):
        from src.drive_inventory import set_folder_mapping

        def fail_second(conn, folder_id, product_id, **kwargs):
            if folder_id == 'folder-b':
                raise RuntimeError('fixture second mapping failure')
            return set_folder_mapping(conn, folder_id, product_id, **kwargs)

        with patch('src.drive_inventory.set_folder_mapping', side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, 'second mapping failure'):
                self.service.save_product({'name': 'Nao criar', 'folder_ids': ['folder-a', 'folder-b']})
        self.assertEqual(self._counts(), (0, 0, 0))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0], 0)

    def test_library_resolves_new_association_without_drive_scan(self):
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                "INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,file_kind,classification,last_seen_at,created_at,updated_at) VALUES (?,?,?,?,?,?,'NEEDS_REVIEW',?,?,?)",
                ('video-a', 'folder-a', 'PRODUTO_G1C1A1_0101.mp4', 'video/mp4', 1000, 'VIDEO', stamp, stamp, stamp),
            )
        product = self.service.save_product({'name': 'Produto', 'default_caption': 'Legenda', 'folder_ids': ['folder-a']})
        self.assertEqual(self.conn.execute("SELECT product_id FROM drive_files WHERE drive_file_id='video-a'").fetchone()[0], product)
        item = next(row for row in list_library(self.conn)['items'] if row['drive_file_id'] == 'video-a')
        self.assertEqual(item['product_id'], product)

    def test_existing_add_remove_contract_remains(self):
        product = self.service.save_product({'name': 'Existente'})
        self.service.set_product_folder(product, 'folder-a', 'add')
        self.service.set_product_folder(product, 'folder-a', 'add')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_folders WHERE drive_folder_id='folder-a' AND product_id=?", (product,)).fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_folder_mapping_history WHERE drive_folder_id='folder-a'").fetchone()[0], 1)
        other = self.service.save_product({'name': 'Outro'})
        with self.assertRaises(ProductFolderError) as raised:
            self.service.set_product_folder(other, 'folder-a', 'add')
        self.assertEqual(raised.exception.code, 'PRODUCT_FOLDER_CONFLICT')
        self.service.set_product_folder(product, 'folder-a', 'remove')
        self.assertIsNone(self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='folder-a'").fetchone()[0])

    def test_manual_container_role_is_untouched(self):
        with self.conn:
            self.conn.execute(
                "UPDATE drive_folders SET folder_type='PRODUCT_CONTAINER',role_source='MANUAL',role_review_status='APPLIED',auto_create_new_direct_children=0 WHERE drive_folder_id='folder-b'"
            )
        self.service.save_product({'name': 'Produto manual', 'folder_ids': ['folder-a']})
        role = self.conn.execute(
            "SELECT folder_type,role_source,role_review_status,auto_create_new_direct_children FROM drive_folders WHERE drive_folder_id='folder-b'"
        ).fetchone()
        self.assertEqual(tuple(role), ('PRODUCT_CONTAINER', 'MANUAL', 'APPLIED', 0))
