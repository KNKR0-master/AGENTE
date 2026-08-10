from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.content_pipeline import (
    ContentStatus,
    InvalidTransition,
    ValidationStatus,
    VideoProbeResult,
    assert_transition,
    enqueue_content,
    import_content_from_path,
    mark_published,
    mark_sent_to_phone,
    next_content_code,
    next_publishable_content,
)
from src.store import init_db, upsert_product


class ContentPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        init_db(self.conn)
        self.product_id = upsert_product(self.conn, "Creme para Articulacoes")

    def tearDown(self) -> None:
        self.conn.close()

    def test_next_content_code_uses_product_prefix(self) -> None:
        self.assertEqual(next_content_code(self.conn, self.product_id), "CREMEPARA-0001")

    def test_invalid_transition_is_blocked(self) -> None:
        with self.assertRaises(InvalidTransition):
            assert_transition(ContentStatus.READY.value, ContentStatus.PUBLISHED.value)

    def test_import_dedupes_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "video.mp4"
            source.write_bytes(b"fake-video-bytes")
            probe = VideoProbeResult(
                ValidationStatus.PASS,
                {
                    "duration": 12.5,
                    "width": 1080,
                    "height": 1920,
                    "resolution": "1080x1920",
                    "aspect_ratio": 0.5625,
                    "file_size": source.stat().st_size,
                    "codec": "h264",
                    "audio_present": True,
                },
            )

            with patch("src.content_pipeline.wait_for_stable_file"), patch("src.content_pipeline.probe_video", return_value=probe):
                first = import_content_from_path(
                    self.conn,
                    self.product_id,
                    source,
                    caption="Legenda",
                    hashtags="#teste",
                    storage_root=root / "content",
                )
                second = import_content_from_path(
                    self.conn,
                    self.product_id,
                    source,
                    caption="Legenda",
                    hashtags="#teste",
                    storage_root=root / "content",
                )

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["content_id"], second["content_id"])
        row = self.conn.execute("SELECT status FROM content_items WHERE id = ?", (first["content_id"],)).fetchone()
        self.assertEqual(row["status"], ContentStatus.READY.value)

    def test_queue_sent_and_published_flow(self) -> None:
        content_id = self._insert_ready_content()
        enqueue_content(self.conn, content_id, priority=80)

        next_item = next_publishable_content(self.conn)
        self.assertIsNotNone(next_item)
        self.assertEqual(next_item["id"], content_id)

        mark_sent_to_phone(self.conn, content_id)
        mark_published(self.conn, content_id, tiktok_url="https://www.tiktok.com/@perfil/video/123456")

        row = self.conn.execute("SELECT status, tiktok_video_id FROM content_items WHERE id = ?", (content_id,)).fetchone()
        self.assertEqual(row["status"], ContentStatus.PUBLISHED.value)
        self.assertEqual(row["tiktok_video_id"], "123456")

    def _insert_ready_content(self) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO content_items (
                content_code, product_id, original_filename, local_path,
                storage_reference, file_hash, validation_status,
                validation_results, status, created_at, updated_at
            )
            VALUES (
                'CREMEPARA-0001', ?, 'video.mp4', 'video.mp4',
                'video.mp4', 'hash-1', 'PASS', '{}', 'READY',
                '2026-08-08T00:00:00+00:00', '2026-08-08T00:00:00+00:00'
            )
            """,
            (self.product_id,),
        )
        self.conn.commit()
        return int(cursor.lastrowid)


if __name__ == "__main__":
    unittest.main()
