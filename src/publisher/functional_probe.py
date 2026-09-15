"""Historical probe, retained for read-only inspection only."""
from src.publisher.diagnostics import safe_message, safe_url


def run_probe(worker, conn, operation, data):
    if operation not in {'inspect', 'evidence'}:
        raise ValueError('PROBE_MUTATIONS_RETIRED_USE_PRODUCTION_WORKER')
    session = worker.login_sessions.get(1)
    if not session or not session.page:
        raise ValueError('NO_ACTIVE_CONTEXT')
    page = session.page
    if operation == 'evidence':
        path = worker.root / 'data/publisher/evidence/draft1-readonly.png'
        page.screenshot(path=str(path), full_page=False)
        return {'evidence': str(path)}
    tree = page.locator('body').aria_snapshot(timeout=3000)
    return {'url': safe_url(page.url),
            'tree': '\n'.join(safe_message(line) for line in tree.splitlines())[:14000]}
