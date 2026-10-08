"""Saved text and controlled H1 visual variants, copied into Draft snapshots."""
from src.publisher import headlines
from src.store import now_iso

STYLES = {'WHITE': 'Classic · Branco', 'YELLOW': 'Classic · Amarelo',
          'BLACK_BACKGROUND': 'Classic · Fundo preto', 'OUTLINE': 'Classic · Branco com contorno'}
SIZES = {'SMALL': ('Pequeno', .026), 'STANDARD': ('Padrão', .034), 'LARGE': ('Grande', .042)}


def init_workflow(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS publisher_headline_templates (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, text TEXT NOT NULL,
      product_id INTEGER REFERENCES publisher_products(product_id),
      active INTEGER NOT NULL DEFAULT 1, sort_order INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_headline_style_variants (
      style_key TEXT NOT NULL, size_key TEXT NOT NULL,
      preset_id INTEGER NOT NULL REFERENCES publisher_headline_presets(id),
      PRIMARY KEY(style_key,size_key));
    ''')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_products)')}
    for name, definition in {
        'default_headline_template_id': 'INTEGER REFERENCES publisher_headline_templates(id)',
        'default_headline_size': "TEXT NOT NULL DEFAULT 'STANDARD'",
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_products ADD COLUMN {name} {definition}')
    conn.execute("INSERT OR IGNORE INTO publisher_migrations VALUES ('headline_workflow_v1',?)", (now_iso(),))


def list_templates(conn, *, product_id=None, search='', active=None):
    rows = [dict(r) for r in conn.execute('SELECT * FROM publisher_headline_templates ORDER BY sort_order,name,id')]
    return [r for r in rows if (product_id is None or r['product_id'] in (None, int(product_id)))
            and (active is None or bool(r['active']) == bool(active))
            and (not search or search.casefold() in (r['name']+' '+r['text']).casefold())]


def get_template(conn, template_id, product_id=None):
    try:
        template_id = int(template_id)
    except (ValueError,TypeError) as exc:
        raise ValueError('HEADLINE_TEMPLATE_NOT_FOUND') from exc
    row = conn.execute('SELECT * FROM publisher_headline_templates WHERE id=?', (template_id,)).fetchone()
    if not row or not row['active']:
        raise ValueError('HEADLINE_TEMPLATE_INACTIVE')
    if row['product_id'] is not None and row['product_id'] != product_id:
        raise ValueError('HEADLINE_TEMPLATE_PRODUCT_MISMATCH')
    return dict(row)


def save_template(conn, data):
    name = str(data.get('name') or '').strip()
    text = headlines.text_value(data.get('text') or '').strip()
    if not name or len(name) > 120 or not text:
        raise ValueError('HEADLINE_TEMPLATE_TEXT_REQUIRED')
    product_id = int(data['product_id']) if data.get('product_id') else None
    if product_id and not conn.execute('SELECT 1 FROM publisher_products WHERE product_id=?', (product_id,)).fetchone():
        raise ValueError('HEADLINE_TEMPLATE_PRODUCT_MISMATCH')
    active = headlines.boolean(data.get('active', True))
    order = int(data.get('sort_order', 0))
    template_id = int(data['id']) if data.get('id') else None
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if template_id and not conn.execute('SELECT 1 FROM publisher_headline_templates WHERE id=?', (template_id,)).fetchone():
            raise ValueError('HEADLINE_TEMPLATE_NOT_FOUND')
        for row in conn.execute('SELECT id,name,text FROM publisher_headline_templates WHERE product_id IS ?', (product_id,)):
            if row['id'] != template_id and (row['name'].casefold() == name.casefold() or row['text'].casefold() == text.casefold()):
                raise ValueError('HEADLINE_TEMPLATE_DUPLICATE')
        timestamp = now_iso()
        if template_id:
            conn.execute('UPDATE publisher_headline_templates SET name=?,text=?,product_id=?,active=?,sort_order=?,updated_at=? WHERE id=?',
                         (name,text,product_id,int(active),order,timestamp,template_id))
        else:
            template_id = conn.execute('INSERT INTO publisher_headline_templates(name,text,product_id,active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                                      (name,text,product_id,int(active),order,timestamp,timestamp)).lastrowid
    return {'id': template_id}


def style_catalog(conn):
    return [{**dict(r), 'label': STYLES[r['style_key']], 'size_label': SIZES[r['size_key']][0]}
            for r in conn.execute('SELECT * FROM publisher_headline_style_variants ORDER BY style_key,size_key')]


def initialize_styles(conn, root):
    from src.publisher.headline_configuration import machine_readiness
    ready = machine_readiness(root)
    if not ready['ready']:
        raise ValueError(ready['code'])
    font = ready['classic_font']
    for style, label in STYLES.items():
        for size, (size_label, ratio) in SIZES.items():
            if conn.execute('SELECT 1 FROM publisher_headline_style_variants WHERE style_key=? AND size_key=?', (style,size)).fetchone():
                continue
            spec = {**headlines.DEFAULT_SPEC, **font, 'font_size_ratio':ratio, 'max_lines':2,
                    'x':.46, 'y':.10, 'margin_x':.14, 'margin_y':.06, 'max_width':.62,
                    'max_block_height_ratio':.16, 'safe_bottom_ratio':.32,
                    'background_padding_ratio':.008, 'stroke_width_ratio':.0015}
            if style == 'YELLOW': spec['text_color'] = '#FFE500'
            if style == 'BLACK_BACKGROUND': spec['background_color'] = '#000000'
            if style == 'OUTLINE': spec['stroke_color'] = '#000000'
            preset = headlines.save_preset(conn, {'name':label+' · '+size_label, 'spec':spec})
            with conn:
                conn.execute('INSERT INTO publisher_headline_style_variants VALUES (?,?,?)', (style,size,preset['id']))
    return style_catalog(conn)


def variant(conn, preset_id, size):
    if size not in SIZES:
        raise ValueError('HEADLINE_SIZE_INVALID')
    if not preset_id:
        raise ValueError('HEADLINE_CONFIG_INCOMPLETE')
    row = conn.execute('SELECT style_key FROM publisher_headline_style_variants WHERE preset_id=?', (int(preset_id),)).fetchone()
    if not row:
        if size != 'STANDARD': raise ValueError('HEADLINE_SIZE_VARIANT_UNAVAILABLE')
        return int(preset_id)
    return conn.execute('SELECT preset_id FROM publisher_headline_style_variants WHERE style_key=? AND size_key=?', (row[0],size)).fetchone()[0]


def default_product(conn, product):
    result = dict(product)
    if not headlines.boolean(result.get('default_headline_enabled', False)) and not headlines.boolean(result.get('headline_required', False)):
        return result
    template_id = result.get('default_headline_template_id')
    if template_id:
        result['default_headline_text'] = get_template(conn, template_id, result.get('id', result.get('product_id')))['text']
    preset_id = result.get('default_headline_preset_id')
    if preset_id:
        result['default_headline_preset_id'] = variant(conn, preset_id, result.get('default_headline_size') or 'STANDARD')
    return result


def selection_snapshot(conn, product, selection=None):
    selection = {'mode':'none'} if selection is None else selection
    if not isinstance(selection, dict) or set(selection) - {'mode','template_id','text','preset_id','size'}:
        raise ValueError('HEADLINE_SELECTION_INVALID')
    mode = selection.get('mode', 'none')
    # Product configuration is a suggestion, not per-video authorization.
    product = {**product, 'headline_required': False}
    template = None
    if mode == 'product_default':
        effective = default_product(conn, {**product, 'default_headline_enabled': True})
        snapshot = headlines.make_snapshot(conn, effective)
        if snapshot['enabled'] and effective.get('default_headline_template_id'):
            template = get_template(conn, effective['default_headline_template_id'], effective.get('id', effective.get('product_id')))
    elif mode in ('none','template','custom'):
        text = selection.get('text', '')
        if mode == 'template':
            template = get_template(conn, selection.get('template_id'), product.get('id',product.get('product_id')))
            text = template['text']
        preset_id = None if mode == 'none' else selection.get('preset_id') or product.get('default_headline_preset_id')
        if preset_id:
            preset_id = variant(conn, preset_id, selection.get('size','STANDARD'))
        effective = {**product, 'default_headline_preset_id':None, 'default_headline_text':''} if mode == 'none' else product
        snapshot = headlines.make_snapshot(conn, effective, {'enabled':mode != 'none', 'text':text if mode != 'none' else '', 'preset_id':preset_id})
        if mode == 'none': snapshot['provenance'] = 'DISABLED'
    else:
        raise ValueError('HEADLINE_SELECTION_INVALID')
    if mode == 'product_default' and snapshot['enabled'] and (selection.get('preset_id') or selection.get('size')):
        preset_id = selection.get('preset_id') or (snapshot.get('preset') or {}).get('id')
        snapshot['preset'] = headlines.get_preset(conn, variant(conn, preset_id, selection.get('size','STANDARD')))
    snapshot['selection_mode'] = mode
    snapshot['size'] = selection.get('size') or product.get('default_headline_size') or 'STANDARD'
    if template:
        snapshot['template'] = {'id':template['id'], 'name':template['name']}
    snapshot['render_spec_hash'] = headlines.render_hash(snapshot)
    errors = headlines.readiness(snapshot)
    if errors: raise ValueError(errors[0])
    return snapshot
