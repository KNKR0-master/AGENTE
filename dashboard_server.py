from __future__ import annotations

import json
import sqlite3
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.store import connect, ensure_two_versions, init_db, now_iso


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"
DB_PATH = ROOT / "data" / "agent.db"


def rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._api_get(parsed.path, parse_qs(parsed.query))
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length) or b"{}")
        self._api_post(parsed.path, payload)

    def _api_get(self, path: str, query: dict) -> None:
        conn = connect(DB_PATH)
        init_db(conn)

        if path == "/api/summary":
            products = rows(
                conn,
                """
                SELECT p.*, s.revenue_total, s.revenue_video, s.revenue_live,
                       s.sales_total, s.creator_count, s.video_count, s.avg_price,
                       s.gpm_video_export, s.cpa_ads, s.roas, s.collected_at, s.date_range
                FROM products p
                LEFT JOIN product_snapshots s ON s.id = (
                    SELECT id FROM product_snapshots
                    WHERE product_id = p.id
                    ORDER BY collected_at DESC, id DESC
                    LIMIT 1
                )
                ORDER BY COALESCE(s.revenue_total, 0) DESC
                """,
            )
            tasks = rows(conn, "SELECT * FROM production_tasks ORDER BY product_id, version_number")
            self._json({"products": products, "tasks": tasks})
            return

        if path == "/api/product":
            product_id = int(query.get("id", ["0"])[0])
            product = rows(conn, "SELECT * FROM products WHERE id = ?", (product_id,))
            snapshots = rows(
                conn,
                "SELECT * FROM product_snapshots WHERE product_id = ? ORDER BY collected_at DESC",
                (product_id,),
            )
            videos = rows(
                conn,
                """
                SELECT * FROM video_metrics
                WHERE product_id = ?
                ORDER BY revenue DESC
                LIMIT 20
                """,
                (product_id,),
            )
            creators = rows(
                conn,
                """
                SELECT * FROM creator_metrics
                WHERE product_id = ?
                ORDER BY revenue DESC
                LIMIT 20
                """,
                (product_id,),
            )
            tasks = rows(
                conn,
                "SELECT * FROM production_tasks WHERE product_id = ? ORDER BY version_number",
                (product_id,),
            )
            self._json(
                {
                    "product": product[0] if product else None,
                    "snapshots": snapshots,
                    "videos": videos,
                    "creators": creators,
                    "tasks": tasks,
                }
            )
            return

        self.send_error(404)

    def _api_post(self, path: str, payload: dict) -> None:
        conn = connect(DB_PATH)
        init_db(conn)
        timestamp = now_iso()

        if path == "/api/product/status":
            product_id = int(payload["product_id"])
            status = payload["status"]
            decision = payload.get("decision", status)
            conn.execute(
                "UPDATE products SET status = ?, decision = ?, updated_at = ? WHERE id = ?",
                (status, decision, timestamp, product_id),
            )
            if status == "validated":
                ensure_two_versions(conn, product_id)
            conn.commit()
            self._json({"ok": True})
            return

        if path == "/api/task":
            task_id = int(payload["task_id"])
            fields = {
                "status": payload.get("status"),
                "hook": payload.get("hook"),
                "format": payload.get("format"),
                "posted_at": payload.get("posted_at"),
                "scheduled_at": payload.get("scheduled_at"),
                "views": payload.get("views"),
                "clicks": payload.get("clicks"),
                "orders_count": payload.get("orders_count"),
                "gmv": payload.get("gmv"),
                "commission": payload.get("commission"),
                "notes": payload.get("notes"),
            }
            clean = {key: value for key, value in fields.items() if value is not None}
            if clean:
                assignments = ", ".join(f"{key} = ?" for key in clean)
                values = list(clean.values()) + [timestamp, task_id]
                conn.execute(
                    f"UPDATE production_tasks SET {assignments}, updated_at = ? WHERE id = ?",
                    values,
                )
                conn.commit()
            self._json({"ok": True})
            return

        self.send_error(404)

    def _json(self, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect(DB_PATH) as conn:
        init_db(conn)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    print("Dashboard local: http://127.0.0.1:8765")
    server.serve_forever()


if __name__ == "__main__":
    main()
