from __future__ import annotations

import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHEET_NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
REL_NS = {"rel": "http://schemas.openxmlformats.org/package/2006/relationships"}


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: list[list[Any]]


def _column_index(cell_ref: str) -> int:
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + ord(char) - 64
    return value - 1


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []

    values: list[str] = []
    for item in root.findall("a:si", SHEET_NS):
        values.append("".join(node.text or "" for node in item.findall(".//a:t", SHEET_NS)))
    return values


def _sheet_paths(archive: zipfile.ZipFile) -> list[tuple[str, str]]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    relmap = {
        rel.attrib["Id"]: rel.attrib["Target"]
        for rel in rels.findall("rel:Relationship", REL_NS)
    }

    paths: list[tuple[str, str]] = []
    for sheet in workbook.findall("a:sheets/a:sheet", SHEET_NS):
        rel_id = sheet.attrib.get(
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        )
        target = relmap.get(rel_id or "")
        if not target:
            continue
        if not target.startswith("xl/"):
            target = "xl/" + target.lstrip("/")
        paths.append((sheet.attrib.get("name", "Sheet"), target))
    return paths


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> Any:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//a:t", SHEET_NS))

    value = cell.find("a:v", SHEET_NS)
    if value is None:
        return ""

    raw = value.text or ""
    if cell_type == "s":
        try:
            return shared_strings[int(raw)]
        except (ValueError, IndexError):
            return raw
    if cell_type == "b":
        return raw == "1"
    return raw


def read_xlsx(path: str | Path) -> dict[str, Sheet]:
    file_path = Path(path)
    with zipfile.ZipFile(file_path) as archive:
        shared_strings = _read_shared_strings(archive)
        sheets: dict[str, Sheet] = {}

        for name, sheet_path in _sheet_paths(archive):
            root = ET.fromstring(archive.read(sheet_path))
            rows: list[list[Any]] = []

            for row in root.findall("a:sheetData/a:row", SHEET_NS):
                values: list[Any] = []
                for cell in row.findall("a:c", SHEET_NS):
                    index = _column_index(cell.attrib.get("r", ""))
                    while len(values) <= index:
                        values.append("")
                    values[index] = _cell_value(cell, shared_strings)
                rows.append(values)

            sheets[name] = Sheet(name=name, rows=rows)

    return sheets


def to_float(value: Any) -> float:
    text = str(value or "").strip()
    text = text.replace("R$", "").replace("%", "").replace(">", "")
    text = text.replace(".", "").replace(",", ".") if "," in text else text
    if text in {"", "-", "nan", "None"}:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def row_value(row: list[Any], index: int) -> Any:
    return row[index] if index < len(row) else ""


def parse_video_export(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sheets = read_xlsx(path)
    intro = _parse_intro(sheets)
    rows = sheets.get("LIST_VIDEO", Sheet("LIST_VIDEO", [])).rows
    data_rows = rows[1:] if rows else []

    records: list[dict[str, Any]] = []
    for row in data_rows:
        if not any(str(value).strip() for value in row):
            continue
        records.append(
            {
                "date_range": row_value(row, 0),
                "description": row_value(row, 1),
                "duration": row_value(row, 2),
                "creator_username": row_value(row, 3),
                "published_at": row_value(row, 4),
                "revenue": to_float(row_value(row, 5)),
                "sales": to_float(row_value(row, 6)),
                "product_title": row_value(row, 7),
                "product_category": row_value(row, 8),
                "views": to_float(row_value(row, 9)),
                "gpm": to_float(row_value(row, 10)),
                "cpa": to_float(row_value(row, 11)),
                "ads_view_ratio": row_value(row, 12),
                "ad_cost": to_float(row_value(row, 13)),
                "roas": to_float(row_value(row, 14)),
                "kalodata_url": row_value(row, 15),
                "tiktok_url": row_value(row, 16),
            }
        )
    return intro, records


def parse_creator_export(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sheets = read_xlsx(path)
    intro = _parse_intro(sheets)
    rows = sheets.get("LIST_CREATOR", Sheet("LIST_CREATOR", [])).rows
    data_rows = rows[1:] if rows else []

    records: list[dict[str, Any]] = []
    for row in data_rows:
        if not any(str(value).strip() for value in row):
            continue
        records.append(
            {
                "date_range": row_value(row, 0),
                "creator_name": row_value(row, 1),
                "creator_username": row_value(row, 2),
                "followers": to_float(row_value(row, 3)),
                "revenue": to_float(row_value(row, 4)),
                "sales": to_float(row_value(row, 5)),
                "avg_price": to_float(row_value(row, 6)),
                "engagement_rate": row_value(row, 7),
                "new_followers": to_float(row_value(row, 8)),
                "product_count": to_float(row_value(row, 9)),
                "live_count": to_float(row_value(row, 10)),
                "live_gmv": to_float(row_value(row, 11)),
                "video_count": to_float(row_value(row, 12)),
                "video_gmv": to_float(row_value(row, 13)),
                "views": to_float(row_value(row, 14)),
                "creator_first_seen": row_value(row, 15),
                "kalodata_url": row_value(row, 16),
                "tiktok_url": row_value(row, 17),
            }
        )
    return intro, records


def _parse_intro(sheets: dict[str, Sheet]) -> dict[str, Any]:
    intro = sheets.get("Intro")
    if not intro or len(intro.rows) < 2:
        return {}

    headers = [str(value).strip() for value in intro.rows[0]]
    values = intro.rows[1]
    return {
        headers[index]: row_value(values, index)
        for index in range(len(headers))
        if headers[index]
    }
