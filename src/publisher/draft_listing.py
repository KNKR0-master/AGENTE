"""Current Studio observations, never historical absence or retry authority."""

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
  const empty = [...document.querySelectorAll('[data-tt="components_DraftTable_FlexColumn"]')].filter(visible).some(el => {
    const labels = [...el.children].filter(child => child.matches('[data-tt="components_DraftTable_TUXText"]') && visible(child)).map(child => fold(text(child)));
    return labels.some(s => /^(ainda nao ha rascunhos|no drafts yet)$/.test(s)) &&
      labels.some(s => /^(seus rascunhos aparecerao aqui|your drafts will appear here)/.test(s));
  });
  const body = fold(text(document.body));
  return {rows, total: tab ? Number(text(tab).match(/\d+/)[0]) : null,
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
        not listing.get('loading') and not listing.get('error') and not listing.get('pagination') and
        (listing.get('empty_state') is True if total == 0 else
         all(row.get('caption_text') and row.get('updated_text') for row in rows))
    )
    return {
        'rows': rows, 'listing_complete': complete,
        'blocker_code': 'LISTING_ERROR' if listing.get('error') else (None if complete else 'PARTIAL_LISTING'),
        'listing_bounds': {
            'visible_rows': len(rows), 'tab_total': total,
            **{key: bool(listing.get(key)) for key in
               ('draft_tab_present', 'list_surface', 'empty_state', 'loading', 'error', 'pagination')},
        },
    }


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
