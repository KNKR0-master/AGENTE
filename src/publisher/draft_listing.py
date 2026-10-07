"""Current Studio observations, never historical absence or retry authority."""

import re
from time import monotonic
from urllib.parse import urlsplit

LISTING_SNAPSHOT = r"""() => {
  const visible = el => !!el && el.getClientRects().length > 0;
  const text = el => (el?.innerText || '').trim();
  const fold = value => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const tabs = [...document.querySelectorAll('[data-tt="Header_HeaderTabBar_Container"]')].filter(visible);
  const tab = tabs.find(el => /^(Rascunhos|Drafts)\s+\d+$/i.test(text(el)));
  const rawRows = [...document.querySelectorAll('[data-tt="components_DraftRowLayout_FlexRow"]')].filter(visible);
  const rows = rawRows.filter(el => {
    const title = text(el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_2"]'));
    const updated = fold(text(el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_3"]')));
    return !(/^(Rascunhos|Drafts)$/i.test(title) && /^(ultima atualizacao|last updated)$/i.test(updated));
  }).map(el => {
    const lines = text(el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_2"]')).split(/\r?\n/).map(x => x.trim()).filter(Boolean);
    return {caption_text: lines.slice(1).join('\n'), duration_text: lines[0] || '',
      updated_text: text(el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_3"]'))};
  });
  const emptyLabels = [...document.querySelectorAll('[data-tt="components_DraftTable_FlexColumn"]')].filter(visible)
    .filter(el => !el.querySelector('[data-tt="components_DraftRowLayout_FlexRow"]'))
    .map(el => [...el.children].filter(child => child.matches('[data-tt="components_DraftTable_TUXText"]') && visible(child)).map(child => fold(text(child))))
    .filter(labels => labels.length === 2 && labels.every(Boolean));
  const empty = emptyLabels.length === 1;
  const emptyText = emptyLabels.some(labels => labels.some(s => /^(ainda nao ha rascunhos|no drafts yet)$/.test(s)) &&
    labels.some(s => /^(seus rascunhos aparecerao aqui|your drafts will appear here)/.test(s)));
  const body = fold(text(document.body));
  return {rows, total: tab ? Number(text(tab).match(/\d+/)[0]) : null,
    tab_labels: tabs.filter(el => /^(Rascunhos|Drafts)\s+\d+$/i.test(text(el))).map(text),
    raw_row_count: rawRows.length, header_row_count: rawRows.length - rows.length,
    empty_text_category: emptyText ? 'CANONICAL_EMPTY' : null,
    challenge: /captcha|verify you are human|verifique se voce e humano/.test(body) ? 'CAPTCHA' :
      /codigo de verificacao|verificacao em duas etapas|two-factor authentication/.test(body) ? 'TWO_FACTOR' :
      /security challenge|verificacao de seguranca/.test(body) ? 'SECURITY_CHALLENGE' : null,
    draft_tab_present: !!tab, empty_state: empty,
    list_surface: empty || /todos os computadores|all computers/.test(body),
    loading: [...document.querySelectorAll('[aria-busy="true"], [role="progressbar"], [data-e2e*="loading"], [data-tt*="Spinner"]')].some(visible),
    error: [...document.querySelectorAll('[role="alert"], [data-e2e*="error"]')].some(el => visible(el) && !!text(el)),
    pagination: [...document.querySelectorAll('[aria-label*="pr\u00f3xima" i], [aria-label*="next" i], [data-e2e*="pagination"]')].some(visible)};
}"""


def classify_listing(listing):
    rows = listing.get('rows') or []
    total = listing.get('total')
    complete = (
        type(total) is int and total == len(rows) and
        listing.get('draft_tab_present') is True and listing.get('list_surface') is True and
        not listing.get('loading') and not listing.get('error') and not listing.get('pagination') and not listing.get('challenge') and
        (listing.get('empty_state') is True if total == 0 else
         all(row.get('caption_text') and row.get('updated_text') for row in rows))
    )
    return {
        'rows': rows, 'listing_complete': complete,
        'blocker_code': listing.get('challenge') or ('LISTING_ERROR' if listing.get('error') else (None if complete else 'PARTIAL_LISTING')),
        'listing_bounds': {
            'visible_rows': len(rows), 'tab_total': total,
            **{key: bool(listing.get(key)) for key in
               ('draft_tab_present', 'list_surface', 'empty_state', 'loading', 'error', 'pagination')},
        },
    }


