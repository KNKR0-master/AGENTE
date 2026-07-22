from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path("data/agent.db")


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            price REAL,
            commission_rate REAL,
            shop_name TEXT,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'monitoring',
            decision TEXT NOT NULL DEFAULT 'monitorar',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            date_range TEXT,
            collected_at TEXT NOT NULL,
            revenue_total REAL NOT NULL DEFAULT 0,
            revenue_video REAL NOT NULL DEFAULT 0,
            revenue_live REAL NOT NULL DEFAULT 0,
            sales_total REAL NOT NULL DEFAULT 0,
            video_count REAL NOT NULL DEFAULT 0,
            creator_count REAL NOT NULL DEFAULT 0,
            views_video_export REAL NOT NULL DEFAULT 0,
            views_creator_export REAL NOT NULL DEFAULT 0,
            ad_cost REAL NOT NULL DEFAULT 0,
            avg_price REAL NOT NULL DEFAULT 0,
            gpm_video_export REAL NOT NULL DEFAULT 0,
            cpa_ads REAL NOT NULL DEFAULT 0,
            roas REAL NOT NULL DEFAULT 0,
            confidence TEXT NOT NULL DEFAULT 'estimado',
            raw_meta TEXT,
            UNIQUE(product_id, source, date_range, collected_at),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS video_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            creator_username TEXT,
            published_at TEXT,
            description TEXT,
            duration TEXT,
            revenue REAL NOT NULL DEFAULT 0,
            sales REAL NOT NULL DEFAULT 0,
            views REAL NOT NULL DEFAULT 0,
            gpm REAL NOT NULL DEFAULT 0,
            cpa REAL NOT NULL DEFAULT 0,
            ads_view_ratio TEXT,
            ad_cost REAL NOT NULL DEFAULT 0,
            roas REAL NOT NULL DEFAULT 0,
            kalodata_url TEXT,
            tiktok_url TEXT,
            row_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(snapshot_id) REFERENCES product_snapshots(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS creator_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            creator_name TEXT,
            creator_username TEXT,
            followers REAL NOT NULL DEFAULT 0,
            revenue REAL NOT NULL DEFAULT 0,
            sales REAL NOT NULL DEFAULT 0,
            avg_price REAL NOT NULL DEFAULT 0,
            engagement_rate TEXT,
            new_followers REAL NOT NULL DEFAULT 0,
            product_count REAL NOT NULL DEFAULT 0,
            live_count REAL NOT NULL DEFAULT 0,
            live_gmv REAL NOT NULL DEFAULT 0,
            video_count REAL NOT NULL DEFAULT 0,
            video_gmv REAL NOT NULL DEFAULT 0,
            views REAL NOT NULL DEFAULT 0,
            creator_first_seen TEXT,
            kalodata_url TEXT,
            tiktok_url TEXT,
            row_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(snapshot_id) REFERENCES product_snapshots(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS production_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'planejado',
            hook TEXT,
            format TEXT,
            posted_at TEXT,
            scheduled_at TEXT,
            views REAL NOT NULL DEFAULT 0,
            clicks REAL NOT NULL DEFAULT 0,
            orders_count REAL NOT NULL DEFAULT 0,
            gmv REAL NOT NULL DEFAULT 0,
            commission REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(product_id, version_number),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        """
    )
    conn.commit()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def product_key(name: str) -> str:
    normalized = " ".join(name.lower().strip().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def stable_hash(parts: list[Any]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def upsert_product(
    conn: sqlite3.Connection,
    name: str,
    category: str | None = None,
    source_url: str | None = None,
    price: float | None = None,
    commission_rate: float | None = None,
    shop_name: str | None = None,
) -> int:
    timestamp = now_iso()
    key = product_key(name)
    conn.execute(
        """
        INSERT INTO products (
            product_key, name, category, price, commission_rate, shop_name,
            source_url, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
            category = COALESCE(excluded.category, products.category),
            price = COALESCE(excluded.price, products.price),
            commission_rate = COALESCE(excluded.commission_rate, products.commission_rate),
            shop_name = COALESCE(excluded.shop_name, products.shop_name),
            source_url = COALESCE(excluded.source_url, products.source_url),
            updated_at = excluded.updated_at
        """,
        (key, name, category, price, commission_rate, shop_name, source_url, timestamp, timestamp),
    )
    row = conn.execute("SELECT id FROM products WHERE product_key = ?", (key,)).fetchone()
    conn.commit()
    return int(row["id"])


def create_snapshot(
    conn: sqlite3.Connection,
    product_id: int,
    source: str,
    date_range: str,
    video_records: list[dict[str, Any]],
    creator_records: list[dict[str, Any]],
    raw_meta: dict[str, Any] | None = None,
) -> int:
    revenue_video = sum(float(row["revenue"]) for row in video_records)
    video_sales = sum(float(row["sales"]) for row in video_records)
    video_views = sum(float(row["views"]) for row in video_records)
    ad_cost = sum(float(row["ad_cost"]) for row in video_records)

    revenue_total = sum(float(row["revenue"]) for row in creator_records) or revenue_video
    sales_total = sum(float(row["sales"]) for row in creator_records) or video_sales
    creator_views = sum(float(row["views"]) for row in creator_records)
    revenue_live = sum(float(row["live_gmv"]) for row in creator_records)

    avg_price = revenue_total / sales_total if sales_total else 0
    gpm_video = revenue_video / video_views * 1000 if video_views else 0
    cpa_ads = ad_cost / video_sales if video_sales else 0
    roas = revenue_video / ad_cost if ad_cost else 0
    timestamp = now_iso()

    cursor = conn.execute(
        """
        INSERT INTO product_snapshots (
            product_id, source, date_range, collected_at, revenue_total,
            revenue_video, revenue_live, sales_total, video_count, creator_count,
            views_video_export, views_creator_export, ad_cost, avg_price,
            gpm_video_export, cpa_ads, roas, raw_meta
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            source,
            date_range,
            timestamp,
            revenue_total,
            revenue_video,
            revenue_live,
            sales_total,
            len(video_records),
            len(creator_records),
            video_views,
            creator_views,
            ad_cost,
            avg_price,
            gpm_video,
            cpa_ads,
            roas,
            json.dumps(raw_meta or {}, ensure_ascii=False),
        ),
    )
    snapshot_id = int(cursor.lastrowid)

    _insert_videos(conn, product_id, snapshot_id, video_records)
    _insert_creators(conn, product_id, snapshot_id, creator_records)
    conn.commit()
    return snapshot_id


def ensure_two_versions(conn: sqlite3.Connection, product_id: int) -> None:
    timestamp = now_iso()
    for version in (1, 2):
        conn.execute(
            """
            INSERT INTO production_tasks (
                product_id, version_number, status, created_at, updated_at
            )
            VALUES (?, ?, 'planejado', ?, ?)
            ON CONFLICT(product_id, version_number) DO NOTHING
            """,
            (product_id, version, timestamp, timestamp),
        )
    conn.commit()


def _insert_videos(
    conn: sqlite3.Connection,
    product_id: int,
    snapshot_id: int,
    records: list[dict[str, Any]],
) -> None:
    for row in records:
        row_hash = stable_hash(
            [product_id, row.get("creator_username"), row.get("published_at"), row.get("tiktok_url")]
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO video_metrics (
                product_id, snapshot_id, creator_username, published_at,
                description, duration, revenue, sales, views, gpm, cpa,
                ads_view_ratio, ad_cost, roas, kalodata_url, tiktok_url, row_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                snapshot_id,
                row.get("creator_username"),
                row.get("published_at"),
                row.get("description"),
                row.get("duration"),
                row.get("revenue"),
                row.get("sales"),
                row.get("views"),
                row.get("gpm"),
                row.get("cpa"),
                row.get("ads_view_ratio"),
                row.get("ad_cost"),
                row.get("roas"),
                row.get("kalodata_url"),
                row.get("tiktok_url"),
                row_hash,
            ),
        )


def _insert_creators(
    conn: sqlite3.Connection,
    product_id: int,
    snapshot_id: int,
    records: list[dict[str, Any]],
) -> None:
    for row in records:
        row_hash = stable_hash(
            [product_id, row.get("creator_username"), row.get("kalodata_url"), row.get("revenue")]
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO creator_metrics (
                product_id, snapshot_id, creator_name, creator_username,
                followers, revenue, sales, avg_price, engagement_rate,
                new_followers, product_count, live_count, live_gmv,
                video_count, video_gmv, views, creator_first_seen,
                kalodata_url, tiktok_url, row_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                snapshot_id,
                row.get("creator_name"),
                row.get("creator_username"),
                row.get("followers"),
                row.get("revenue"),
                row.get("sales"),
                row.get("avg_price"),
                row.get("engagement_rate"),
                row.get("new_followers"),
                row.get("product_count"),
                row.get("live_count"),
                row.get("live_gmv"),
                row.get("video_count"),
                row.get("video_gmv"),
                row.get("views"),
                row.get("creator_first_seen"),
                row.get("kalodata_url"),
                row.get("tiktok_url"),
                row_hash,
            ),
        )
