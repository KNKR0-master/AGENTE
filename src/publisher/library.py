from __future__ import annotations

import re
from collections.abc import Iterable

from src.store import now_iso


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv"}
HIGH_CONFIDENCE = re.compile(r"(?:^|[^A-Z0-9])(?:G\d+C\d+A\d+|FINAL)(?:$|[^A-Z0-9])", re.IGNORECASE)
MEDIUM_CONFIDENCE = re.compile(r"(?:^|[ _.-])VIDEO[ _.-]?\d+(?:$|[ _.-])", re.IGNORECASE)
FRIENDLY_STATUS = {
    "AVAILABLE": "DISPONÍVEL",
    "RESERVED": "RESERVADO",
    "ATTENTION": "REQUER ATENÇÃO",
    "IGNORED": "IGNORADO",
    "CONSUMED": "CONSUMIDO",
    "OUT_OF_SCOPE": "FORA DO FLUXO",
}


def _is_video(row: dict) -> bool:
    name = str(row.get("name") or "")
    mime = str(row.get("mime_type") or "").lower()
    kind = str(row.get("file_kind") or "").upper()
    return kind == "VIDEO" or mime.startswith("video/") or any(name.lower().endswith(ext) for ext in VIDEO_EXTENSIONS)


def _name_confidence(name: str) -> tuple[str, str]:
    if HIGH_CONFIDENCE.search(name or ""):
        return "HIGH", "Padrão G<n>C<n>A<n> ou FINAL"
    if MEDIUM_CONFIDENCE.search(name or ""):
        return "MEDIUM", "Padrão VIDEO <n>"
    return "LOW", "Nome sem padrão editorial confirmado"


def _ancestor_product_sql() -> str:
    return """
    WITH RECURSIVE mapped(folder_id, product_id, distance) AS (
      SELECT drive_folder_id, product_id, 0
        FROM drive_folders
       WHERE product_id IS NOT NULL AND operational_status='ACTIVE'
      UNION ALL
      SELECT child.drive_folder_id, mapped.product_id, mapped.distance + 1
        FROM drive_folders child
        JOIN mapped ON child.parent_drive_folder_id = mapped.folder_id
       WHERE child.product_id IS NULL AND child.operational_status='ACTIVE'
    )
    """


def _base_query() -> str:
    return _ancestor_product_sql() + """
    SELECT df.*, f.name folder_name, f.relative_path folder_path,
           f.parent_drive_folder_id, f.product_id direct_folder_product_id,
           COALESCE(f.product_id, ancestor.product_id) inferred_product_id,
           pp.name inferred_product_name, pp.short_name inferred_product_short_name,
           pp.default_caption inferred_caption, pp.active inferred_product_active,
           direct_pp.name direct_folder_product_name,
           ancestor_pp.name ancestor_product_name,
           ov.product_id override_product_id, dec.decision, dec.reason decision_reason,
           ma.id media_asset_id, ma.status media_status, ma.consumed_by_draft_id,
           d.id draft_id, d.status draft_status
      FROM drive_files df
      JOIN drive_folders f ON f.drive_folder_id=df.drive_folder_id
      LEFT JOIN mapped ancestor ON ancestor.folder_id=f.drive_folder_id
       AND ancestor.distance=(SELECT MIN(m2.distance) FROM mapped m2 WHERE m2.folder_id=f.drive_folder_id)
      LEFT JOIN publisher_media_overrides ov ON ov.drive_file_id=df.drive_file_id
      LEFT JOIN publisher_media_decisions dec ON dec.drive_file_id=df.drive_file_id
      LEFT JOIN publisher_media_assets ma ON ma.remote_file_id=df.drive_file_id
      LEFT JOIN publisher_drafts d ON d.media_asset_id=ma.id AND d.status<>'ARCHIVED'
      LEFT JOIN publisher_products pp ON pp.product_id=COALESCE(ov.product_id, f.product_id, ancestor.product_id)
      LEFT JOIN publisher_products direct_pp ON direct_pp.product_id=f.product_id
      LEFT JOIN publisher_products ancestor_pp ON ancestor_pp.product_id=ancestor.product_id
     WHERE df.missing_since IS NULL
       AND f.operational_status='ACTIVE'
    """


