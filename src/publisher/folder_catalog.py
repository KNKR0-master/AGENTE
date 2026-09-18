from __future__ import annotations

import json
import uuid
from pathlib import Path

from src.store import now_iso


FOLDER_ROLES = {"CATEGORY", "PRODUCT", "PRODUCT_CONTAINER", "UNCLASSIFIED"}
PROTECTED_SOURCES = {"MANUAL", "MIGRATION_HIGH_CONFIDENCE"}
AMBIGUOUS_NAMES = {
    "MEIA BICHINHO", "TRAVESSEIRO", "LENÇOL 400 FIOS",
    "LIQUIDIFICADOR PORTÁTIL", "KIT 4 TRAVESSEIROS",
}


def load_plan(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data.get("folders"), list) or not isinstance(data.get("summary"), dict):
        raise ValueError("FOLDER_CATALOG_PLAN_INVALID")
    return data


def _is_duplicate(row: dict) -> bool:
    return str(row.get("folder_name") or "").strip().upper() in AMBIGUOUS_NAMES


def _safe_product(row: dict) -> bool:
    return (
        row.get("proposed_role") == "PRODUCT"
        and row.get("confidence") == "HIGH"
        and not row.get("existing_product_id")
        and row.get("deduplication") == "NO_MATCH"
        and not row.get("possible_existing_product_matches")
        and not _is_duplicate(row)
    )


def preview_catalog_migration(conn, plan: dict) -> dict:
    folders = plan["folders"]
    current_ids = {
        row[0] for row in conn.execute(
            "SELECT drive_folder_id FROM drive_folders WHERE operational_status='ACTIVE'"
        )
    }
    planned_ids = {str(row["folder_id"]) for row in folders}
    missing = sorted(planned_ids - current_ids)
    safe = [row for row in folders if _safe_product(row)]
    categories = [row for row in folders if row.get("proposed_role") == "CATEGORY" and row.get("confidence") == "HIGH"]
    containers = [row for row in folders if row.get("proposed_role") == "PRODUCT_CONTAINER" and row.get("confidence") == "HIGH"]
    existing = [row for row in folders if row.get("existing_product_id")]
    probable = [row for row in folders if row.get("deduplication") == "PROBABLE_EXISTING_PRODUCT_MATCH"]
    duplicate = [row for row in folders if _is_duplicate(row)]
    medium = [row for row in folders if row.get("proposed_role") == "PRODUCT" and row.get("confidence") == "MEDIUM"]
    ambiguous = [row for row in folders if row.get("proposed_role") == "UNCLASSIFIED" or _is_duplicate(row)]
    return {
        "safe_high_products_to_create": len(safe),
        "category_roles_to_apply": len(categories),
        "product_container_roles_to_apply": len(containers),
        "product_roles_to_apply": len(safe) + len(existing),
        "existing_associations_preserved": len(existing),
        "probable_existing_matches_left_for_review": len(probable),
        "duplicates_left_for_review": len(duplicate),
        "medium_products_left_for_review": len(medium),
        "ambiguous_folders_left_for_review": len(ambiguous),
        "review_items_remaining": len({str(r["folder_id"]) for r in probable + duplicate + medium + ambiguous}),
        "missing_folder_ids": missing,
        "safe_folder_ids": [str(row["folder_id"]) for row in safe],
        "coherent": not missing and len(folders) == int(plan["summary"].get("total_relevant_folders", -1)),
    }


def _create_drive_product(conn, folder: dict, timestamp: str) -> int:
    folder_id = str(folder["folder_id"])
    existing = conn.execute(
        "SELECT product_id FROM publisher_products WHERE source_drive_folder_id=?", (folder_id,)
    ).fetchone()
    if existing:
        return int(existing[0])
    name = str(folder.get("suggested_product_name") or folder["folder_name"]).strip()
    product_id = conn.execute(
        "INSERT INTO products(product_key,name,created_at,updated_at) VALUES (?,?,?,?)",
        (f"publisher:{uuid.uuid4().hex}", name, timestamp, timestamp),
    ).lastrowid
    conn.execute(
        """INSERT INTO publisher_products
           (product_id,name,short_name,default_caption,flow_prompt,active,created_at,updated_at,
            name_source,source_drive_folder_id)
           VALUES (?,?,?,'','',1,?,?, 'DRIVE',?)""",
        (product_id, name, str(folder.get("suggested_short_name") or name).strip(), timestamp, timestamp, folder_id),
    )
    return int(product_id)


