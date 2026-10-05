"""Reversible local visibility metadata; never a Draft lifecycle transition."""

from src.store import now_iso


TARGET_TABLES = {'DRAFT': 'publisher_drafts', 'BATCH': 'publisher_batches',
                 'EXECUTION': 'publisher_batch_executions'}
HIDDEN_ITEM_SQL = """EXISTS (
    SELECT 1 FROM publisher_queue_presentation_state q
    WHERE (q.target_type='DRAFT' AND q.target_id=d.id)
       OR (q.target_type='BATCH' AND q.target_id=d.batch_id)
       OR (q.target_type='EXECUTION' AND q.target_id=e.id))"""


def init_queue_presentation(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS publisher_queue_presentation_state (
        id INTEGER PRIMARY KEY,
        target_type TEXT NOT NULL CHECK(target_type IN ('DRAFT','BATCH','EXECUTION')),
        target_id INTEGER NOT NULL CHECK(target_id > 0),
        hidden_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(target_type,target_id))""")


def change_visibility(conn, action, target_type, target_id):
    if action not in ('hide', 'restore'):
        raise ValueError('INVALID_QUEUE_PRESENTATION_ACTION')
    if not isinstance(target_type, str) or target_type not in TARGET_TABLES:
        raise ValueError('UNSUPPORTED_QUEUE_PRESENTATION_TARGET')
    if isinstance(target_id, bool) or not isinstance(target_id, int) or target_id <= 0:
        raise ValueError('INVALID_QUEUE_PRESENTATION_TARGET_ID')
    table = TARGET_TABLES[target_type]
    with conn:
        if not conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (target_id,)).fetchone():
            raise ValueError('QUEUE_PRESENTATION_TARGET_NOT_FOUND')
        if action == 'hide':
            timestamp = now_iso()
            changed = conn.execute("""INSERT OR IGNORE INTO publisher_queue_presentation_state
                (target_type,target_id,hidden_at,created_at,updated_at) VALUES (?,?,?,?,?)""",
                (target_type, target_id, timestamp, timestamp, timestamp)).rowcount
        else:
            changed = conn.execute('DELETE FROM publisher_queue_presentation_state WHERE target_type=? AND target_id=?',
                                   (target_type, target_id)).rowcount
    return {'target_type': target_type, 'target_id': target_id, 'hidden': action == 'hide', 'changed': bool(changed)}


def presentation_targets(conn):
    targets, orphans = [], []
    for row in conn.execute('SELECT target_type,target_id,hidden_at FROM publisher_queue_presentation_state ORDER BY id'):
        item = dict(row)
        table = TARGET_TABLES.get(item['target_type'])
        if table and conn.execute(f'SELECT 1 FROM {table} WHERE id=?', (item['target_id'],)).fetchone():
            targets.append(item)
        else:
            orphans.append(item)
    return {'targets': targets, 'orphan_targets': orphans}


def history_visibility_counts(conn):
    row = conn.execute(f"""SELECT COUNT(*) total_items,
        COALESCE(SUM(CASE WHEN {HIDDEN_ITEM_SQL} THEN 1 ELSE 0 END),0) hidden_items
        FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id
        JOIN publisher_batch_executions e ON e.id=i.execution_id
        WHERE e.status IN ('COMPLETED','SUCCESS')""").fetchone()
    return {'total_items': row['total_items'], 'hidden_items': row['hidden_items'],
            'visible_items': row['total_items'] - row['hidden_items']}