def _raw_rows(conn, *, drive_file_ids: Iterable[str] | None = None, search: str = "",
              path: str = "", product_id=None, file_type: str = "VIDEO") -> list[dict]:
    clauses: list[str] = []
    params: list = []
    if drive_file_ids is not None:
        ids = list(dict.fromkeys(str(value) for value in drive_file_ids if str(value).strip()))
        if not ids:
            return []
        clauses.append(f"df.drive_file_id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    if search:
        like = f"%{search.strip()}%"
        clauses.append("(LOWER(df.name) LIKE LOWER(?) OR LOWER(f.relative_path) LIKE LOWER(?) OR LOWER(df.drive_file_id) LIKE LOWER(?) OR LOWER(pp.name) LIKE LOWER(?) OR LOWER(pp.short_name) LIKE LOWER(?))")
        params.extend([like] * 5)
    if path:
        clauses.append("LOWER(f.relative_path) LIKE LOWER(?)")
        params.append(f"%{path.strip()}%")
    if product_id not in (None, "", "ALL"):
        if str(product_id).upper() in {"UNASSIGNED", "0"}:
            clauses.append("COALESCE(ov.product_id, f.product_id, ancestor.product_id) IS NULL")
        else:
            clauses.append("COALESCE(ov.product_id, f.product_id, ancestor.product_id)=?")
            params.append(int(product_id))
    if file_type and str(file_type).upper() != "ALL":
        if str(file_type).upper() == "VIDEO":
            clauses.append("(df.file_kind='VIDEO' OR LOWER(COALESCE(df.mime_type,'')) LIKE 'video/%' OR LOWER(df.name) LIKE '%.mp4' OR LOWER(df.name) LIKE '%.mov' OR LOWER(df.name) LIKE '%.m4v' OR LOWER(df.name) LIKE '%.webm' OR LOWER(df.name) LIKE '%.avi' OR LOWER(df.name) LIKE '%.mkv')")
        elif str(file_type).upper() == "IMAGE":
            clauses.append("(LOWER(COALESCE(df.mime_type,'')) LIKE 'image/%' OR LOWER(df.name) LIKE '%.png' OR LOWER(df.name) LIKE '%.jpg' OR LOWER(df.name) LIKE '%.jpeg' OR LOWER(df.name) LIKE '%.gif' OR LOWER(df.name) LIKE '%.webp')")
        else:
            clauses.append("df.file_kind=?")
            params.append(str(file_type).upper())
    query = _base_query()
    if clauses:
        query += " AND " + " AND ".join(clauses)
    query += " ORDER BY COALESCE(df.modified_time, df.created_time, df.updated_at) DESC, df.name COLLATE NOCASE"
    return [dict(row) for row in conn.execute(query, params)]


def _classify(row: dict) -> dict:
    name = str(row.get("name") or "")
    video = _is_video(row)
    confidence, confidence_reason = _name_confidence(name)
    decision = str(row.get("decision") or "").upper() or None
    product_id = row.get("override_product_id") or row.get("inferred_product_id")
    product_name = row.get("inferred_product_name")
    caption = str(row.get("inferred_caption") or "")
    technical = str(row.get("classification") or "").upper()
    media_status = str(row.get("media_status") or "").upper()

    result = {
        "drive_file_id": row["drive_file_id"],
        "filename": name,
        "name": name,
        "folder_id": row.get("drive_folder_id"),
        "folder_name": row.get("folder_name"),
        "folder_path": row.get("folder_path") or "",
        "mime_type": row.get("mime_type") or "",
        "file_kind": row.get("file_kind") or "OTHER",
        "file_type": "VIDEO" if video else ("IMAGE" if str(row.get("mime_type") or "").lower().startswith("image/") else "OTHER"),
        "size": row.get("size"),
        "modified_time": row.get("modified_time"),
        "created_time": row.get("created_time"),
        "md5_checksum": row.get("md5_checksum"),
        "web_view_link": row.get("web_view_link"),
        "thumbnail_link": row.get("thumbnail_link"),
        "product_id": int(product_id) if product_id is not None else None,
        "product_name": product_name,
        "product_short_name": row.get("inferred_product_short_name") or product_name,
        "product_archived": product_id is not None and row.get("inferred_product_active") == 0,
        "caption": caption,
        "decision": decision,
        "decision_reason": row.get("decision_reason"),
        "classification": technical,
        "classification_confidence": confidence,
        "classification_reason": confidence_reason,
        "classification_label": {"HIGH": "Reconhecido", "MEDIUM": "Provável", "LOW": "Revisar"}[confidence],
        "product_resolution": "MEDIA_OVERRIDE" if row.get("override_product_id") is not None else (
            "DIRECT_FOLDER" if row.get("direct_folder_product_id") is not None else (
                "ANCESTOR_FOLDER" if row.get("inferred_product_id") is not None else "UNASSIGNED"
            )
        ),
        "folder_product_id": row.get("direct_folder_product_id") or row.get("inferred_product_id"),
        "folder_product_name": row.get("direct_folder_product_name") or row.get("ancestor_product_name"),
        "media_asset_id": row.get("media_asset_id"),
        "media_status": media_status or None,
        "draft_id": row.get("draft_id"),
        "draft_status": row.get("draft_status"),
        "eligible": False,
        "status": "ATTENTION",
        "status_label": FRIENDLY_STATUS["ATTENTION"],
        "reason": "",
    }
    if decision == "IGNORED":
        result.update(status="IGNORED", status_label=FRIENDLY_STATUS["IGNORED"], reason=row.get("decision_reason") or "Ignorado manualmente")
        return result
    if not video:
        result.update(status="OUT_OF_SCOPE", status_label=FRIENDLY_STATUS["OUT_OF_SCOPE"], reason="Arquivo não é vídeo; use o filtro de tipo para revisar")
        return result
    if media_status in {"RESERVED", "PROCESSING", "NEEDS_RECONCILIATION", "CONSUMED_PENDING_DELETE"} or (row.get("draft_id") and row.get("draft_status") != "ARCHIVED"):
        result.update(status="RESERVED", status_label=FRIENDLY_STATUS["RESERVED"], reason=f"Em uso pelo Draft #{row['draft_id']}" if row.get("draft_id") else "Mídia reservada")
        return result
    if media_status == "CONSUMED":
        result.update(status="CONSUMED", status_label=FRIENDLY_STATUS["CONSUMED"], reason="Mídia já consumida")
        return result
    if technical in {"INVALID", "DUPLICATE", "ALREADY_PUBLISHED", "ALREADY_QUEUED"}:
        result.update(reason=row.get("classification_reason") or "Inventário marcou o arquivo como não elegível")
        return result
    if not product_id:
        result.update(reason="Produto da pasta ainda não associado")
        return result
    if not caption.strip():
        result.update(reason="Legenda do produto não configurada")
        return result
    valid_metadata = bool(row.get("size") and int(row.get("size") or 0) > 0 and str(row.get("mime_type") or "").lower().startswith("video/"))
    manually_valid = decision == "VALID"
    if not valid_metadata:
        result.update(reason="Metadados técnicos incompletos")
        return result
    if manually_valid or confidence in {"HIGH", "MEDIUM"}:
        result.update(eligible=True, status="AVAILABLE", status_label=FRIENDLY_STATUS["AVAILABLE"], reason="Elegível para lote" if not manually_valid else "Validado manualmente")
        return result
    result.update(reason="Nome sem padrão confirmado; marque como válido após revisão")
    return result


def list_library(conn, *, page: int = 1, page_size: int = 50, search: str = "", path: str = "",
                 product_id=None, file_type: str = "VIDEO", status: str = "", decision: str = "") -> dict:
    page = max(1, int(page or 1))
    page_size = min(100, max(1, int(page_size or 50)))
    items = [_classify(row) for row in _raw_rows(conn, search=search, path=path, product_id=product_id, file_type=file_type)]
    status = str(status or "").upper()
    decision = str(decision or "").upper()
    if status:
        items = [item for item in items if item["status"] == status]
    if decision:
        items = [item for item in items if (item["decision"] or "NONE") == decision]
    total = len(items)
    folder_summaries = []
    grouped = {}
    for item in items:
        if item["file_type"] != "VIDEO":
            continue
        group = grouped.setdefault(item["folder_id"], {
            "folder_id": item["folder_id"], "folder_name": item["folder_name"],
            "folder_path": item["folder_path"], "video_count": 0,
            "recognized_count": 0, "product_id": item["folder_product_id"],
            "product_name": item["folder_product_name"],
        })
        group["video_count"] += 1
        if item["classification_confidence"] in {"HIGH", "MEDIUM"}:
            group["recognized_count"] += 1
    folder_summaries = list(grouped.values())
    start = (page - 1) * page_size
    return {"items": items[start:start + page_size], "folder_summaries": folder_summaries,
            "page": page, "page_size": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size, "filters": {"search": search, "path": path, "product_id": product_id, "file_type": file_type, "status": status, "decision": decision}}


def get_library_items(conn, drive_file_ids: Iterable[str]) -> list[dict]:
    return [_classify(row) for row in _raw_rows(conn, drive_file_ids=drive_file_ids, file_type="ALL")]


def _group_key(item: dict) -> str:
    return f"product:{item['product_id']}" if item["product_id"] is not None else f"folder:{item['folder_id']}"


def _matching_group_items(conn, *, search="", path="", product_id=None, file_type="VIDEO",
                          status="", decision="") -> list[dict]:
    items = [_classify(row) for row in _raw_rows(conn, search=search, path=path,
                                                 product_id=product_id, file_type=file_type)]
    status, decision = str(status or "").upper(), str(decision or "").upper()
    if status:
        items = [item for item in items if item["status"] == status]
    if decision:
        items = [item for item in items if (item["decision"] or "NONE") == decision]
    return list({item["drive_file_id"]: item for item in items}.values())


def _group_sort_key(group: dict) -> tuple:
    # Stable ID breaks ties between products with identical display names.
    category = 0 if group["requires_attention"] else (1 if group["product_id"] is not None else 2)
    return category, group["display_name"].casefold(), group["group_key"]


def list_library_groups(conn, *, page=1, page_size=20, search="", path="", product_id=None,
                        file_type="VIDEO", status="", decision="") -> dict:
    page, page_size = max(1, int(page or 1)), min(100, max(1, int(page_size or 20)))
    matching = _matching_group_items(conn, search=search, path=path, product_id=product_id,
                                     file_type=file_type, status=status, decision=decision)
    totals = _matching_group_items(conn, file_type=file_type) if any(
        (search, path, product_id not in (None, "", "ALL"), status, decision)) else matching
    total_counts = {}
    total_folders = {}
    for item in totals:
        key = _group_key(item)
        total_counts[key] = total_counts.get(key, 0) + 1
        total_folders.setdefault(key, {})[item["folder_id"]] = item["folder_path"] or item["folder_name"] or ""
    groups = {}
    for item in matching:
        key = _group_key(item)
        group = groups.setdefault(key, {
            "group_key": key, "group_type": "PRODUCT" if item["product_id"] is not None else "UNASSIGNED_FOLDER",
            "product_id": item["product_id"], "display_name": item["product_short_name"] or item["product_name"] or item["folder_name"] or "Pasta sem produto",
            "product_name": item["product_name"], "product_archived": item["product_archived"],
            "folder_ids": set(), "folder_paths": set(), "total_media_count": total_counts.get(key, 0),
            "matching_media_count": 0, "available_count": 0, "attention_count": 0, "reserved_count": 0,
            "has_default_caption": bool(str(item["caption"] or "").strip()),
            "requires_attention": False, "review_reason": None,
        })
        group["folder_ids"].add(item["folder_id"])
        group["folder_paths"].add(item["folder_path"] or item["folder_name"] or "")
        group["matching_media_count"] += 1
        if item["status"] == "AVAILABLE": group["available_count"] += 1
        elif item["status"] == "RESERVED": group["reserved_count"] += 1
        elif item["status"] == "ATTENTION": group["attention_count"] += 1
        if item["product_archived"]:
            group["requires_attention"], group["review_reason"] = True, "Produto arquivado: revisar antes de criar lote"
        elif item["product_id"] is None:
            group["requires_attention"], group["review_reason"] = True, "Produto da pasta ainda não associado"
        elif item["status"] == "ATTENTION" and not group["review_reason"]:
            group["requires_attention"], group["review_reason"] = True, item["reason"]
    ordered = sorted(groups.values(), key=_group_sort_key)
    for group in ordered:
        group["display_status"] = (group["review_reason"] or
            (f"{group['attention_count']} requerem atenção" if group["attention_count"] else
             "Pronto" if group["available_count"] else
             "Reservado" if group["reserved_count"] else "Sem vídeos prontos"))
        group["folder_ids"] = sorted(total_folders[group["group_key"]])
        group["folder_paths"] = sorted(set(total_folders[group["group_key"]].values()))
        group["folder_count"] = len(group["folder_ids"])
        group["representative_folder_path"] = group["folder_paths"][0] if group["folder_paths"] else ""
    start = (page - 1) * page_size
    return {"groups": ordered[start:start + page_size], "total_groups": len(ordered),
            "total_matching_media": len(matching), "page": page, "page_size": page_size,
            "pages": (len(ordered) + page_size - 1) // page_size}


def list_group_items(conn, group_key: str, *, page=1, page_size=50, **filters) -> dict:
    page, page_size = max(1, int(page or 1)), min(100, max(1, int(page_size or 50)))
    items = [item for item in _matching_group_items(conn, **filters) if _group_key(item) == group_key]
    start = (page - 1) * page_size
    return {"group_key": group_key, "items": items[start:start + page_size], "total": len(items),
            "page": page, "page_size": page_size, "pages": (len(items) + page_size - 1) // page_size}


def select_group_items(conn, group_key: str, *, selected_drive_file_ids=(), **filters) -> dict:
    selected = set(str(value) for value in selected_drive_file_ids)
    items = [item for item in _matching_group_items(conn, **filters)
             if _group_key(item) == group_key and item["eligible"] and not item["product_archived"]]
    if not items:
        raise ValueError("Nenhum vídeo pronto neste grupo")
    if len(selected | {item["drive_file_id"] for item in items}) > 100:
        raise ValueError(f"Este grupo possui {len(items)} vídeo(s) elegíveis; o lote aceita no máximo 100. Selecione individualmente até 100 vídeos.")
    return {"group_key": group_key, "items": items,
            "drive_file_ids": [item["drive_file_id"] for item in items],
            "media_asset_ids": [item["media_asset_id"] for item in items if item["media_asset_id"] is not None]}


def save_decision(conn, drive_file_id: str, decision: str, reason: str = "") -> dict:
    decision = str(decision or "").upper()
    if decision not in {"VALID", "IGNORED"}:
        raise ValueError("Decisão deve ser VALID ou IGNORED")
    if not _raw_rows(conn, drive_file_ids=[drive_file_id], file_type="ALL"):
        raise ValueError("Arquivo do Drive não encontrado no inventário")
    timestamp = now_iso()
    with conn:
        conn.execute("""INSERT INTO publisher_media_decisions(drive_file_id,decision,decided_at,reason,updated_at)
          VALUES (?,?,?,?,?) ON CONFLICT(drive_file_id) DO UPDATE SET decision=excluded.decision,
          decided_at=excluded.decided_at,reason=excluded.reason,updated_at=excluded.updated_at""",
          (str(drive_file_id), decision, timestamp, str(reason or "").strip(), timestamp))
    return {"drive_file_id": str(drive_file_id), "decision": decision, "decided_at": timestamp, "reason": reason or ""}


def set_product_overrides(conn, drive_file_ids: Iterable[str], product_id: int) -> dict:
    product = conn.execute("SELECT product_id id,name,default_caption FROM publisher_products WHERE product_id=? AND active=1", (int(product_id),)).fetchone()
    if not product:
        raise ValueError("Produto ativo não encontrado")
    ids = list(dict.fromkeys(str(value) for value in drive_file_ids if str(value).strip()))
    rows = get_library_items(conn, ids)
    found = {row["drive_file_id"] for row in rows}
    if set(ids) != found:
        raise ValueError("Um ou mais arquivos não existem no inventário")
    timestamp = now_iso()
    with conn:
        for file_id in ids:
            conn.execute("""INSERT INTO publisher_media_overrides(drive_file_id,product_id,updated_at,reason)
              VALUES (?,?,?,?) ON CONFLICT(drive_file_id) DO UPDATE SET product_id=excluded.product_id,
              updated_at=excluded.updated_at,reason=excluded.reason""",
              (file_id, int(product_id), timestamp, "Atribuição manual na Biblioteca"))
    return {"drive_file_ids": ids, "product_id": int(product_id), "updated": len(ids)}
