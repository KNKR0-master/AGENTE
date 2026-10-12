"""Versioned, local-only headline configuration. No renderer or media IO."""

import hashlib
import json
import math
import re

from src.store import now_iso

RENDER_SPEC_VERSION = 1
# Editorial overlay, not caption: bound payload/layout work to 500 Unicode code
# points and eight explicit lines. H2 will separately validate glyphs and layout.
MAX_TEXT_LENGTH = 500
MAX_LINES = 8
DEFAULT_SPEC = dict(font_ref='', font_sha256='', font_face_index=0,
                    font_size_ratio=0.05, font_weight=400, text_color='#FFFFFF',
                    alignment='center', x=0.5, y=0.15, margin_x=0.05, margin_y=0.05,
                    max_width=0.9, max_lines=3, wrapping='word', background_color=None,
                    background_padding_ratio=0.01, background_radius_ratio=0.0,
                    background_per_line=False, stroke_color=None, stroke_width_ratio=0.002,
                    shadow_color=None, shadow_offset_x=0.002, shadow_offset_y=0.002,
                    shadow_blur_ratio=0.004, max_block_height_ratio=1.0, safe_bottom_ratio=1.0)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def init_headlines(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS publisher_headline_presets (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1,
      current_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_headline_preset_versions (
      preset_id INTEGER NOT NULL REFERENCES publisher_headline_presets(id),
      version INTEGER NOT NULL, spec_json TEXT NOT NULL, created_at TEXT NOT NULL,
      PRIMARY KEY(preset_id,version));
    CREATE TRIGGER IF NOT EXISTS headline_version_no_update
      BEFORE UPDATE ON publisher_headline_preset_versions
      BEGIN SELECT RAISE(ABORT,'HEADLINE_VERSION_IMMUTABLE'); END;
    CREATE TRIGGER IF NOT EXISTS headline_version_no_delete
      BEFORE DELETE ON publisher_headline_preset_versions
      BEGIN SELECT RAISE(ABORT,'HEADLINE_VERSION_IMMUTABLE'); END;
    """)
    for table, definitions in {
        'publisher_products': {
            'default_headline_enabled': 'INTEGER NOT NULL DEFAULT 0',
            'default_headline_text': "TEXT NOT NULL DEFAULT ''",
            'default_headline_preset_id': 'INTEGER REFERENCES publisher_headline_presets(id)',
            'headline_required': 'INTEGER NOT NULL DEFAULT 0'},
        'publisher_drafts': {'headline_snapshot_json': 'TEXT'},
    }.items():
        columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
        for name, definition in definitions.items():
            if name not in columns:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    conn.execute("INSERT OR IGNORE INTO publisher_migrations VALUES ('headline_contract_v1',?)", (now_iso(),))
    from src.publisher.headline_workflow import init_workflow
    init_workflow(conn)


def boolean(value):
    if type(value) not in (bool, int) or value not in (0, 1):
        raise ValueError('HEADLINE_BOOLEAN_INVALID')
    return bool(value)


def text_value(value):
    if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH or '\x00' in value:
        raise ValueError('HEADLINE_TEXT_INVALID: limite de 500 caracteres')
    if len(value.split('\n')) > MAX_LINES:
        raise ValueError('HEADLINE_LINES_INVALID: limite de 8 linhas')
    return value


def validate_spec(data):
    if not isinstance(data, dict) or set(data) - set(DEFAULT_SPEC):
        raise ValueError('HEADLINE_SPEC_INVALID')
    spec = {**DEFAULT_SPEC, **data}
    if not isinstance(spec['font_ref'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', spec['font_ref']):
        raise ValueError('HEADLINE_FONT_REFERENCE_INVALID')
    if not isinstance(spec['font_sha256'], str) or not re.fullmatch(r'[0-9a-fA-F]{64}', spec['font_sha256']):
        raise ValueError('HEADLINE_FONT_HASH_REQUIRED')
    spec['font_sha256'] = spec['font_sha256'].lower()
    for key, low, high in [('font_face_index', 0, 255), ('font_weight', 100, 900), ('max_lines', 1, MAX_LINES)]:
        if type(spec[key]) is not int or not low <= spec[key] <= high:
            raise ValueError(f'HEADLINE_{key.upper()}_INVALID')
    ranges = {'font_size_ratio': (0.005, 0.3), 'x': (0, 1), 'y': (0, 1),
              'margin_x': (0, 0.45), 'margin_y': (0, 0.45), 'max_width': (0.01, 1),
              'background_padding_ratio': (0, 0.1), 'stroke_width_ratio': (0, 0.05),
              'background_radius_ratio': (0, 0.05),
              'shadow_offset_x': (-0.1, 0.1), 'shadow_offset_y': (-0.1, 0.1),
              'shadow_blur_ratio': (0, 0.1), 'max_block_height_ratio': (0.01, 1), 'safe_bottom_ratio': (0.01, 1)}
    for key, (low, high) in ranges.items():
        value = spec[key]
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'HEADLINE_{key.upper()}_INVALID')
        spec[key] = float(value)
    if spec['max_width'] > 1 - 2 * spec['margin_x'] + 1e-9:
        raise ValueError('HEADLINE_SAFE_WIDTH_INVALID')
    if not spec['margin_x'] <= spec['x'] <= 1-spec['margin_x'] or not spec['margin_y'] <= spec['y'] <= 1-spec['margin_y']:
        raise ValueError('HEADLINE_SAFE_POSITION_INVALID')
    for key in ('text_color', 'background_color', 'stroke_color', 'shadow_color'):
        if spec[key] is None and key != 'text_color':
            continue
        if not isinstance(spec[key], str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', spec[key]):
            raise ValueError('HEADLINE_COLOR_INVALID')
        spec[key] = spec[key].upper()
    if spec['alignment'] not in ('left', 'center', 'right') or spec['wrapping'] not in ('word', 'explicit'):
        raise ValueError('HEADLINE_LAYOUT_INVALID')
    spec['background_per_line'] = boolean(spec['background_per_line'])
    return spec


def list_presets(conn):
    return [{**dict(r), 'spec': json.loads(r['spec_json'])} for r in conn.execute(
        'SELECT p.*,v.spec_json FROM publisher_headline_presets p JOIN publisher_headline_preset_versions v '
        'ON v.preset_id=p.id AND v.version=p.current_version ORDER BY p.id')]


def get_preset(conn, preset_id):
    try:
        if isinstance(preset_id, (bool, float)):
            raise ValueError('HEADLINE_PRESET_INVALID')
        preset_id = int(preset_id)
    except (TypeError, ValueError) as exc:
        raise ValueError('HEADLINE_PRESET_INVALID') from exc
    preset = next((p for p in list_presets(conn) if p['id'] == preset_id), None)
    if not preset or not preset['active']:
        raise ValueError('HEADLINE_PRESET_INVALID')
    return {'id': preset['id'], 'version': preset['current_version'], 'name': preset['name'], 'spec': preset['spec']}


def save_preset(conn, data):
    name = str(data.get('name') or '').strip()
    if not name or len(name) > 120:
        raise ValueError('HEADLINE_PRESET_NAME_INVALID')
    spec = validate_spec(data.get('spec'))
    active = boolean(data.get('active', True))
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        timestamp = now_iso()
        preset_id = data.get('id')
        if preset_id:
            row = conn.execute('SELECT * FROM publisher_headline_presets WHERE id=?', (int(preset_id),)).fetchone()
            if not row or data.get('expected_version') != row['current_version']:
                raise ValueError('HEADLINE_PRESET_VERSION_CONFLICT')
            version = row['current_version'] + 1
            conn.execute('UPDATE publisher_headline_presets SET name=?,active=?,current_version=?,updated_at=? WHERE id=?',
                         (name, active, version, timestamp, preset_id))
        else:
            version = 1
            preset_id = conn.execute('INSERT INTO publisher_headline_presets(name,active,current_version,created_at,updated_at) VALUES (?,?,?,?,?)',
                                     (name, active, version, timestamp, timestamp)).lastrowid
        conn.execute('INSERT INTO publisher_headline_preset_versions VALUES (?,?,?,?)',
                     (preset_id, version, canonical(spec), timestamp))
    return {'id': int(preset_id), 'version': version}


def product_config(conn, data):
    enabled = boolean(data.get('default_headline_enabled', False))
    required = boolean(data.get('headline_required', False))
    text = text_value(data.get('default_headline_text') or '')
    preset_id = data.get('default_headline_preset_id') or None
    if preset_id:
        get_preset(conn, preset_id)
        preset_id = int(preset_id)
    return enabled, text, preset_id, required


def render_hash(snapshot):
    pixels = {'render_spec_version': snapshot['render_spec_version'], 'enabled': bool(snapshot['enabled'])}
    if pixels['enabled']:
        spec = dict((snapshot.get('preset') or {}).get('spec') or {})
        spec.pop('font_ref', None)  # Asset bytes + face identify the font, not its local alias.
        for color, fields in {
            'background_color': ('background_padding_ratio', 'background_radius_ratio', 'background_per_line'),
            'stroke_color': ('stroke_width_ratio',),
            'shadow_color': ('shadow_offset_x', 'shadow_offset_y', 'shadow_blur_ratio'),
        }.items():
            if spec.get(color) is None:
                for field in fields:
                    spec.pop(field, None)
        pixels.update(text=snapshot['text'], spec=spec)
    return hashlib.sha256(canonical(pixels).encode('utf-8')).hexdigest()


def make_snapshot(conn, product, override=None):
    enabled, text, preset_id, required = product_config(conn, product)
    if override is not None:
        if set(override) - {'enabled', 'text', 'preset_id'}:
            raise ValueError('HEADLINE_OVERRIDE_FIELDS_INVALID')
        enabled = boolean(override.get('enabled', enabled))
        text = text_value(override.get('text', text))
        preset_id = override.get('preset_id', preset_id) or None
        if required and not enabled:
            raise ValueError('HEADLINE_REQUIRED')
    snapshot = dict(render_spec_version=RENDER_SPEC_VERSION, enabled=enabled, required=required,
                    text=text, preset=get_preset(conn, preset_id) if enabled and preset_id else None,
                    provenance='DRAFT_OVERRIDE' if override is not None else 'PRODUCT_DEFAULT' if enabled or required else 'DISABLED')
    snapshot['render_spec_hash'] = render_hash(snapshot)
    return snapshot


def read_snapshot(draft):
    raw = draft.get('headline_snapshot_json')
    if raw:
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError('HEADLINE_CONFIG_INVALID')
            return result
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError('HEADLINE_CONFIG_INVALID') from exc
    result = dict(render_spec_version=1, enabled=False, required=False, text='', preset=None, provenance='DISABLED')
    result['render_spec_hash'] = render_hash(result)
    return result


def readiness(snapshot):
    try:
        if snapshot['render_spec_version'] != RENDER_SPEC_VERSION or snapshot['render_spec_hash'] != render_hash(snapshot):
            return ['HEADLINE_CONFIG_INVALID']
        boolean(snapshot['enabled'])
        boolean(snapshot['required'])
    except (KeyError, TypeError, ValueError):
        return ['HEADLINE_CONFIG_INVALID']
    if not snapshot['enabled']:
        return ['HEADLINE_REQUIRED'] if snapshot['required'] else []
    if not snapshot['text'].strip() or not snapshot.get('preset'):
        return ['HEADLINE_CONFIG_INCOMPLETE']
    try:
        text_value(snapshot['text'])
        spec = validate_spec(snapshot['preset']['spec'])
        if len(snapshot['text'].split('\n')) > spec['max_lines']:
            return ['HEADLINE_LINES_INVALID']
    except (ValueError, KeyError, TypeError):
        return ['HEADLINE_CONFIG_INVALID']
    return []


def capture(conn, draft_id, product, selection=None):
    from src.publisher.headline_workflow import selection_snapshot
    snapshot = selection_snapshot(conn, product, selection)
    conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=? WHERE id=?', (canonical(snapshot), draft_id))
    return snapshot