def read_hydrated_listing(page, *, max_samples=17, interval_ms=500):
    """One navigation, at most one tab selection, bounded passive hydration reads."""
    page.goto('https://www.tiktok.com/tiktokstudio/content', wait_until='domcontentloaded', timeout=30000)
    started = monotonic()
    samples = []
    tab_selected = False
    previous_complete = None
    result = {'rows': [], 'listing_complete': False, 'blocker_code': 'PARTIAL_LISTING', 'listing_bounds': {}}
    for attempt in range(max_samples):
        route = urlsplit(page.url)
        if route.hostname != 'www.tiktok.com' or route.path.rstrip('/') != '/tiktokstudio/content':
            from src.publisher.auth import is_login_url
            result.update(listing_complete=False, blocker_code='AUTH_REQUIRED' if is_login_url(page.url) else 'LISTING_ERROR')
            break
        listing = page.evaluate(LISTING_SNAPSHOT)
        result = classify_listing(listing)
        samples.append({'elapsed_ms': round((monotonic() - started) * 1000),
                        'complete': result['listing_complete'], 'blocker': result['blocker_code'],
                        **result['listing_bounds'],
                        'tab_labels': listing.get('tab_labels') or [],
                        'raw_row_count': listing.get('raw_row_count'),
                        'header_row_count': listing.get('header_row_count'),
                        'empty_text_category': listing.get('empty_text_category')})
        if result['blocker_code'] not in (None, 'PARTIAL_LISTING'):
            break
        signature = (listing.get('total'), listing.get('empty_state'), listing.get('rows'))
        if result['listing_complete'] and signature == previous_complete:
            break
        previous_complete = signature if result['listing_complete'] else None
        if attempt == max_samples - 1:
            # One transient complete sample is not stable enough to claim coverage.
            result.update(listing_complete=False, blocker_code='PARTIAL_LISTING')
            break
        if listing.get('draft_tab_present') and not listing.get('list_surface') and not tab_selected and not listing.get('loading'):
            tab = page.locator('[data-tt="Header_HeaderTabBar_Container"]').filter(
                has_text=re.compile(r'^(?:Rascunhos|Drafts)\s+\d+$', re.I))
            if tab.count() == 1 and tab.is_visible():
                tab.click(timeout=3000)
                tab_selected = True
        page.wait_for_timeout(interval_ms)
    route = urlsplit(page.url)
    result.update(page_identity={'host': route.hostname, 'path': route.path},
                  hydration_samples=samples, draft_tab_selected=tab_selected)
    return result


def sanitized_listing_probe(observation):
    """Diagnostic projection excludes captions, rows and any full-page content."""
    return {key: observation.get(key) for key in (
        'auth_result', 'account_identity', 'observed_account', 'source_surface',
        'listing_complete', 'blocker_code', 'listing_bounds', 'page_identity',
        'hydration_samples', 'draft_tab_selected',
    )} | {'current_draft_count': len(observation.get('rows') or []) if observation.get('listing_complete') else None,
         'remote_read_only': True, 'draft_evidence_written': False}


def current_listing_summary(evidence):
    import json
    payload = json.loads(evidence.get('evidence_payload_json') or '{}')
    observation = payload.get('current_listing') or {}
    complete = evidence['coverage_status'] == 'COMPLETE' and observation.get('complete') is True
    return {'listing_complete': complete,
            'current_draft_count': observation.get('count') if complete else None,
            'match_result': observation.get('match_result') if complete else 'UNKNOWN'}


def resolution_safety_summary(draft, checkpoints):
    steps = {row['step'] for row in checkpoints}
    started = 'UPLOAD_STARTED' in steps or draft.get('upload_status') not in (None, 'NOT_STARTED')
    no_start = (not started and draft.get('upload_status') == 'NOT_STARTED' and
                draft.get('processing_status') == 'NOT_STARTED' and
                not steps.intersection({'SAVE_DRAFT', 'TIKTOK_DRAFT_CONFIRMED', 'TRANSFER_CONFIRMED',
                                        'TRANSFER_RECONCILED_FROM_DOM', 'MEDIA_READY_FOR_EDIT'}))
    return {'local_upload_status': draft.get('upload_status'),
            'upload_started_evidence': 'UPLOAD_STARTED' in steps,
            'local_attempt': 'NOT_STARTED' if no_start else 'POSSIBLE_REMOTE_EFFECT',
            'duplicate_risk': 'LOW_APPARENT' if no_start else 'REMAINS',
            'automatic_release_allowed': False, 'automatic_retry_allowed': False}