def apply_catalog_migration(conn, plan: dict) -> dict:
    preview = preview_catalog_migration(conn, plan)
    if not preview["coherent"]:
        raise ValueError("FOLDER_CATALOG_PREVIEW_DIVERGED")
    timestamp = now_iso()
    created = 0
    associations = 0
    categories = products = containers = 0
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        for folder in plan["folders"]:
            folder_id = str(folder["folder_id"])
            current = conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (folder_id,)).fetchone()
            if not current:
                raise ValueError(f"FOLDER_NOT_FOUND:{folder_id}")
            current = dict(current)
            if current.get("role_source") == "MANUAL":
                continue
            role = str(folder.get("proposed_role") or "UNCLASSIFIED")
            confidence = str(folder.get("confidence") or "LOW")
            safe = _safe_product(folder)
            existing_product = current.get("product_id") or folder.get("existing_product_id")
            apply_role = confidence == "HIGH" and role in {"CATEGORY", "PRODUCT_CONTAINER"}
            if existing_product:
                role = "PRODUCT"
                apply_role = True
            if safe:
                role = "PRODUCT"
                apply_role = True
                before = conn.execute(
                    "SELECT product_id FROM publisher_products WHERE source_drive_folder_id=?", (folder_id,)
                ).fetchone()
                existing_product = _create_drive_product(conn, folder, timestamp)
                created += 0 if before else 1
                associations += 0 if current.get("product_id") == existing_product else 1
            review_status = "APPLIED" if apply_role else "REVIEW_REQUIRED"
            if folder.get("deduplication") == "PROBABLE_EXISTING_PRODUCT_MATCH" or _is_duplicate(folder):
                review_status = "REVIEW_REQUIRED"
                apply_role = False
            persisted_role = role if apply_role else "UNCLASSIFIED"
            source = "MIGRATION_HIGH_CONFIDENCE" if apply_role else "HEURISTIC"
            suggestions = folder.get("possible_existing_product_matches") or []
            suggestion = suggestions[0] if suggestions else {}
            conn.execute(
                """UPDATE drive_folders SET folder_type=?, role_source=?, role_confidence=?,
                   role_reason=?, role_review_status=?, role_decided_at=?,
                   suggested_product_id=COALESCE(?,suggested_product_id),
                   suggestion_confidence=COALESCE(?,suggestion_confidence),
                   suggestion_reason=COALESCE(?,suggestion_reason),
                   product_id=COALESCE(?,product_id),
                   mapping_status=CASE WHEN ? IS NOT NULL THEN 'MAPPED_TO_PRODUCT' ELSE mapping_status END,
                   mapping_source=CASE WHEN ? IS NOT NULL THEN 'MIGRATION_HIGH_CONFIDENCE' ELSE mapping_source END,
                   updated_at=? WHERE drive_folder_id=?""",
                (persisted_role, source, confidence, folder.get("reason"), review_status,
                 timestamp if apply_role else None, suggestion.get("product_id"), suggestion.get("score"),
                 suggestion.get("match"), existing_product, existing_product,
                 existing_product, timestamp, folder_id),
            )
            categories += int(apply_role and role == "CATEGORY")
            products += int(apply_role and role == "PRODUCT")
            containers += int(apply_role and role == "PRODUCT_CONTAINER")
        conn.execute(
            "INSERT OR IGNORE INTO publisher_migrations(name,applied_at) VALUES ('folder_catalog_v1',?)", (timestamp,)
        )
    return {**preview, "products_created": created,
            "product_folder_associations_created": associations,
            "category_roles_applied": categories, "product_roles_applied": products,
            "product_container_roles_applied": containers}


def list_folder_reviews(conn, *, review_filter: str = "REVIEW_REQUIRED") -> list[dict]:
    clauses = ["f.operational_status='ACTIVE'"]
    params: list = []
    if review_filter and review_filter != "ALL":
        if review_filter == "MEDIUM":
            clauses.append("f.role_confidence='MEDIUM'")
        elif review_filter == "AMBIGUOUS":
            clauses.append("f.folder_type='UNCLASSIFIED' AND f.role_review_status IN ('REVIEW_REQUIRED','DEFERRED')")
        elif review_filter == "MATCH_EXISTING":
            clauses.append("f.suggested_product_id IS NOT NULL")
        elif review_filter == "WITHOUT_PRODUCT":
            clauses.append("f.product_id IS NULL")
        else:
            clauses.append("f.role_review_status=?")
            params.append(review_filter)
    rows = conn.execute(
        f"""SELECT f.drive_folder_id folder_id,f.name,f.relative_path,f.direct_video_count,
             f.direct_subfolder_count,f.folder_type proposed_role,f.role_confidence confidence,
             f.role_reason reason,f.role_review_status,f.product_id,f.suggested_product_id,
             p.name suggested_product_name
             FROM drive_folders f LEFT JOIN publisher_products p ON p.product_id=f.suggested_product_id
             WHERE {' AND '.join(clauses)} ORDER BY f.relative_path COLLATE NOCASE""", params,
    ).fetchall()
    return [dict(row) for row in rows]


def decide_folder_role(conn, folder_id: str, role: str, *, product_id=None) -> dict:
    role = str(role).upper()
    if role not in FOLDER_ROLES:
        raise ValueError("FOLDER_ROLE_INVALID")
    current = conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (folder_id,)).fetchone()
    if not current:
        raise ValueError("FOLDER_NOT_FOUND")
    if product_id is not None and not conn.execute(
        "SELECT 1 FROM publisher_products WHERE product_id=?", (int(product_id),)
    ).fetchone():
        raise ValueError("PRODUCT_NOT_FOUND")
    timestamp = now_iso()
    if role == "UNCLASSIFIED" and product_id is None:
        with conn:
            conn.execute(
                """UPDATE drive_folders SET folder_type='UNCLASSIFIED',role_source='HEURISTIC',
                   role_review_status='DEFERRED',role_decided_at=NULL,updated_at=?
                   WHERE drive_folder_id=?""", (timestamp, folder_id),
            )
        return dict(conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (folder_id,)).fetchone())
    with conn:
        conn.execute(
            """UPDATE drive_folders SET folder_type=?,role_source='MANUAL',role_confidence='HIGH',
               role_reason='Decisão humana',role_review_status='APPLIED',role_decided_at=?,
               product_id=?,mapping_status=?,mapping_source='MANUAL',updated_at=?
               WHERE drive_folder_id=?""",
            (role, timestamp, int(product_id) if product_id is not None else None,
             "MAPPED_TO_PRODUCT" if product_id is not None else "CATEGORY_ONLY", timestamp, folder_id),
        )
    return dict(conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (folder_id,)).fetchone())


def sync_drive_product_name(conn, folder_id: str, folder_name: str) -> None:
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='publisher_products'"
    ).fetchone():
        return
    conn.execute(
        """UPDATE publisher_products SET name=?,short_name=?,updated_at=?
           WHERE source_drive_folder_id=? AND name_source='DRIVE'""",
        (folder_name, folder_name, now_iso(), folder_id),
    )
