from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from time import monotonic
from concurrent.futures import Future, TimeoutError as FutureTimeout
from urllib.parse import urlsplit
from contextlib import closing
from pathlib import Path
from zoneinfo import ZoneInfo

from src.content_pipeline import file_sha256, probe_video, ValidationStatus
from src.media_tools import FFProbeError, ensure_ffprobe_available
from src.store import connect, now_iso
from src.publisher.service import PublisherService, get_row, log, transition, assert_upload_allowed
from src.publisher.media import media_provider, TemporaryMediaCache
from src.publisher.lifecycle import confirm_received, reconcile_received, cleanup_remote
from src.publisher.auth import (
    AUTHENTICATED, LOGIN_IN_PROGRESS, NEEDS_LOGIN, NOT_AUTHENTICATED,
    LOGIN_URL, STUDIO_URL, LoginSession, TikTokAuthDetector,
    is_login_url, is_studio_url, is_tiktok_url, persist_auth, recover_auth,
    evaluate_studio_snapshot,
)
from src.publisher.diagnostics import AuthDiagnostics, safe_error, safe_url
from src.publisher.login_trace import LoginTrace
from src.publisher.auth_test_lock import AuthTestLock
from src.publisher.manual_profile import ProfileOwnership
from src.publisher.browser_runtime import BrowserRuntimeManager
from src.publisher.reconciliation_evidence import (
    record_reconciliation_evidence,
)
from src.publisher.profile_paths import resolve_browser_profile
from src.publisher.account_safety import (
    AccountSafetyGuard, AccountProtectionBlocked, BLOCKED_BY_ACCOUNT_PROTECTION, map_risk_signal,
)


class PreparationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


_PT_MONTHS = {
    'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
    'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12,
}
_PT_DRAFT_TIMESTAMP = re.compile(
    r'^(?P<day>\d{1,2})\s+de\s+(?P<month>[A-Za-zÀ-ÿ]{3,})\s+de\s+'
    r'(?P<year>\d{4}),\s*(?P<hour>\d{1,2}):(?P<minute>\d{2})$'
)

_INSPECTION_ALLOWED_HOSTS = frozenset({'www.tiktok.com'})
_INSPECTION_BLOCKED_PATHS = frozenset({
    '/login/qrcode',
    '/login/qrconnect',
    '/passport/web/get_qrcode',
})


def inspection_navigation_decision(url, *, navigation_request, main_frame):
    """Return the least-privilege decision for read-only session inspection."""
    if not navigation_request or not main_frame:
        return {'blocked': False, 'reason': None, 'host': ''}
    parsed = urlsplit(str(url))
    host = (parsed.hostname or '').casefold()
    path = parsed.path.rstrip('/').casefold() or '/'
    if parsed.scheme == 'https' and host in _INSPECTION_ALLOWED_HOSTS:
        if path in _INSPECTION_BLOCKED_PATHS:
            return {'blocked': True, 'reason': 'TIKTOK_AUTH_QR_NAVIGATION', 'host': host}
        return {'blocked': False, 'reason': None, 'host': host}
    return {'blocked': True, 'reason': 'NON_TIKTOK_MAIN_FRAME', 'host': host}


def classify_inspection_target_error(exc, *, context=None, page=None):
    """Classify a closed Playwright target without treating it as auth loss."""
    if type(exc).__name__ != 'TargetClosedError':
        return None
    if page is not None:
        try:
            if page.is_closed():
                return 'STALE_PAGE_TARGET'
        except Exception:
            pass
    if context is not None:
        try:
            list(context.pages)
        except Exception:
            return 'CONTEXT_LOST'
        return 'STALE_PAGE_TARGET'
    return 'BROWSER_LOST'


def select_live_inspection_page(context, previous_page=None):
    """Select one live page, re-enumerating once when a prior page is stale."""
    if previous_page is not None:
        try:
            if not previous_page.is_closed():
                return previous_page
        except Exception as exc:
            if type(exc).__name__ != 'TargetClosedError':
                raise
    pages = list(context.pages)
    live_pages = []
    for page in pages:
        try:
            if not page.is_closed():
                live_pages.append(page)
        except Exception as exc:
            if type(exc).__name__ != 'TargetClosedError':
                raise
    if len(live_pages) > 1:
        raise ValueError('Mais de uma pagina restaurada; nenhuma navegacao efetuada')
    if live_pages:
        return live_pages[0]
    return context.new_page()


def _parse_checkpoint_time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_tiktok_draft_time(value):
    match = _PT_DRAFT_TIMESTAMP.match(' '.join(str(value or '').split()))
    if not match:
        return None
    month_text = match.group('month').casefold()[:3]
    month = _PT_MONTHS.get(month_text)
    if not month:
        return None
    try:
        return datetime(
            int(match.group('year')),
            month,
            int(match.group('day')),
            int(match.group('hour')),
            int(match.group('minute')),
            tzinfo=ZoneInfo('America/Sao_Paulo'),
        )
    except ValueError:
        return None


def _draft_duration_seconds(value):
    parts = str(value or '').strip().split(':')
    if len(parts) not in (2, 3) or any(not part.isdigit() for part in parts):
        return None
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def canonical_draft_candidates(rows, caption, reference_time=None, *,
                               expected_duration_seconds=None, require_strong=False):
    """The same row identity rules used by normal persistence and verification."""
    candidates = [row for row in rows if str(row.get('caption_text') or '').strip() == str(caption or '').strip()]
    expected = _parse_checkpoint_time(reference_time)
    if require_strong:
        if expected is None or expected_duration_seconds is None:
            return []
        expected_local = expected.astimezone(ZoneInfo('America/Sao_Paulo'))
        return [row for row in candidates if (
            (observed := _parse_tiktok_draft_time(row.get('updated_text'))) is not None
            and abs((observed - expected_local).total_seconds()) <= 120
            and (duration := _draft_duration_seconds(row.get('duration_text'))) is not None
            and abs(duration - expected_duration_seconds) <= 1.5
        )]
    if len(candidates) <= 1 or expected is None:
        return candidates
    expected_local = expected.astimezone(ZoneInfo('America/Sao_Paulo'))
    return [row for row in candidates if (
        (observed := _parse_tiktok_draft_time(row.get('updated_text'))) is not None
        and abs((observed - expected_local).total_seconds()) <= 120
    )]


def select_existing_draft_row(rows, caption, reference_time=None, *,
                              expected_duration_seconds=None, require_strong=False):
    """Select a persisted Studio row without relying on DOM position.

    A duplicated caption is accepted only when the visible update timestamp
    uniquely matches the persisted SAVE_DRAFT checkpoint. Otherwise this
    deliberately fails closed and requires manual reconciliation.
    """
    candidates = canonical_draft_candidates(
        rows, caption, reference_time,
        expected_duration_seconds=expected_duration_seconds,
        require_strong=require_strong,
    )
    if len(candidates) != 1:
        message = 'Associacao do rascunho ausente' if not candidates else 'Associacao do rascunho ambigua ou ausente'
        if require_strong and not candidates:
            message = 'Associacao do rascunho ambigua ou ausente'
        raise PreparationError('MANUAL_ACTION_REQUIRED', message)
    return candidates[0]


def assess_remote_draft_rows(rows, caption, save_checkpoint_at, expected_duration_seconds,
                             *, listing_complete=False):
    """Classify visible rows without treating no visible match as remote absence."""
    candidates = canonical_draft_candidates(
        rows, caption, save_checkpoint_at,
        expected_duration_seconds=expected_duration_seconds, require_strong=True,
    )
    coverage = 'COMPLETE' if listing_complete else 'PARTIAL'
    if not listing_complete or not save_checkpoint_at or expected_duration_seconds is None:
        return {'outcome': 'REMOTE_STATE_UNKNOWN', 'coverage_status': coverage,
                'blocker_code': 'PARTIAL_LISTING' if not listing_complete else 'MATCH_EVIDENCE_INSUFFICIENT',
                'candidate_count': len(candidates), 'matched_candidate_fingerprint': None}
    if len(candidates) > 1:
        return {'outcome': 'REMOTE_MATCH_AMBIGUOUS', 'coverage_status': coverage,
                'blocker_code': None, 'candidate_count': len(candidates),
                'matched_candidate_fingerprint': None}
    if len(candidates) == 1:
        matched = select_existing_draft_row(
            rows, caption, save_checkpoint_at,
            expected_duration_seconds=expected_duration_seconds, require_strong=True,
        )
        fingerprint = hashlib.sha256(json.dumps({
            'caption': matched['caption_text'], 'duration': matched['duration_text'],
            'updated': matched['updated_text'],
        }, sort_keys=True, ensure_ascii=True).encode('utf-8')).hexdigest()
        return {'outcome': 'REMOTE_EXISTS_CONFIRMED', 'coverage_status': coverage,
                'blocker_code': None, 'candidate_count': 1,
                'matched_candidate_fingerprint': f'sha256:{fingerprint}'}
    return {'outcome': 'REMOTE_STATE_UNKNOWN', 'coverage_status': 'PARTIAL',
            'blocker_code': 'MATCH_EVIDENCE_INSUFFICIENT', 'candidate_count': 0,
            'matched_candidate_fingerprint': None}


def restore_pre_upload_failure(conn, draft_id):
    """Return a failed item to a retryable state when TikTok was never touched."""
    draft = get_row(conn, 'publisher_drafts', draft_id)
    if (
        draft.get('status') == 'DRAFT' or
        draft.get('upload_status') != 'NOT_STARTED' or
        draft.get('confirmed_at') or
        draft.get('processing_status') != 'NOT_STARTED' or
        not draft.get('media_asset_id')
    ):
        return False
    if conn.execute(
        "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
        "AND step='UPLOAD_STARTED' AND status='OK' LIMIT 1",
        (draft_id,),
    ).fetchone():
        return False
    asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
    if asset.get('status') not in {'PROCESSING', 'FAILED', 'AVAILABLE'}:
        return False
    timestamp = now_iso()
    conn.execute(
        "UPDATE publisher_drafts SET status='DRAFT',error_code=NULL,updated_at=? WHERE id=?",
        (timestamp, draft_id),
    )
    conn.execute(
        "UPDATE publisher_media_assets SET status='AVAILABLE',consumed_by_draft_id=NULL,"
        "cleanup_error=NULL,updated_at=? WHERE id=? AND status IN ('PROCESSING','FAILED')",
        (timestamp, draft['media_asset_id']),
    )
    return True


class StudioInterruptionHandler:
    """Fail-closed handling for known, non-destructive Studio dialogs."""

    PREVIEW_INFO_TEXT = "Veja a prévia do seu vídeo no seu celular"
    PREVIEW_INFO_BUTTON = "Entendi"
    COPYRIGHT_SAVE_TITLE = "Salvar rascunho?"
    COPYRIGHT_SAVE_TEXT = (
        "A verificação de direitos autorais continua em andamento, e salvar esta publicação agora interromperá o processo."
    )
    COPYRIGHT_SAVE_BUTTON = "Salvar mesmo assim"
    BLOCKED_TERMS = (
        "publicar", "agendar", "confirmar publicação", "excluir", "remover",
        "login", "qr", "challenge", "segurança", "permiss", "produto",
    )

    def __init__(self, page):
        self.page = page
        self.dismissed_preview_info = False
        self.dismissed_copyright_save = False

    def _dialog(self):
        dialogs = self.page.locator('[role="alertdialog"], [role="dialog"]')
        count = dialogs.count()
        if count == 0:
            return None, ""
        if count != 1:
            raise PreparationError("MANUAL_ACTION_REQUIRED", "Mais de um modal do Studio esta aberto")
        dialog = dialogs
        text = dialog.inner_text(timeout=2000)
        return dialog, text

    def dismiss_allowed(self):
        dialog, text = self._dialog()
        if dialog is None:
            return False
        normalized = " ".join(text.split()).lower()
        if self.PREVIEW_INFO_TEXT.lower() in normalized:
            if self.dismissed_preview_info:
                return False
            button = dialog.get_by_role("button", name=self.PREVIEW_INFO_BUTTON, exact=True)
            if button.count() != 1 or not button.is_visible() or not button.is_enabled():
                raise PreparationError("MANUAL_ACTION_REQUIRED", "Popup informativo conhecido sem botao seguro unico")
            button.click(timeout=10000)
            self.dismissed_preview_info = True
            return True
        if (
            self.COPYRIGHT_SAVE_TITLE.lower() in normalized
            and self.COPYRIGHT_SAVE_TEXT.lower() in normalized
        ):
            if self.dismissed_copyright_save:
                return False
            button = dialog.get_by_role("button", name=self.COPYRIGHT_SAVE_BUTTON, exact=True)
            if button.count() != 1 or not button.is_visible() or not button.is_enabled():
                raise PreparationError(
                    "MANUAL_ACTION_REQUIRED",
                    "Modal de salvamento conhecido sem botao seguro unico",
                )
            button.click(timeout=10000)
            self.dismissed_copyright_save = True
            return True
        if any(term in normalized for term in self.BLOCKED_TERMS):
            raise PreparationError("MANUAL_ACTION_REQUIRED", "Modal do Studio exige decisao manual")
        raise PreparationError("MANUAL_ACTION_REQUIRED", "Modal do Studio nao esta na allowlist")


class StudioPage:
    """All site-specific selectors live in the mapping, and all actions fail closed."""
    def __init__(self, page, mapping, before_mutation=None, after_mutation=None, risk_observer=None):
        self.page, self.mapping = page, mapping
        self.before_mutation = before_mutation or (lambda operation: None)
        self.after_mutation = after_mutation or (lambda operation: None)
        self.risk_observer = risk_observer or (lambda text: None)
        self.interruptions = StudioInterruptionHandler(page)
        self.transfer_confirmed = False
        self.save_attempted = False

    def element(self, key):
        spec = self.mapping.get(key)
        if not spec:
            raise PreparationError("STUDIO_CHANGED", f"Seletor nao validado: {key}. Configure config/publisher_studio.json")
        if "role" in spec:
            return self.page.get_by_role(spec["role"], name=spec.get("name"), exact=True)
        return self.page.locator(spec["selector"])

    def challenge(self):
        text = self.page.locator("body").inner_text(timeout=5000).lower()
        self.risk_observer(text)
        if any(x in text for x in ("captcha", "verify you are human", "verifique se", "verification code", "codigo de verificacao")):
            raise PreparationError("MANUAL_ACTION_REQUIRED", "Resolva a verificacao no navegador e reexecute")
        if is_login_url(self.page.url):
            raise PreparationError("NEEDS_LOGIN", "Faca login manualmente no navegador da conta")

    def wait_for_single(self, locator, label, timeout=30):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.interruptions.dismiss_allowed()
            self.challenge()
            if locator.count() == 1:
                return locator
            self.page.wait_for_timeout(500)
        raise PreparationError("STUDIO_CHANGED", f"{label} ausente ou ambiguo")

    def prepare(self, draft, preset, path, evidence):
        self.page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")
        self.challenge()
        if not self.mapping.get("validated"):
            raise PreparationError("STUDIO_CHANGED", "Mapeamento de upload e persistencia ainda precisa de validacao real")
        check = {"file": True}
        upload = self.element("file_input")
        upload = self.wait_for_single(upload, 'Campo de arquivo')
        self.before_mutation('VIDEO_UPLOAD')
        evidence('UPLOAD_STARTED','Selecao unica do arquivo; qualquer falha exige reconciliacao')
        upload.set_input_files(str(path))
        self._set_upload_state(draft, 'TRANSFER_IN_PROGRESS', 'PROCESSING')
        media_state = self.wait_processed(draft, evidence)
        self.interruptions.dismiss_allowed()
        self.challenge()
        check["upload"] = True
        check["media_ready"] = media_state["media_ready_for_edit"]
        check['upload_confirmed_at'] = now_iso()
        evidence("UPLOAD_CONFIRMED", "Enviado + preview + verificacoes concluidas")
        self.after_mutation('VIDEO_UPLOAD')
        caption = self.element("caption")
        if caption.count()!=1:
            raise PreparationError('STUDIO_CHANGED','Campo de legenda ambiguo')
        caption.fill(draft["caption"])
        value = caption.evaluate("el => el.value ?? el.innerText")
        if value != draft["caption"]:
            raise PreparationError("FAILED", "Legenda nao corresponde ao draft")
        check["caption"] = True
        check['caption_applied_at'] = now_iso()
        evidence('CAPTION','Draft.caption aplicado e relido exatamente')
        # Draft-only preparation intentionally does not apply publication settings.
        self.challenge()
        check["settings"] = True
        check['settings_mode'] = 'UNCHANGED_MANUAL_REVIEW'
        if draft.get('media_asset_id'):
            check.update(self.confirm_durable(draft, evidence))
        return check

    def _set_upload_state(self, draft, upload_status, processing_status):
        draft['_upload_status'] = upload_status
        draft['_processing_status'] = processing_status

    def media_state(self):
        import re
        tree = self.page.locator('body').aria_snapshot(timeout=5000)
        text = self.page.locator('body').inner_text(timeout=5000)
        combined = f'{tree}\n{text}'
        transfer = bool(re.search(r'Enviado\s*[（(]', combined, re.I))
        preview = self.page.get_by_role('button', name='Substituir', exact=True).count() == 1
        duration = bool(re.search(r'Reproduzir\s+\d\d:\d\d:\d\d\s*/\s*\d\d:\d\d:\d\d', combined))
        caption = self.element('caption')
        caption_ready = caption.count() == 1
        try:
            caption_ready = caption_ready and caption.is_editable()
        except Exception:
            pass
        blocking_terms = ('enviando', 'processando', 'carregando vídeo', 'aguarde o upload')
        processing_blocked = any(term in combined.lower() for term in blocking_terms)
        return {
            'transfer_confirmed': transfer,
            'preview_ready': preview,
            'duration_ready': duration,
            'caption_field_ready': caption_ready,
            'processing_blocked': processing_blocked,
            'media_ready_for_edit': transfer and preview and duration and caption_ready and not processing_blocked,
        }

    def inspect_current_upload(self, expected_filename):
        """Read-only reconciliation for an upload that may have completed after a local timeout."""
        body = self.page.locator('body').inner_text(timeout=5000)
        observed_filenames = sorted(set(re.findall(
            r'(?<![^\s/])[^\r\n<>]{1,240}\.mp4\b', body, re.I
        )))
        observed_filenames = [" ".join(value.split()) for value in observed_filenames]
        identity_confirmed = observed_filenames == [str(expected_filename).strip()]
        if not identity_confirmed:
            raise PreparationError(
                'MEDIA_IDENTITY_MISMATCH',
                f'Midia atual divergente: esperada={expected_filename}; observada={observed_filenames}',
            )

        state = self.media_state()
        sent_marker = bool(re.search(r'Enviado\s*[\uff08(]', body, re.I))
        size_match = re.search(r'Enviado\s*[\uff08(][^\)\uff09]+[\)\uff09]', body, re.I)
        state.update({
            'current_media_filename': observed_filenames[0],
            'media_identity_confirmed': True,
            'upload_sent_marker_present': sent_marker,
            'upload_size_text': size_match.group(0) if size_match else None,
            'transfer_confirmed': sent_marker,
            'caption_field_value': None,
            'copyright_check_status': [
                line.strip() for line in body.splitlines()
                if re.search(r'direitos autorais|copyright', line, re.I)
            ],
            'content_check_status': [
                line.strip() for line in body.splitlines()
                if re.search(r'nenhum problema|verifica.{0,30}(conclu|andamento)', line, re.I)
            ],
        })
        caption = self.element('caption')
        if caption.count() == 1:
            state['caption_field_value'] = caption.evaluate("el => el.value ?? el.innerText")
        state['media_ready_for_edit'] = bool(
            identity_confirmed and sent_marker and state.get('preview_ready') and
            state.get('caption_field_ready') and not state.get('processing_blocked')
        )
        return state

    def continue_current_upload(self, draft, evidence):
        """Continue an already visible upload without selecting another file."""
        state = self.inspect_current_upload(draft['_expected_filename'])
        if not state['media_ready_for_edit']:
            raise PreparationError('MANUAL_ACTION_REQUIRED', 'Upload atual ainda nao esta pronto para edicao')
        self._set_upload_state(draft, 'TRANSFER_CONFIRMED', 'MEDIA_READY_FOR_EDIT')
        caption = self.element('caption')
        if caption.count() != 1 or not caption.is_editable():
            raise PreparationError('STUDIO_CHANGED', 'Campo de legenda ausente ou nao editavel')
        caption.fill(draft['caption'])
        value = caption.evaluate("el => el.value ?? el.innerText")
        if value != draft['caption']:
            raise PreparationError('FAILED', 'Legenda nao corresponde ao draft')
        evidence('CAPTION', 'Draft.caption aplicado apos reconciliacao DOM e relido exatamente')
        checks = {
            'file': True,
            'upload': True,
            'media_ready': True,
            'caption': True,
            'settings': True,
            'settings_mode': 'UNCHANGED_MANUAL_REVIEW',
            'caption_applied_at': now_iso(),
        }
        checks.update(self.confirm_durable(draft, evidence))
        return checks

    def wait_processed(self, draft=None, evidence=None, timeout=180):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.interruptions.dismiss_allowed()
            self.challenge()
            state = self.media_state()
            if state['transfer_confirmed'] and not self.transfer_confirmed:
                self.transfer_confirmed = True
                self._set_upload_state(draft if draft is not None else {}, 'TRANSFER_CONFIRMED', 'PROCESSING')
                if evidence:
                    evidence('TRANSFER_CONFIRMED', 'TikTok exibiu Enviado (...); transferencia confirmada')
            if state['media_ready_for_edit']:
                self._set_upload_state(draft if draft is not None else {}, 'TRANSFER_CONFIRMED', 'MEDIA_READY_FOR_EDIT')
                if evidence:
                    evidence('MEDIA_READY_FOR_EDIT', 'Preview/capa, duracao e campo de descricao prontos; sem processamento bloqueante')
                return state
            self.page.wait_for_timeout(500)
        if self.transfer_confirmed:
            self._set_upload_state(draft if draft is not None else {}, 'TRANSFER_CONFIRMED', 'PROCESSING_TIMEOUT')
            raise PreparationError('MANUAL_ACTION_REQUIRED', 'PROCESSING_TIMEOUT: transferencia confirmada; processamento nao comprovado')
        self._set_upload_state(draft if draft is not None else {}, 'TRANSFER_UNKNOWN', 'UNKNOWN')
        raise PreparationError('MANUAL_ACTION_REQUIRED', 'RECONCILIATION_REQUIRED: resultado da transferencia inconclusivo')

    def confirm_durable(self, draft, evidence):
        if not self.mapping.get('durable_confirmation_validated'):
            raise PreparationError('STUDIO_CHANGED','Persistencia do draft ainda nao validada; origem preservada')
        labels=('Salvar rascunho','Save draft','Salvar como rascunho')
        candidates=[(name,self.page.get_by_role('button',name=name,exact=True)) for name in labels]
        candidates=[(name,button) for name,button in candidates if button.count()==1 and button.is_visible()]
        if len(candidates)!=1 or not candidates[0][1].is_enabled():
            raise PreparationError('STUDIO_CHANGED','Salvar rascunho ausente ou ambiguo')
        evidence('SAVE_DRAFT',f'role=button; name={candidates[0][0]}; enabled=true; draft-only allowlist')
        self.before_mutation('SAVE_DRAFT')
        self.save_attempted = True
        candidates[0][1].click(timeout=10000)
        deadline=monotonic()+60
        while monotonic()<deadline:
            # The exact copyright-check dialog may appear after the save click.
            # Keep the same fail-closed allowlist active while waiting for the
            # durable content-list confirmation.
            self.interruptions.dismiss_allowed()
            self.challenge()
            content_path = urlsplit(self.page.url).path.rstrip('/')
            if content_path == '/tiktokstudio/content':
                text=self.page.locator('body').inner_text(timeout=5000)
                if ('rascunhos' in text.casefold() and 'todos os computadores' in text.casefold() and
                    self.page.get_by_text(draft['caption'], exact=True).count() >= 1):
                    evidence('TIKTOK_DRAFT_CONFIRMED','Apos salvar: lista Rascunhos, legenda exata unica e disponibilidade entre computadores')
                    self.after_mutation('SAVE_DRAFT')
                    return {'durable':True,'tiktok_draft_url':self.page.url,'persisted_at':now_iso()}
            self.page.wait_for_timeout(500)
        raise PreparationError('MANUAL_ACTION_REQUIRED','Persistencia nao comprovada; nao repetir upload')


def prepare_job(adapter, draft, preset, path, evidence, ffprobe_bin=None, media_validator=None):
    if not draft.get("caption", "").strip():
        raise PreparationError("CAPTION_MISSING", "Defina uma legenda no draft antes de preparar")
    if not path.is_file():
        raise PreparationError("UPLOAD_FAILED", "Arquivo local ausente")
    probe = probe_video(path, ffprobe_bin or 'ffprobe')
    if probe.validation_status == ValidationStatus.FAIL:
        raise PreparationError("UPLOAD_FAILED", "Arquivo sem stream de video valido")
    if not probe.results.get("ffprobe_available"):
        raise PreparationError("MANUAL_ACTION_REQUIRED", "Instale ffprobe para validar o arquivo antes do upload")
    if media_validator:
        try:
            media_validator(probe.results)
        except ValueError as exc:
            raise PreparationError('UPLOAD_FAILED', str(exc)) from exc
    before = file_sha256(path)
    checks = adapter.prepare(draft, preset, path, evidence)
    if file_sha256(path) != before:
        raise PreparationError("UPLOAD_FAILED", "Arquivo alterado durante a preparacao")
    return checks


class PublisherWorker:
    """One owning thread for Playwright; persistent context per account, tab per draft."""
    def __init__(self, root, db, provider_factory=media_provider):
        self.root, self.db = Path(root).resolve(), db
        self.commands = queue.Queue()
        self.contexts, self.pages = {}, {}
        self.lock = threading.RLock()
        self.account_locks, self.login_sessions = {}, {}
        self.auth_detector = TikTokAuthDetector()
        self.auth_diagnostics = AuthDiagnostics()
        self.diagnostic_requests = queue.Queue(maxsize=8)
        self.verification_requests = {}
        self.inspection_guards = {}
        self.login_traces = {}
        self.auth_test_lock = AuthTestLock(db)
        self.auth_test_run_ids = {}
        self.profile_ownership = ProfileOwnership(db)
        self.browser_runtime = BrowserRuntimeManager(self.root, db, ownership=self.profile_ownership)
        self.profile_leases = {}
        self.stop_event = threading.Event()
        self.runtime_auth = {}
        self.activation = {'server_pid': os.getpid(), 'one_shot_required': True,
            'code_sha256': hashlib.sha256(b''.join(Path(__file__).with_name(path).read_bytes() for path in (
                'worker.py', 'diagnostics.py', 'login_trace.py', 'auth_test_lock.py'))).hexdigest()}
        self.last_auth_poll = 0
        self.thread = None
        self.cache = TemporaryMediaCache(self.root)
        self.provider_factory = provider_factory

    def start(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self.run, daemon=True, name="publisher-studio")
                self.thread.start()

    def shutdown(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=30)
            if self.thread.is_alive(): raise RuntimeError('WORKER_SHUTDOWN_PENDING')

    def probe_draft_one(self, operation, data):
        if not self.thread or not self.thread.is_alive():raise ValueError('EXISTING_WORKER_REQUIRED')
        future=Future()
        self.commands.put(('draft-one-probe',(operation,data,future)))
        return future.result(timeout=90)

    def close_owned_contexts(self):
        try:
            self.browser_runtime.shutdown()
        except Exception as exc:
            self.auth_diagnostics.record(0, 'RUNTIME_SHUTDOWN_ERROR', **safe_error('shutdown', exc))
        self.contexts.clear()
        self.pages.clear()
        self.profile_leases.clear()

    def release_owned_profiles(self):
        # BrowserRuntimeManager owns detach/shutdown semantics. External Chrome
        # is detached without closing its process; runtime-owned Chrome is closed
        # gracefully before the persistent mutex is released.
        try:
            self.browser_runtime.shutdown()
        finally:
            self.profile_leases.clear()

    def enqueue(self, conn, ids, auto_cleanup=False):
        self.assert_actions_allowed()
        if self.login_traces:
            raise ValueError('Teste de login isolado; preparacao de drafts bloqueada nesta execucao')
        ids = list(dict.fromkeys(int(i) for i in ids))
        if not ids or len(ids) > 100:
            raise ValueError("Selecione entre 1 e 100 drafts")
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                f"SELECT id,queue_position FROM publisher_drafts WHERE id IN ({','.join('?' for _ in ids)}) "
                "ORDER BY batch_id DESC,queue_position ASC,id ASC", ids,
            ).fetchall()
            if len(rows) != len(ids) or any(row['queue_position'] is None for row in rows):
                raise ValueError('QUEUE_POSITION_REQUIRED')
            ids = [row['id'] for row in rows]
            batches = set()
            for row_id in ids:
                row = get_row(conn, "publisher_drafts", row_id)
                assert_upload_allowed(conn,row_id)
                account = get_row(conn, 'publisher_accounts', row['account_id'])
                try:
                    AccountSafetyGuard(conn).assert_allowed(account['id'])
                except AccountProtectionBlocked as exc:
                    raise ValueError(f'{BLOCKED_BY_ACCOUNT_PROTECTION}: {exc.reason_code}') from exc
                if account.get('account_identity') == 'ACCOUNT_MISMATCH':
                    raise ValueError('ACCOUNT_MISMATCH')
                if self.profile_ownership.state(self.account_profile(account)) in {'MANUAL_STARTING', 'MANUAL_BROWSER_ACTIVE'}:
                    raise ValueError('MANUAL_BROWSER_ACTIVE')
                if not row.get('media_asset_id'):
                    raise ValueError('Draft local legado: selecione a midia no Drive para criar um novo lote')
                PublisherService(conn, self.root).validate_fields(row)
                if not str(row.get('caption') or '').strip():
                    raise ValueError(f"CAPTION_MISSING: draft {row_id} sem legenda")
                asset = get_row(conn,'publisher_media_assets',row['media_asset_id'])
                PublisherService(conn,self.root).assert_media_available(asset,row_id)
                transition(conn, row_id, "PREPARING")
                batches.add(row['batch_id'])
            execution = conn.execute("INSERT INTO publisher_batch_executions(batch_id,total,started_at,auto_cleanup) VALUES (?,?,?,?)",
                                     (next(iter(batches)) if len(batches)==1 else None,len(ids),now_iso(),int(bool(auto_cleanup)))).lastrowid
            for position, row_id in enumerate(ids, 1):
                conn.execute('INSERT INTO publisher_execution_items(execution_id,draft_id,execution_position) VALUES (?,?,?)',(execution,row_id,position))
        self.start()
        for row_id in ids:
            self.commands.put(("prepare", (row_id, execution)))
        return execution

    def resume_execution(self, conn, execution_id):
        """Resume the existing paused execution without creating another batch."""
        self.assert_actions_allowed()
        execution_id = int(execution_id)
        with conn:
            execution = conn.execute(
                "SELECT * FROM publisher_batch_executions WHERE id=?",
                (execution_id,),
            ).fetchone()
            if not execution:
                raise ValueError('EXECUTION_NOT_FOUND')
            if execution['status'] != 'PAUSED':
                raise ValueError('EXECUTION_NOT_RESUMABLE')
            if not execution['auto_cleanup']:
                raise ValueError('AUTO_CLEANUP_REQUIRED')

            items = conn.execute(
                "SELECT * FROM publisher_execution_items WHERE execution_id=? ORDER BY execution_position",
                (execution_id,),
            ).fetchall()
            if len(items) != execution['total']:
                raise ValueError('EXECUTION_ITEM_COUNT_MISMATCH')

            queued = []
            reconcile_ids = []
            cleanup_ids = []
            for item in items:
                draft = get_row(conn, 'publisher_drafts', item['draft_id'])
                asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
                if item['status'] == 'SUCCEEDED':
                    if draft['persistence_status'] != 'PERSISTED' or asset['status'] != 'CONSUMED':
                        raise ValueError('SUCCEEDED_ITEM_NOT_FINAL')
                    continue
                if item['status'] != 'WAITING_MANUAL':
                    raise ValueError('PENDING_ITEM_NOT_WAITING_MANUAL')
                if (draft['status'] == 'READY_FOR_REVIEW' and
                        draft['persistence_status'] == 'PERSISTED' and
                        asset['status'] == 'RESERVED'):
                    cleanup_ids.append(draft['id'])
                    continue
                if (draft['upload_status'] == 'TRANSFER_CONFIRMED' and
                        draft['persistence_status'] != 'PERSISTED' and
                        asset['status'] == 'NEEDS_RECONCILIATION'):
                    reconcile_ids.append(draft['id'])
                    continue
                if draft['status'] != 'DRAFT' or draft['upload_status'] != 'NOT_STARTED' or draft['processing_status'] != 'NOT_STARTED':
                    raise ValueError('PENDING_DRAFT_NOT_PRISTINE')
                if asset['status'] != 'AVAILABLE' or asset['consumed_by_draft_id'] is not None or asset['remote_deleted_at']:
                    raise ValueError('PENDING_MEDIA_NOT_AVAILABLE')
                PublisherService(conn, self.root).validate_fields(draft)
                if not str(draft.get('caption') or '').strip():
                    raise ValueError(f'CAPTION_MISSING: draft {draft["id"]} sem legenda')
                queued.append(draft['id'])

            if len(reconcile_ids) + len(cleanup_ids) > 1:
                raise ValueError('MULTIPLE_RECOVERY_CANDIDATES')
            if reconcile_ids and cleanup_ids:
                raise ValueError('MULTIPLE_RECOVERY_CANDIDATES')
            if not queued and not reconcile_ids and not cleanup_ids:
                raise ValueError('NO_PENDING_ITEMS')
            for draft_id in queued:
                transition(conn, draft_id, 'PREPARING')
                conn.execute(
                    "UPDATE publisher_execution_items SET status='QUEUED',error_code=NULL WHERE execution_id=? AND draft_id=?",
                    (execution_id, draft_id),
                )
            if reconcile_ids:
                conn.execute(
                    "UPDATE publisher_execution_items SET status='PROCESSING',error_code=NULL "
                    "WHERE execution_id=? AND draft_id=?",
                    (execution_id, reconcile_ids[0]),
                )
            if cleanup_ids:
                conn.execute(
                    "UPDATE publisher_execution_items SET status='PROCESSING',error_code=NULL "
                    "WHERE execution_id=? AND draft_id=?",
                    (execution_id, cleanup_ids[0]),
                )
            conn.execute(
                "UPDATE publisher_batch_executions SET status='QUEUED',finished_at=NULL WHERE id=?",
                (execution_id,),
            )
        self.start()
        for draft_id in reconcile_ids:
            self.commands.put(('reconcile', (draft_id, execution_id)))
        for draft_id in cleanup_ids:
            self.commands.put(('cleanup-reconciled', (draft_id, execution_id)))
        for draft_id in queued:
            self.commands.put(('prepare', (draft_id, execution_id)))
        return execution_id

    def _queue_pending_execution(self, conn, execution_id):
        """Queue untouched items only after the reconciliation item succeeds."""
        queued = []
        items = conn.execute(
            "SELECT * FROM publisher_execution_items WHERE execution_id=? ORDER BY execution_position",
            (execution_id,),
        ).fetchall()
        for item in items:
            if item['status'] != 'WAITING_MANUAL':
                continue
            draft = get_row(conn, 'publisher_drafts', item['draft_id'])
            asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
            if (draft['status'] != 'DRAFT' or draft['upload_status'] != 'NOT_STARTED' or
                    draft['processing_status'] != 'NOT_STARTED' or asset['status'] != 'AVAILABLE' or
                    asset['consumed_by_draft_id'] is not None or asset['remote_deleted_at']):
                raise ValueError('PENDING_DRAFT_NOT_PRISTINE')
            PublisherService(conn, self.root).validate_fields(draft)
            if not str(draft.get('caption') or '').strip():
                raise ValueError(f'CAPTION_MISSING: draft {draft["id"]} sem legenda')
            transition(conn, draft['id'], 'PREPARING')
            conn.execute(
                "UPDATE publisher_execution_items SET status='QUEUED',error_code=NULL WHERE execution_id=? AND draft_id=?",
                (execution_id, draft['id']),
            )
            queued.append(draft['id'])
        if queued:
            conn.execute(
                "UPDATE publisher_batch_executions SET status='QUEUED',finished_at=NULL WHERE id=?",
                (execution_id,),
            )
            conn.commit()
            for draft_id in queued:
                self.commands.put(('prepare', (draft_id, execution_id)))
        else:
            conn.execute(
                "UPDATE publisher_batch_executions SET status='COMPLETED',finished_at=? WHERE id=?",
                (now_iso(), execution_id),
            )
            conn.commit()
        return queued

    def submit(self, command, row_id):
        if self.login_traces and command == 'review':
            raise ValueError('Teste de login isolado; abertura de drafts bloqueada')
        if command in {'login','review'}:
            self.assert_actions_allowed()
        if command == 'login':
            return self.request_login(int(row_id))
        self.start()
        self.commands.put((command, int(row_id)))

    def request_draft_verification(self, draft_id, timeout=90):
        """Coalesce concurrent reads of one execution item; never enqueue a mutation."""
        draft_id = int(draft_id)
        with closing(connect(self.db)) as conn:
            draft = get_row(conn, 'publisher_drafts', draft_id)
            asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
            if draft['persistence_status'] != 'UNCONFIRMED' or asset['status'] != 'NEEDS_RECONCILIATION':
                raise ValueError('DRAFT_NOT_VERIFY_ELIGIBLE')
            item = conn.execute(
                'SELECT i.execution_id,i.status,e.status execution_status '
                'FROM publisher_execution_items i JOIN publisher_batch_executions e '
                'ON e.id=i.execution_id WHERE i.draft_id=? ORDER BY i.execution_id DESC LIMIT 1',
                (draft_id,),
            ).fetchone()
            if not item or item['status'] != 'WAITING_MANUAL' or item['execution_status'] != 'PAUSED':
                raise ValueError('EXECUTION_ITEM_NOT_PAUSED_FOR_VERIFICATION')
            execution_id = item['execution_id']
        key = (execution_id, draft_id)
        with self.lock:
            future = self.verification_requests.get(key)
            if future is None:
                future = Future()
                self.verification_requests[key] = future
                self.start()
                self.commands.put(('verify-draft', (draft_id, execution_id, future)))
        return future.result(timeout=timeout)

    def account_lock(self, account_id):
        with self.lock:
            return self.account_locks.setdefault(account_id, threading.RLock())

    def request_login(self, account_id):
        raise ValueError('EXPLICIT_ONE_SHOT_AUTHORIZATION_REQUIRED')
        # Reserve before queueing, including the interval before Playwright starts.
        # No Playwright calls run on HTTP threads or while this short lock is held.
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            if account_id in self.login_traces or self.auth_test_lock.has_history(account_id):
                raise ValueError('Tentativa controlada reservada ou encerrada; nenhum retry permitido')
            account = get_row(conn, 'publisher_accounts', account_id)
            if not account['active']:
                raise ValueError('Conta Publisher inativa')
            session = self.login_sessions.setdefault(account_id, LoginSession(status=account['auth_status']))
            already = session.queued or session.page is not None
            code = 'LOGIN_ALREADY_IN_PROGRESS' if already else 'LOGIN_STARTED'
            if session.status == AUTHENTICATED:
                code = 'STUDIO_OPEN_REQUESTED'
            if not session.queued:
                session.queued = True
                if session.status != AUTHENTICATED:
                    session.manual_pending = True
                    session.status = LOGIN_IN_PROGRESS
                    persist_auth(conn, account_id, session.status, 'Aguardando autenticacao manual na mesma aba')
                self.commands.put(('login', account_id))
            result = {'ok': True, 'code': code, 'auth_status': session.status, 'login_active': True}
        self.start()
        return result

    def authorize_controlled_login(self, account_id, *, explicitly_authorized=False):
        self.assert_experimental_qr_enabled()
        return self.auth_test_lock.authorize(account_id, explicitly_authorized=explicitly_authorized)

    def assert_experimental_qr_enabled(self):
        config = json.loads((self.root / 'config/publisher_studio.json').read_text(encoding='utf-8'))
        if config.get('experimental_qr_enabled') is not True:
            raise ValueError('EXPERIMENTAL_QR_DISABLED')

    def request_controlled_login(self, account_id, *, run_id=None, explicitly_authorized=False):
        """Not called by startup or the ordinary login button."""
        self.assert_experimental_qr_enabled()
        if explicitly_authorized is not True:
            raise ValueError('Nova autorizacao explicita obrigatoria')
        if not run_id:
            raise ValueError('AUTHORIZED_RUN_ID_REQUIRED')
        self.assert_actions_allowed()
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            account = get_row(conn, 'publisher_accounts', account_id)
            profile = self.account_profile(account, require_existing=False)
            if not account['active'] or not profile.is_dir():
                raise ValueError('Conta inativa ou profile existente ausente')
            session = self.login_sessions.setdefault(account_id, LoginSession(status=account['auth_status']))
            previous = self.login_traces.get(account_id)
            if (account_id in self.login_traces and (previous is None or not previous.stopped)) or session.queued:
                raise ValueError('Tentativa ocupada ou ja consumida; sem retry')
            if self.pages or not self.commands.empty():
                raise ValueError('Worker ocupado; teste exige nenhuma outra operacao pendente')
            self.auth_test_lock.consume(account_id, run_id)
            self.auth_test_run_ids[account_id] = run_id
            self.login_traces[account_id] = None
            session.queued = True
            self.commands.put(('controlled-login', account_id))
        try:
            self.start()
        except Exception:
            self.auth_test_lock.finish(run_id, 'UNKNOWN')
            raise

    def report_phone_confirmation(self, account_id):
        with self.account_lock(account_id):
            trace = self.login_traces.get(account_id)
            if trace is None or trace.stopped:
                raise ValueError('Nenhuma tentativa controlada ativa')
            self.commands.put(('phone-confirmed', account_id))

    def handle_controlled_login(self, playwright, conn, account_id):
        self.assert_experimental_qr_enabled()
        self.auth_test_lock.claim_execution(self.auth_test_run_ids[account_id])
        session = self.login_sessions[account_id]
        trace = LoginTrace(self.root / 'data/publisher/evidence' /
                          f'login-{account_id}-{now_iso().replace(":", "-")}.jsonl')
        self.login_traces[account_id] = trace
        self.auth_diagnostics.event_sinks[account_id] = trace.event
        try:
            account = get_row(conn, 'publisher_accounts', account_id)
            if not self.account_profile(account, require_existing=False).is_dir():
                raise ValueError('Profile existente ausente')
            ctx = self.context(playwright, account)
            if len(ctx.pages) != 1:
                raise ValueError('Tentativa exige exatamente uma pagina; nenhuma aba sera criada ou fechada')
            self.auth_diagnostics.attach_context(account_id, ctx)
            page = ctx.pages[0]
            session.page = page
            registered = self.inspection_guards.pop(account_id, None)
            if registered:
                registered[0].unroute('**/*', registered[1])
            session.verification_only = False
            session.navigation_started = True
            session.status = LOGIN_IN_PROGRESS
            # This is the sole navigation performed by this command, with no retry path.
            page.goto(LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
        except Exception as exc:
            self.auth_diagnostics.record(account_id, 'CONTROLLED_LOGIN_ERROR', **safe_error('login_navigation', exc))
            trace.stop_reason = 'UNKNOWN'
        finally:
            session.queued = False

    def poll_controlled_login(self, account_id, session):
        trace = self.login_traces[account_id]
        if trace is None or trace.stopped:
            return
        try:
            snapshot = self.auth_diagnostics.collect(account_id, self.contexts.get(account_id), session.page)
            result = trace.observe(snapshot)
        except Exception:
            self.auth_test_lock.finish(self.auth_test_run_ids[account_id], 'UNKNOWN')
            trace.stopped, trace.result = True, 'UNKNOWN'
            self.auth_diagnostics.event_sinks.pop(account_id, None)
            raise
        if result:
            self.auth_test_lock.finish(self.auth_test_run_ids[account_id], result)
            self.auth_diagnostics.event_sinks.pop(account_id, None)
            if result == AUTHENTICATED:
                with closing(connect(self.db)) as conn:
                    session.status = AUTHENTICATED
                    persist_auth(conn, account_id, AUTHENTICATED, 'Tentativa unica: evidencia desktop positiva em dois snapshots')
                try:
                    self.contexts[account_id].close()
                    trace.write('EVENT', {'event': 'GRACEFUL_CONTEXT_CLOSE_COMPLETED'})
                except Exception as exc:
                    trace.write('EVENT', {'event': 'GRACEFUL_CLOSE_ERROR', **safe_error('context_close', exc)})

    def login_snapshot(self, account):
        with self.account_lock(account['id']):
            session = self.login_sessions.get(account['id'])
            return {**account, 'profile_state': self.profile_ownership.state(self.account_profile(account, require_existing=False)),
                    'runtime_auth_state': self.runtime_auth.get(account['id'], 'UNCHECKED'),
                    'auth_status': session.status if session and session.queued else account['auth_status'],
                    'login_active': bool(session and not session.verification_only and (session.queued or session.page is not None))}

    def account_profile(self, account, require_existing=True):
        profile = resolve_browser_profile(account['browser_profile'])
        if require_existing and not profile.is_dir():
            raise ValueError('EXISTING_PROFILE_REQUIRED')
        return profile

    def open_manual_profile(self, account_id):
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            account = get_row(conn, 'publisher_accounts', account_id)
            session = self.login_sessions.get(account_id)
            if not account['active'] or account_id in self.contexts or (session and session.queued):
                raise ValueError('BROWSER_PROFILE_BUSY')
            result = self.profile_ownership.open_manual(self.account_profile(account))
            self.login_sessions[account_id] = LoginSession(status=account['auth_status'])
            return result

    def verify_manual_profile(self, account_id):
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            account = get_row(conn, 'publisher_accounts', account_id)
            profile = self.account_profile(account)
            self.profile_ownership.reconcile(profile)
            state = self.profile_ownership.check_manual_closed(profile)
            if state not in {'IDLE','SESSION_REQUIRES_VERIFICATION'}:
                raise ValueError('Feche o Chrome manual antes de verificar a sessao')
        return self.inspect_existing_session(account_id, navigate=True)

    def inspect_existing_session(self, account_id, navigate=False):
        self.assert_actions_allowed()
        account_id = int(account_id)
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            account = get_row(conn, 'publisher_accounts', account_id)
            if not account['active'] or not self.account_profile(account, require_existing=False).is_dir():
                raise ValueError('Conta inativa ou perfil persistente ausente; nenhum perfil sera criado')
            if self.profile_ownership.state(self.account_profile(account)) in {'MANUAL_STARTING', 'MANUAL_BROWSER_ACTIVE'}:
                raise ValueError('MANUAL_BROWSER_ACTIVE')
            session = self.login_sessions.setdefault(account_id, LoginSession(status=account['auth_status']))
            if session.queued or (session.page is not None and not session.verification_only):
                raise ValueError('Sessao ocupada; verificacao nao iniciada')
            if navigate and session.verification_navigation_used:
                raise ValueError('A navegacao unica desta verificacao ja foi utilizada')
            session.verification_only = True
            session.queued = True
            self.commands.put(('inspect-existing', (account_id, bool(navigate))))
        self.start()
        return {'ok': True, 'code': 'EXISTING_SESSION_INSPECTION_QUEUED', 'navigate': bool(navigate)}

    def handle_existing_session(self, playwright, conn, account_id, navigate):
        session = self.login_sessions[account_id]
        ctx = None
        page = None
        try:
            account = get_row(conn, 'publisher_accounts', account_id)
            if not self.account_profile(account, require_existing=False).is_dir():
                raise ValueError('Perfil existente ausente')
            ctx = self.context(playwright, account)
            if account_id not in self.inspection_guards:
                def guard(route):
                    request = route.request
                    navigation = request.is_navigation_request()
                    main_frame = navigation and request.frame.parent_frame is None
                    decision = inspection_navigation_decision(
                        request.url, navigation_request=navigation, main_frame=main_frame
                    )
                    if decision['blocked']:
                        session.blocked_auth_navigation |= decision['reason'] == 'TIKTOK_AUTH_QR_NAVIGATION'
                        self.auth_diagnostics.record(account_id, 'ROUTE_GUARD_BLOCKED_NAVIGATION',
                            blocked=True, blocked_url_host=decision['host'],
                            reason=decision['reason'], main_frame=main_frame)
                        route.abort('blockedbyclient')
                    else:
                        route.continue_()
                ctx.route('**/*', guard)
                self.inspection_guards[account_id] = (ctx, guard)
            if session.page is None or session.page.is_closed():
                session.page = select_live_inspection_page(ctx, session.page)
                page = session.page
                page.on('close', lambda: self.login_page_closed(account_id, page))
            if navigate:
                if session.verification_navigation_used:
                    raise ValueError('Navegacao unica ja utilizada')
                session.verification_navigation_used = True
                self.auth_diagnostics.record(account_id, 'CONTROLLED_STUDIO_NAVIGATION', url=STUDIO_URL)
                session.page.goto(STUDIO_URL, wait_until='domcontentloaded', timeout=30000)
        except Exception as exc:
            error = safe_error('existing_session', exc)
            target_classification = classify_inspection_target_error(exc, context=ctx, page=page)
            if target_classification:
                error['target_classification'] = target_classification
            self.auth_diagnostics.record(account_id, 'INSPECTION_ERROR', **error)
        finally:
            if session.blocked_auth_navigation:
                session.status = NEEDS_LOGIN
                persist_auth(conn, account_id, NEEDS_LOGIN, 'Redirecionamento para login bloqueado; nenhuma nova autenticacao iniciada')
            with self.account_lock(account_id):
                session.queued = False

    def get_auth_diagnostic_snapshot(self, account_id, timeout=5):
        account_id = int(account_id)
        # HTTP threads never touch Playwright objects or start the owning thread.
        with self.lock:
            active = self.contexts.get(account_id) is not None
            running = self.thread is not None and self.thread.is_alive()
        if not active:
            return {**self.auth_diagnostics.empty(account_id), 'activation': self.activation}
        if not running:
            result = self.auth_diagnostics.empty(account_id, 'WORKER_UNAVAILABLE',
                [safe_error('worker', 'InvalidStateError')])
            result.update(context_count=1, page_count=None)
            return result
        future = Future()
        try:
            self.diagnostic_requests.put_nowait((account_id, future))
            return future.result(timeout=timeout)
        except (queue.Full, FutureTimeout) as exc:
            future.cancel()
            result = self.auth_diagnostics.empty(account_id, 'SNAPSHOT_BUSY',
                [safe_error('diagnostic_queue', 'TimeoutError' if isinstance(exc, FutureTimeout) else 'Error')])
            result.update(context_count=1, page_count=None)
            return result

    def process_diagnostic_requests(self):
        # Called exclusively by the already-running Playwright owner, never by GET.
        for _ in range(8):
            try:
                account_id, future = self.diagnostic_requests.get_nowait()
            except queue.Empty:
                break
            if not future.set_running_or_notify_cancel():
                continue
            try:
                session = self.login_sessions.get(account_id)
                result = self.auth_diagnostics.collect(account_id, self.contexts.get(account_id),
                                                       session.page if session else None)
                result['verification_only'] = bool(session and session.verification_only)
                result['verification_navigation_used'] = bool(session and session.verification_navigation_used)
                result['blocked_auth_navigation'] = bool(session and session.blocked_auth_navigation)
                result['auth_entry_guard_active'] = account_id in self.inspection_guards
                with closing(connect(self.db)) as conn:
                    account = get_row(conn, 'publisher_accounts', account_id)
                result.update(evaluate_studio_snapshot(result, account.get('tiktok_username',''),
                    profile=account['browser_profile'], confirmed_profile=account.get('identity_confirmed_profile',''),
                    confirmed_handle=account.get('identity_confirmed_handle','')))
            except Exception as exc:
                error = safe_error('snapshot', exc)
                self.auth_diagnostics.record(account_id, 'COLLECTION_ERROR', **error)
                result = self.auth_diagnostics.empty(account_id, 'COLLECTION_FAILED', [error])
            future.set_result(result)

    def login_page_closed(self, account_id, page):
        with self.account_lock(account_id), closing(connect(self.db)) as conn:
            session = self.login_sessions.get(account_id)
            if not session or session.page is not page:
                return
            session.page = None
            session.positive_key, session.positive_readings = None, 0
            session.manual_pending = False
            self.runtime_auth[account_id] = 'SESSION_REQUIRES_VERIFICATION'

    def handle_login(self, playwright, conn, account_id):
        account = get_row(conn, 'publisher_accounts', account_id)
        session = self.login_sessions[account_id]
        try:
            self.assert_actions_allowed()
            ctx = self.context(playwright, account)
            was_verification = session.verification_only
            if was_verification:
                registered = self.inspection_guards.pop(account_id, None)
                if registered:
                    registered[0].unroute('**/*', registered[1])
                session.verification_only = False
            page = session.page
            if page is None or page.is_closed():
                available = [p for p in ctx.pages if not p.is_closed() and p not in self.pages.values()]
                candidates = [p for p in available if is_login_url(p.url) or is_studio_url(p.url)]
                page = next(iter(candidates), None)
                if page is None:
                    page = next((p for p in available if p.url == 'about:blank'), None)
                if page is None:
                    page = ctx.new_page()
                with self.account_lock(account_id):
                    session.page = page
                    session.navigation_started = page.url != 'about:blank'
                page.on('close', lambda: self.login_page_closed(account_id, page))
                # Consolidate restored login duplicates only; never close draft or OAuth tabs.
                for duplicate in candidates:
                    if duplicate is not page and is_login_url(duplicate.url):
                        duplicate.close()
            page.bring_to_front()
            if session.status == AUTHENTICATED and not is_studio_url(page.url):
                session.navigation_started = True
                page.goto(STUDIO_URL, wait_until='domcontentloaded')
            elif session.status != AUTHENTICATED and (was_verification or (page.url == 'about:blank' and not session.navigation_started)):
                session.navigation_started = True
                page.goto(LOGIN_URL, wait_until='domcontentloaded')
            # An existing login (including timeout/challenge) is focused, never reloaded.
        except Exception as exc:
            with self.account_lock(account_id):
                if session.page is None:
                    session.status, session.manual_pending = NEEDS_LOGIN, False
                persist_auth(conn, account_id, session.status,
                             f'Abertura interrompida ({type(exc).__name__}); sem repeticao automatica')
            raise
        finally:
            with self.account_lock(account_id):
                session.queued = False

    def poll_login_sessions(self):
        # Reads only the DOM/cookie jar of manually opened pages; no HTTP, goto or reload.
        if monotonic() - self.last_auth_poll < 1:
            return
        self.last_auth_poll = monotonic()
        for profile,token in list(self.profile_leases.values()):
            self.profile_ownership.heartbeat(profile,token)
        with self.lock:
            account_ids = list(self.account_locks)
        for account_id in account_ids:
            with self.account_lock(account_id):
                session = self.login_sessions.get(account_id)
                page = session.page if session else None
            if page is None:
                if account_id in self.login_traces:
                    self.poll_controlled_login(account_id, session)
                continue
            if account_id in self.login_traces:
                self.poll_controlled_login(account_id, session)
                continue
            if page.is_closed():
                self.login_page_closed(account_id, page)
                continue
            try:
                with closing(connect(self.db)) as conn:
                    account = get_row(conn, 'publisher_accounts', account_id)
                observation = self.auth_detector.observe(page, account)
            except Exception as exc:
                # A navigation/detached DOM breaks positive stability, not evidence of success.
                with self.account_lock(account_id):
                    session.positive_key, session.positive_readings = None, 0
                self.auth_diagnostics.detector_failed(account_id, exc)
                self.runtime_auth[account_id] = 'UNKNOWN'
                continue
            self.auth_diagnostics.detector_succeeded(account_id)
            with self.account_lock(account_id), closing(connect(self.db)) as conn:
                if observation.account_identity == 'ACCOUNT_MISMATCH':
                    conn.execute("UPDATE publisher_accounts SET account_identity='ACCOUNT_MISMATCH',session_verified=0,auth_status='NEEDS_LOGIN' WHERE id=?", (account_id,))
                    conn.commit()
                    session.status = NEEDS_LOGIN
                    continue
                if session.verification_only and observation.login_required:
                    session.status = NEEDS_LOGIN
                    persist_auth(conn, account_id, NEEDS_LOGIN, 'Sessao expirada; autentique no Chrome manual')
                    continue
                if session.verification_only and (not observation.positive or not is_studio_url(page.url)):
                    self.runtime_auth[account_id] = 'UNKNOWN'
                    session.positive_key, session.positive_readings = None, 0
                    continue
                session.detect(observation)
                self.runtime_auth[account_id] = session.status
                if not session.verification_only or session.status == AUTHENTICATED:
                    persist_auth(conn, account_id, session.status, observation.evidence)

    def actions_paused(self):
        mapping = self.root/'config/publisher_studio.json'
        return not mapping.exists() or bool(json.loads(mapping.read_text(encoding='utf-8')).get('tiktok_actions_paused',True))

    def assert_actions_allowed(self):
        if self.actions_paused():
            raise ValueError('Contato com TikTok pausado para diagnostico; nenhuma tentativa sera iniciada')

    def session_ids(self):
        with self.lock:
            return list(self.pages)

    def context(self, playwright, account):
        self.assert_actions_allowed()
        key = account["id"]
        if key not in self.contexts:
            profile = self.account_profile(account)
            self.contexts[key] = self.browser_runtime.get_context(playwright, profile)
            runtime_session = self.browser_runtime.session(profile)
            if runtime_session:
                self.profile_leases[key] = (profile, runtime_session.token)
            context = self.contexts[key]
            self.auth_diagnostics.attach_context(key, context)
            def closed():
                self.profile_leases.pop(key, None)
                if self.contexts.get(key) is context:
                    self.contexts.pop(key, None)
                session = self.login_sessions.get(key)
                if session and session.page is not None:
                    self.login_page_closed(key, session.page)
            context.on("close", closed)
        return self.contexts[key]

    def _verify_runtime_session(self, account, context):
        pages = [page for page in context.pages if not page.is_closed()]
        page = next((page for page in pages if is_studio_url(page.url)), None)
        page = page or (pages[0] if pages else None)
        if page is None:
            return {'auth_result': 'UNKNOWN', 'account_identity': 'ACCOUNT_IDENTITY_UNKNOWN'}
        snapshot = self.auth_diagnostics.collect(account['id'], context, page)
        result = evaluate_studio_snapshot(
            snapshot,
            account.get('tiktok_username', ''),
            profile=account.get('browser_profile', ''),
            confirmed_profile=account.get('identity_confirmed_profile', ''),
            confirmed_handle=account.get('identity_confirmed_handle', ''),
        )
        result['runtime_auth_verified'] = (
            result.get('auth_result') == AUTHENTICATED
            and result.get('account_identity') == 'ACCOUNT_CONFIRMED'
        )
        return result

    def _inspect_draft_list_read_only(self, playwright, account):
        """Only navigation and DOM reads; editor state is never dismissed."""
        result = {'source_surface': 'TIKTOK_STUDIO_AUTH', 'observed_account': None,
                  'rows': [], 'listing_complete': False, 'blocker_code': None,
                  'listing_bounds': {}}
        context = self.context(playwright, account)
        live = [page for page in context.pages if not page.is_closed()]
        if len(live) != 1:
            result['blocker_code'] = 'LISTING_ERROR'
            return result
        page = live[0]
        if page.url != 'about:blank' and not is_tiktok_url(page.url):
            result['blocker_code'] = 'LISTING_ERROR'
            return result
        path = urlsplit(page.url).path.rstrip('/')
        if path.startswith('/tiktokstudio/upload') or path.startswith('/tiktokstudio/edit'):
            result.update(source_surface='TIKTOK_STUDIO_EDITOR', blocker_code='UNSAVED_EDITOR')
            return result
        body = page.locator('body').inner_text(timeout=5000) if page.url != 'about:blank' else ''
        folded = ''.join(char for char in unicodedata.normalize('NFD', body.casefold())
                         if not unicodedata.combining(char))
        if ('continuar editando' in folded and ('nao salv' in folded or 'rascunho' in folded)):
            result.update(source_surface='TIKTOK_STUDIO_EDITOR', blocker_code='UNSAVED_EDITOR')
            return result
        if is_login_url(page.url) or 'entrar no tiktok' in folded:
            result['blocker_code'] = 'AUTH_REQUIRED'
            return result
        if 'captcha' in folded:
            result['blocker_code'] = 'CAPTCHA'
            return result
        if any(marker in folded for marker in ('verifique se voce e humano', 'security challenge', 'verificacao de seguranca')):
            result['blocker_code'] = 'SECURITY_CHALLENGE'
            return result
        if any(marker in folded for marker in ('codigo de verificacao', 'two-factor authentication', 'verificacao em duas etapas')):
            result['blocker_code'] = 'TWO_FACTOR'
            return result
        if page.url == 'about:blank' or path in ('/tiktokstudio', '/tiktokstudio/content'):
            if path != '/tiktokstudio':
                page.goto('https://www.tiktok.com/tiktokstudio', wait_until='domcontentloaded', timeout=30000)
        else:
            result['blocker_code'] = 'LISTING_ERROR'
            return result
        auth = self._verify_runtime_session(account, context)
        expected = str(account.get('tiktok_username') or '').strip().lstrip('@').casefold()
        handles = auth.get('observed_handles') or []
        if len(handles) == 1:
            result['observed_account'] = handles[0]
        if auth.get('account_identity') == 'ACCOUNT_MISMATCH' or (
            result['observed_account'] is not None and result['observed_account'] != expected
        ):
            result['blocker_code'] = 'WRONG_ACCOUNT'
            return result
        if auth.get('auth_result') != AUTHENTICATED:
            result['blocker_code'] = 'AUTH_REQUIRED'
            return result
        if result['observed_account'] is None or auth.get('account_identity') != 'ACCOUNT_CONFIRMED':
            result['blocker_code'] = 'WRONG_ACCOUNT'
            return result
        page.goto('https://www.tiktok.com/tiktokstudio/content', wait_until='domcontentloaded', timeout=30000)
        if urlsplit(page.url).path.rstrip('/') != '/tiktokstudio/content':
            result['blocker_code'] = 'AUTH_REQUIRED' if is_login_url(page.url) else 'LISTING_ERROR'
            return result
        result['source_surface'] = 'TIKTOK_STUDIO_DRAFT_LIST'
        text = page.locator('body').inner_text(timeout=5000)
        folded = ''.join(char for char in unicodedata.normalize('NFD', text.casefold())
                         if not unicodedata.combining(char))
        if 'captcha' in folded:
            result['blocker_code'] = 'CAPTCHA'
            return result
        if any(marker in folded for marker in ('codigo de verificacao', 'verificacao em duas etapas')):
            result['blocker_code'] = 'TWO_FACTOR'
            return result
        if any(marker in folded for marker in ('security challenge', 'verificacao de seguranca')):
            result['blocker_code'] = 'SECURITY_CHALLENGE'
            return result
        if 'rascunhos' in folded and 'todos os computadores' not in folded:
            drafts_tab = page.locator('[data-tt="Header_HeaderTabBar_Container"]').filter(
                has_text=re.compile(r'^Rascunhos\s+\d+$', re.I))
            if drafts_tab.count() != 1 or not drafts_tab.is_visible():
                result['blocker_code'] = 'PARTIAL_LISTING'
                return result
            drafts_tab.click(timeout=10000)
            text = page.locator('body').inner_text(timeout=5000)
            folded = text.casefold()
        if 'rascunhos' not in folded or 'todos os computadores' not in folded:
            result['blocker_code'] = 'PARTIAL_LISTING'
            return result
        listing = page.evaluate("""() => {
          const rows = [...document.querySelectorAll('[data-tt="components_DraftRowLayout_FlexRow"]')]
            .map(el => {
              const lines = (el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_2"]')?.innerText || '')
                .split(/\\r?\\n/).map(x => x.trim()).filter(Boolean);
              return {
                caption_text: lines.slice(1).join('\\n'), duration_text: lines[0] || '',
                updated_text: (el.querySelector('[data-tt="components_DraftRowLayout_FlexRow_3"]')?.innerText || '').trim()
              };
            });
          const tabs = [...document.querySelectorAll('[data-tt="Header_HeaderTabBar_Container"]')];
          const draftTab = tabs.find(el => /^Rascunhos\\s+\\d+$/i.test((el.innerText || '').trim()));
          const total = draftTab ? Number((draftTab.innerText || '').match(/\\d+/)?.[0]) : null;
          return {
            rows, total, loading: !!document.querySelector('[aria-busy="true"], [role="progressbar"]'),
            pagination: !!document.querySelector('[aria-label*="próxima" i], [aria-label*="next" i], [data-e2e*="pagination"]')
          };
        }""")
        rows = listing.get('rows') or []
        total = listing.get('total')
        result['rows'] = rows
        result['listing_bounds'] = {'visible_rows': len(rows), 'tab_total': total,
                                    'loading': bool(listing.get('loading')),
                                    'pagination': bool(listing.get('pagination'))}
        result['listing_complete'] = (
            isinstance(total, int) and total == len(rows) and
            all(row.get('caption_text') and row.get('updated_text') for row in rows) and
            not listing.get('loading') and not listing.get('pagination')
        )
        if not result['listing_complete']:
            result['blocker_code'] = 'PARTIAL_LISTING'
        return result

    def _verify_remote_draft_record(self, playwright, conn, draft_id, execution_id):
        draft = get_row(conn, 'publisher_drafts', draft_id)
        asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
        account = get_row(conn, 'publisher_accounts', draft['account_id'])
        if draft['persistence_status'] != 'UNCONFIRMED' or asset['status'] != 'NEEDS_RECONCILIATION':
            raise ValueError('DRAFT_NOT_VERIFY_ELIGIBLE')
        if not str(account.get('tiktok_username') or '').strip():
            raise ValueError('EXPECTED_ACCOUNT_REQUIRED')
        checkpoint = conn.execute(
            "SELECT id FROM publisher_execution_logs WHERE draft_id=? "
            "AND step IN ('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED') "
            'ORDER BY id DESC LIMIT 1', (draft_id,),
        ).fetchone()
        if not checkpoint:
            raise ValueError('REMOTE_EFFECT_CHECKPOINT_REQUIRED')
        started = now_iso()
        try:
            observation = self._inspect_draft_list_read_only(playwright, account)
        except Exception:
            observation = {'source_surface': 'TIKTOK_STUDIO_AUTH', 'observed_account': None,
                           'rows': [], 'listing_complete': False,
                           'blocker_code': 'LISTING_ERROR', 'listing_bounds': {}}
        save = conn.execute(
            "SELECT created_at FROM publisher_execution_logs WHERE draft_id=? "
            "AND step='SAVE_DRAFT' AND status='OK' ORDER BY id DESC LIMIT 1", (draft_id,),
        ).fetchone()
        duration = None
        path = Path(draft.get('temp_path') or '')
        if draft.get('temp_path') and path.resolve().is_relative_to(self.cache.root.resolve()) and path.is_file():
            try:
                duration = probe_video(path, str(ensure_ffprobe_available(self.root))).results.get('duration')
            except (OSError, FFProbeError):
                pass
        if observation.get('blocker_code') and observation['blocker_code'] != 'PARTIAL_LISTING':
            assessment = {'outcome': 'REMOTE_STATE_UNKNOWN', 'coverage_status': 'UNKNOWN',
                          'candidate_count': 0, 'matched_candidate_fingerprint': None,
                          'blocker_code': observation['blocker_code']}
        else:
            assessment = assess_remote_draft_rows(
                observation.get('rows') or [], draft['caption'], save['created_at'] if save else None,
                duration, listing_complete=observation.get('listing_complete') is True,
            )
        completed = datetime.now(timezone.utc)
        evidence_id = record_reconciliation_evidence(
            conn, draft_id=draft_id, media_asset_id=asset['id'], execution_id=execution_id,
            account_id=account['id'], expected_account=account['tiktok_username'],
            observed_account=observation.get('observed_account'),
            source_surface=observation['source_surface'], matcher_version='canonical-v1-strong',
            remote_effect_checkpoint_id=checkpoint['id'],
            verification_started_at=started, verification_completed_at=completed.isoformat(),
            valid_until=(completed + timedelta(minutes=5)).isoformat(),
            evidence_payload={'listing_bounds': observation.get('listing_bounds') or {},
                              'observed_duration': duration,
                              'pagination_observations': {'complete': observation.get('listing_complete') is True}},
            **assessment,
        )
        return {'evidence_id': evidence_id, 'remote_read_only': True,
                'local_evidence_append': True, 'outcome': assessment['outcome'],
                'coverage_status': assessment['coverage_status'],
                'blocker_code': assessment['blocker_code'],
                'candidate_count': assessment['candidate_count']}

    def run(self):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                while not self.stop_event.is_set():
                    self.process_diagnostic_requests()
                    self.poll_login_sessions()
                    try:
                        command, row_id = self.commands.get(timeout=.2)
                        execution_id = None
                        if command in {'prepare', 'reconcile', 'cleanup-reconciled'}:
                            row_id, execution_id = row_id
                    except queue.Empty:
                        for context in list(self.contexts.values()):
                            try:
                                if context.pages:
                                    context.pages[0].wait_for_timeout(10)
                            except Exception:
                                pass
                        continue
                    with closing(connect(self.db)) as conn:
                        if command == 'verify-draft':
                            draft_id, verify_execution_id, future = row_id
                            try:
                                if future.set_running_or_notify_cancel():
                                    future.set_result(self._verify_remote_draft_record(
                                        playwright, conn, draft_id, verify_execution_id,
                                    ))
                            except Exception as exc:
                                future.set_exception(exc)
                            finally:
                                with self.lock:
                                    key = (verify_execution_id, draft_id)
                                    if self.verification_requests.get(key) is future:
                                        self.verification_requests.pop(key, None)
                            continue
                        if command=='draft-one-probe':
                            from src.publisher.functional_probe import run_probe
                            operation,data,future=row_id
                            try:future.set_result(run_probe(self,conn,operation,data))
                            except Exception as exc:future.set_exception(exc)
                            continue
                        if command == 'controlled-login':
                            try:
                                self.handle_controlled_login(playwright, conn, row_id)
                            except Exception:
                                self.auth_test_lock.finish(self.auth_test_run_ids[row_id], 'UNKNOWN')
                                raise
                            continue
                        if command == 'phone-confirmed':
                            trace = self.login_traces.get(row_id)
                            if trace:
                                trace.phone_confirmed()
                            continue
                        if command == 'inspect-existing':
                            account_id, navigate = row_id
                            self.handle_existing_session(playwright, conn, account_id, navigate)
                            continue
                        try:
                            if command == "login":
                                self.handle_login(playwright, conn, row_id)
                            elif command == 'cleanup-reconciled':
                                draft = get_row(conn, 'publisher_drafts', row_id)
                                asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
                                checkpoint = conn.execute(
                                    "SELECT 1 FROM publisher_execution_logs "
                                    "WHERE draft_id=? AND step IN ('TIKTOK_DRAFT_RECONCILED','PERSISTED') "
                                    "AND status IN ('OK','READY_FOR_REVIEW_RECONCILED') LIMIT 1",
                                    (row_id,),
                                ).fetchone()
                                if not (
                                    draft['status'] == 'READY_FOR_REVIEW' and
                                    draft['persistence_status'] == 'PERSISTED' and
                                    asset['status'] == 'RESERVED' and
                                    checkpoint
                                ):
                                    raise PreparationError(
                                        'MANUAL_ACTION_REQUIRED',
                                        'Cleanup reconciliado sem checkpoints persistidos suficientes',
                                    )
                                conn.execute(
                                    "UPDATE publisher_execution_items SET status='CLEANUP',error_code=NULL "
                                    "WHERE execution_id=? AND draft_id=? AND status='PROCESSING'",
                                    (execution_id, row_id),
                                )
                                log(
                                    conn, row_id, 'IDEMPOTENCY_CHECK', 'DRAFT_ALREADY_PERSISTED',
                                    'Persistencia local ja confirmada; cleanup sem nova acao TikTok',
                                )
                                conn.commit()
                                if not cleanup_remote(conn, row_id, self.provider_factory(asset), self.root):
                                    raise PreparationError('MANUAL_ACTION_REQUIRED', 'Cleanup remoto pendente; origem preservada')
                                self.finish_item(conn, row_id, 'SUCCEEDED')
                                self._queue_pending_execution(conn, execution_id)
                                continue
                            elif command == 'reconcile':
                                draft = get_row(conn, 'publisher_drafts', row_id)
                                account = get_row(conn, 'publisher_accounts', draft['account_id'])
                                context = self.context(playwright, account)
                                self.browser_runtime.verify_auth(
                                    self.account_profile(account),
                                    lambda runtime_context: self._verify_runtime_session(account, runtime_context),
                                )
                                if len(context.pages) != 1:
                                    raise PreparationError(
                                        'MANUAL_ACTION_REQUIRED',
                                        'Reconciliacao exige a unica pagina existente; nenhuma aba sera criada',
                                    )
                                page = context.pages[0]
                                self.auth_diagnostics.attach_page(account['id'], page)
                                content_path = urlsplit(page.url).path.rstrip('/')
                                if content_path != '/tiktokstudio/content':
                                    page.goto(
                                        'https://www.tiktok.com/tiktokstudio/content',
                                        wait_until='domcontentloaded',
                                        timeout=30000,
                                    )
                                self.browser_runtime.verify_auth(
                                    self.account_profile(account),
                                    lambda runtime_context: self._verify_runtime_session(account, runtime_context),
                                )
                                page_text = page.locator('body').inner_text(timeout=5000)
                                folded = page_text.casefold()
                                if 'rascunhos' in folded and 'todos os computadores' not in folded:
                                    drafts_tab = page.locator(
                                        '[data-tt="Header_HeaderTabBar_Container"]'
                                    ).filter(has_text=re.compile(r'^Rascunhos\s+\d+$', re.I))
                                    if drafts_tab.count() != 1 or not drafts_tab.is_visible():
                                        raise PreparationError('MANUAL_ACTION_REQUIRED', 'Aba Rascunhos nao identificada de forma unica')
                                    drafts_tab.click(timeout=10000)
                                    page.wait_for_timeout(750)
                                    page_text = page.locator('body').inner_text(timeout=5000)
                                    folded = page_text.casefold()
                                if 'rascunhos' not in folded or 'todos os computadores' not in folded:
                                    raise PreparationError('MANUAL_ACTION_REQUIRED', 'Lista de rascunhos nao comprovada')
                                draft_rows = page.locator(
                                    '[data-tt="components_DraftRowLayout_FlexRow"]'
                                ).evaluate_all(
                                    """
                                    els => els.map(el => {
                                      const content = el.querySelector(
                                        '[data-tt="components_DraftRowLayout_FlexRow_2"]'
                                      );
                                      const updated = el.querySelector(
                                        '[data-tt="components_DraftRowLayout_FlexRow_3"]'
                                      );
                                      const contentLines = (content?.innerText || '')
                                        .split(/\\r?\\n/).map(x => x.trim()).filter(Boolean);
                                      return {
                                        caption_text: contentLines.slice(1).join('\\n'),
                                        duration_text: contentLines[0] || '',
                                        updated_text: (updated?.innerText || '').trim(),
                                        text: (el.innerText || '').trim(),
                                      };
                                    }).filter(row => row.caption_text && row.updated_text)
                                    """
                                )
                                checkpoint = conn.execute(
                                    "SELECT created_at FROM publisher_execution_logs "
                                    "WHERE draft_id=? AND step='SAVE_DRAFT' AND status='OK' "
                                    "ORDER BY id DESC LIMIT 1",
                                    (row_id,),
                                ).fetchone()
                                matched_row = select_existing_draft_row(
                                    draft_rows,
                                    draft['caption'],
                                    checkpoint['created_at'] if checkpoint else draft.get('last_execution_at'),
                                )
                                evidence_dir = self.root / 'data' / 'publisher' / 'evidence'
                                evidence_dir.mkdir(parents=True, exist_ok=True)
                                screenshot = evidence_dir / f"{row_id}-TIKTOK_DRAFT_RECONCILED-{now_iso().replace(':','-')}.png"
                                try:
                                    page.screenshot(path=str(screenshot), full_page=True)
                                    screenshot_value = str(screenshot.relative_to(self.root))
                                except Exception:
                                    screenshot_value = None
                                log(
                                    conn, row_id, 'TIKTOK_DRAFT_RECONCILED', 'OK',
                                    'Lista Rascunhos/Todos os computadores e rascunho associado por legenda + atualizacao '
                                    f"{matched_row['updated_text']}; nenhum upload",
                                    screenshot_value,
                                )
                                conn.commit()
                                timestamp = now_iso()
                                checks = {
                                    'file': True, 'upload': True, 'caption': True,
                                    'settings': True, 'durable': True, 'reconciled': True,
                                    'tiktok_draft_url': page.url, 'persisted_at': timestamp,
                                    'upload_confirmed_at': draft.get('upload_confirmed_at') or timestamp,
                                    'caption_applied_at': draft.get('caption_applied_at') or timestamp,
                                }
                                reconcile_received(conn, row_id, checks)
                                conn.execute(
                                    "UPDATE publisher_execution_items SET status='CLEANUP',error_code=NULL "
                                    "WHERE execution_id=? AND draft_id=?",
                                    (execution_id, row_id),
                                )
                                conn.commit()
                                draft = get_row(conn, 'publisher_drafts', row_id)
                                asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
                                if not cleanup_remote(conn, row_id, self.provider_factory(asset), self.root):
                                    raise PreparationError('MANUAL_ACTION_REQUIRED', 'Cleanup remoto pendente; origem preservada')
                                self.finish_item(conn, row_id, 'SUCCEEDED')
                                self._queue_pending_execution(conn, execution_id)
                                continue
                            elif command == "review":
                                with self.lock:
                                    page = self.pages.get(row_id)
                                if not page or page.is_closed():
                                    draft = get_row(conn,'publisher_drafts',row_id)
                                    if not draft.get('tiktok_draft_url'):
                                        raise PreparationError('MANUAL_ACTION_REQUIRED','Aba indisponivel; confira o estado no Studio antes de qualquer retry')
                                    account = get_row(conn,'publisher_accounts',draft['account_id'])
                                    page = self.context(playwright,account).new_page()
                                    page.goto(draft['tiktok_draft_url'],wait_until='domcontentloaded')
                                    with self.lock:
                                        self.pages[row_id] = page
                                page.bring_to_front()
                            elif command == 'cleanup':
                                draft = get_row(conn,'publisher_drafts',row_id)
                                asset = get_row(conn,'publisher_media_assets',draft['media_asset_id'])
                                cleanup_remote(conn,row_id,self.provider_factory(asset),self.root)
                            elif command == "release":
                                with self.lock:
                                    self.pages.pop(row_id, None)
                            else:
                                row = get_row(conn,'publisher_drafts',row_id)
                                if row['status'] != 'PREPARING' or not conn.execute("SELECT 1 FROM publisher_execution_items WHERE execution_id=? AND draft_id=? AND status='QUEUED'",(execution_id,row_id)).fetchone():
                                    continue
                                conn.execute("UPDATE publisher_execution_items SET status='PROCESSING' WHERE execution_id=? AND draft_id=? AND status='QUEUED'",(execution_id,row_id))
                                conn.execute("UPDATE publisher_batch_executions SET status='RUNNING' WHERE id IN (SELECT execution_id FROM publisher_execution_items WHERE draft_id=? AND status='PROCESSING')",(row_id,))
                                conn.commit()
                                self.prepare(playwright, conn, row_id)
                                auto_cleanup = bool(conn.execute(
                                    'SELECT auto_cleanup FROM publisher_batch_executions WHERE id=?',
                                    (execution_id,),
                                ).fetchone()[0])
                                if auto_cleanup:
                                    conn.execute(
                                        "UPDATE publisher_execution_items SET status='CLEANUP' "
                                        "WHERE execution_id=? AND draft_id=? AND status='PROCESSING'",
                                        (execution_id, row_id),
                                    )
                                    conn.commit()
                                    draft = get_row(conn, 'publisher_drafts', row_id)
                                    asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
                                    cleanup_ok = cleanup_remote(
                                        conn, row_id, self.provider_factory(asset), self.root,
                                    )
                                    if not cleanup_ok:
                                        raise PreparationError(
                                            'MANUAL_ACTION_REQUIRED',
                                            'Cleanup remoto pendente; origem preservada',
                                        )
                                self.finish_item(conn,row_id,'SUCCEEDED')
                        except Exception as exc:
                            code = getattr(exc, "code", "FAILED")
                            if command != "login":
                                row = get_row(conn, "publisher_drafts", row_id)
                                if row["status"] in {"PREPARING", "READY_FOR_REVIEW"} and command != 'cleanup' and not row.get('confirmed_at'):
                                    upload_started = command == 'prepare' and conn.execute(
                                        "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
                                        "AND step='UPLOAD_STARTED' AND status='OK' LIMIT 1",
                                        (row_id,),
                                    ).fetchone()
                                    transition_code = (
                                        'MANUAL_ACTION_REQUIRED' if code == BLOCKED_BY_ACCOUNT_PROTECTION
                                        else
                                        'MANUAL_ACTION_REQUIRED' if upload_started and code == 'FAILED'
                                        else code if row["status"] == "PREPARING"
                                        else "MANUAL_ACTION_REQUIRED"
                                    )
                                    transition(conn, row_id, transition_code, error=str(exc)[:1000])
                                log(conn, row_id, command, code, str(exc)[:1000])
                                if command == 'prepare':
                                    current = get_row(conn, 'publisher_drafts', row_id)
                                    no_upload = restore_pre_upload_failure(conn, row_id)
                                    if not no_upload:
                                        upload_started = conn.execute(
                                            "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
                                            "AND step='UPLOAD_STARTED' AND status='OK' LIMIT 1",
                                            (row_id,),
                                        ).fetchone()
                                        asset_status = (
                                            'NEEDS_RECONCILIATION'
                                            if upload_started or current.get('upload_status') in {
                                                'TRANSFER_IN_PROGRESS', 'TRANSFER_CONFIRMED', 'TRANSFER_UNKNOWN'
                                            }
                                            else 'FAILED'
                                        )
                                        conn.execute("UPDATE publisher_media_assets SET status=?,updated_at=? WHERE id=? AND status='PROCESSING'",(asset_status,now_iso(),row['media_asset_id']))
                                    ready = row.get('confirmed_at') and row['status']=='READY_FOR_REVIEW'
                                    self.finish_item(conn,row_id,'SUCCEEDED' if ready else 'WAITING_MANUAL' if code in {'NEEDS_LOGIN','MANUAL_ACTION_REQUIRED','STUDIO_CHANGED',BLOCKED_BY_ACCOUNT_PROTECTION} else 'FAILED',None if ready else code)
                                elif command == 'reconcile':
                                    self.finish_item(
                                        conn, row_id, 'WAITING_MANUAL',
                                        code if code in {'NEEDS_LOGIN', 'MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED'}
                                        else 'MANUAL_ACTION_REQUIRED',
                                    )
                                elif command == 'cleanup-reconciled':
                                    self.finish_item(
                                        conn, row_id, 'WAITING_MANUAL',
                                        code if code in {'NEEDS_LOGIN', 'MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED'}
                                        else 'MANUAL_ACTION_REQUIRED',
                                    )
                            else:
                                log(conn, None, "LOGIN", code, f"Conta {row_id}: {type(exc).__name__}; sem repeticao automatica")
                            conn.commit()
                self.close_owned_contexts()
        except Exception as exc:
            print(f"Publisher worker stopped: {safe_error('worker', exc)}", flush=True)
            with closing(connect(self.db)) as conn:
                for row in conn.execute("SELECT id FROM publisher_drafts WHERE status='PREPARING'").fetchall():
                    transition(conn, row[0], "FAILED", error=str(exc)[:1000])
                conn.commit()
                recover_sessions(conn)
            for run_id in self.auth_test_run_ids.values():
                self.auth_test_lock.finish(run_id, 'UNKNOWN')
            with self.lock:
                account_ids = list(self.account_locks)
            for account_id in account_ids:
                with self.account_lock(account_id), closing(connect(self.db)) as conn:
                    session = self.login_sessions.get(account_id)
                    if session:
                        session.page, session.queued, session.manual_pending = None, False, False
                        self.runtime_auth[account_id] = 'UNKNOWN'
            self.contexts.clear()
        finally:
            self.release_owned_profiles()

    def finish_item(self, conn, row_id, status, code=None):
        item = conn.execute("SELECT execution_id FROM publisher_execution_items WHERE draft_id=? AND status IN ('PROCESSING','CLEANUP') ORDER BY execution_id DESC LIMIT 1",(row_id,)).fetchone()
        if not item:
            return
        execution = item[0]
        conn.execute('UPDATE publisher_execution_items SET status=?,error_code=? WHERE execution_id=? AND draft_id=?',(status,code,execution,row_id))
        if code in {'NEEDS_LOGIN','MANUAL_ACTION_REQUIRED','STUDIO_CHANGED',BLOCKED_BY_ACCOUNT_PROTECTION}:
            for pending in conn.execute("SELECT draft_id FROM publisher_execution_items WHERE execution_id=? AND status='QUEUED'",(execution,)).fetchall():
                pending_row = get_row(conn, 'publisher_drafts', pending[0])
                if (pending_row['status'] == 'PREPARING' and
                    pending_row.get('upload_status') == 'NOT_STARTED' and
                    pending_row.get('processing_status') == 'NOT_STARTED' and
                    not pending_row.get('confirmed_at')):
                    conn.execute("UPDATE publisher_drafts SET status='DRAFT',error_code=NULL,updated_at=? WHERE id=?", (now_iso(), pending[0]))
                    if pending_row.get('media_asset_id'):
                        conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',consumed_by_draft_id=NULL,cleanup_error=NULL,updated_at=? WHERE id=? AND status='RESERVED'", (now_iso(), pending_row['media_asset_id']))
                    log(conn, pending[0], 'BATCH_NOT_STARTED', 'DRAFT', 'Batch pausado antes de qualquer upload; media preservada como AVAILABLE')
                else:
                    transition(conn,pending[0],'MANUAL_ACTION_REQUIRED',error=f'Lote pausado: {code}')
            conn.execute("UPDATE publisher_execution_items SET status='WAITING_MANUAL',error_code=? WHERE execution_id=? AND status='QUEUED'",(code,execution))
            conn.execute("UPDATE publisher_batch_executions SET status='PAUSED',finished_at=? WHERE id=?",(now_iso(),execution))
        elif not conn.execute("SELECT 1 FROM publisher_execution_items WHERE execution_id=? AND status IN ('QUEUED','PROCESSING','CLEANUP','WAITING_MANUAL')",(execution,)).fetchone():
            failed = conn.execute(
                "SELECT 1 FROM publisher_execution_items WHERE execution_id=? AND status='FAILED' LIMIT 1",
                (execution,),
            ).fetchone()
            conn.execute(
                "UPDATE publisher_batch_executions SET status=?,finished_at=? WHERE id=?",
                ('PAUSED' if failed else 'COMPLETED', now_iso(), execution),
            )
        conn.commit()

    def prepare(self, playwright, conn, row_id):
        self.assert_actions_allowed()
        draft = get_row(conn, "publisher_drafts", row_id)
        assert_upload_allowed(conn,row_id)
        if draft["status"] != "PREPARING":
            return
        PublisherService(conn, self.root).validate_fields(draft)
        account = get_row(conn, "publisher_accounts", draft["account_id"])
        preset = get_row(conn, "publisher_presets", draft["preset_id"]) if draft["preset_id"] else {"visibility": "PUBLIC", "comments": True}
        asset = get_row(conn,'publisher_media_assets',draft['media_asset_id'])
        PublisherService(conn,self.root).assert_media_available(asset,row_id)
        if not account.get('session_verified') or account.get('account_identity')!='ACCOUNT_CONFIRMED':
            raise PreparationError('NEEDS_LOGIN','Sessao e identidade devem estar previamente verificadas')
        try:
            ffprobe_bin = ensure_ffprobe_available(self.root)
        except FFProbeError as exc:
            raise PreparationError('LOCAL_DEPENDENCY_MISSING', str(exc)) from exc
        context=self.context(playwright,account)
        try:
            self.browser_runtime.verify_auth(
                self.account_profile(account),
                lambda runtime_context: self._verify_runtime_session(account, runtime_context),
            )
        except ValueError as exc:
            raise PreparationError('NEEDS_LOGIN', str(exc)) from exc
        if len(context.pages)>1:
            raise PreparationError('MANUAL_ACTION_REQUIRED','Mais de uma pagina; nenhuma nova aba sera aberta')
        page=context.pages[0] if context.pages else context.new_page()
        self.auth_diagnostics.attach_page(account['id'],page)
        with self.lock:
            self.pages[row_id] = page
        def closed():
            with self.lock:
                if self.pages.get(row_id) is page:
                    self.pages.pop(row_id, None)
        page.on("close", closed)
        conn.execute("UPDATE publisher_drafts SET last_execution_at=? WHERE id=?", (now_iso(), row_id))
        conn.commit()

        def evidence(step, message):
            folder = self.root / "data" / "publisher" / "evidence"
            folder.mkdir(parents=True, exist_ok=True)
            filename = folder / f"{row_id}-{step}-{now_iso().replace(':','-')}.png"
            try:
                page.screenshot(path=str(filename), full_page=True)
                log(conn, row_id, step, "OK", message, filename.relative_to(self.root))
            except Exception:
                log(conn, row_id, step, "INFO", message + " (screenshot indisponivel)")
            conn.commit()

        adapter = None
        try:
            mapping = json.loads((self.root / "config" / "publisher_studio.json").read_text(encoding="utf-8"))
            safety = AccountSafetyGuard(conn)
            mutation_keys = {
                'VIDEO_UPLOAD': f'draft:{row_id}:VIDEO_UPLOAD',
                'SAVE_DRAFT': f'draft:{row_id}:SAVE_DRAFT',
            }
            def before_mutation(operation):
                safety.start_mutation(account['id'], mutation_keys[operation], operation, draft_id=row_id)
                log(conn, row_id, 'ACCOUNT_SAFETY_GUARD', 'OK', f'{operation} autorizado e registrado antes do efeito remoto')
                conn.commit()
            def after_mutation(operation):
                safety.complete_mutation(account['id'], mutation_keys[operation])
                conn.commit()
            def observe_risk(text):
                signal = map_risk_signal(text)
                if signal:
                    safety.activate(account['id'], signal.state, signal.reason_code,
                                    'OBSERVED_TIKTOK_UI', evidence={'ui_marker': signal.reason_code, 'draft_id': row_id})
                    raise AccountProtectionBlocked(signal.state, signal.reason_code)
            adapter = StudioPage(page, mapping, before_mutation, after_mutation, observe_risk)
            if not mapping.get("validated") or not mapping.get('durable_confirmation_validated'):
                page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded")
                adapter.challenge()
                raise PreparationError("STUDIO_CHANGED", "Mapeamento de upload e persistencia pendente de validacao real")
            provider = self.provider_factory(asset)
            if not provider.exists(asset['remote_file_id']):
                raise PreparationError('UPLOAD_FAILED','Arquivo nao existe mais no Drive')
            conn.execute("UPDATE publisher_media_assets SET status='PROCESSING',updated_at=? WHERE id=?",(now_iso(),asset['id']))
            conn.commit()
            with self.cache.job(row_id,asset['size_bytes'],preserve=True) as directory:
                path = provider.download_temporary(asset,directory)
                evidence('DOWNLOADED','Download JIT validado')
                # Preserve the remote filename for TikTok identity validation, without trusting path components.
                safe_name = Path(asset['filename'].replace('\\','/')).name
                target = directory / safe_name
                if target != path:
                    path.rename(target)
                with conn:
                    conn.execute('UPDATE publisher_drafts SET temp_path=? WHERE id=?',(str(target),row_id))
                checklist = prepare_job(
                    adapter,draft,preset,target,evidence,ffprobe_bin=ffprobe_bin,
                    media_validator=lambda metadata: safety.validate_media(account['id'], metadata),
                )
                conn.execute("UPDATE publisher_drafts SET upload_status=?,processing_status=?,updated_at=? WHERE id=?",
                             (draft.get('_upload_status', 'TRANSFER_CONFIRMED'), draft.get('_processing_status', 'MEDIA_READY_FOR_EDIT'), now_iso(), row_id))
                conn.commit()
                if not confirm_received(conn,row_id,checklist):
                    raise PreparationError('MANUAL_ACTION_REQUIRED','TikTok recebeu a midia; evidencia pendente. Nao repetir upload.')
            log(conn,row_id,'CLEANUP_AVAILABLE','PENDING','TEMP e remoto preservados; finalizar consumo exige acao explicita')
            conn.commit()
        except Exception as exc:
            upload_started_checkpoint = bool(conn.execute(
                "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
                "AND step='UPLOAD_STARTED' AND status='OK' LIMIT 1",
                (row_id,),
            ).fetchone())
            # A local setInputFiles timeout can arrive after TikTok has already
            # rendered the editor. Inspect the existing page once, read-only;
            # never retry file selection from this branch.
            if upload_started_checkpoint and adapter is not None and not getattr(adapter, 'transfer_confirmed', False):
                try:
                    reconciled = adapter.inspect_current_upload(asset['filename'])
                    if reconciled['media_ready_for_edit']:
                        draft['_upload_status'] = 'TRANSFER_CONFIRMED'
                        draft['_processing_status'] = 'MEDIA_READY_FOR_EDIT'
                        log(
                            conn, row_id, 'TRANSFER_RECONCILED_FROM_DOM', 'OK',
                            'setInputFiles inconclusivo; Enviado + preview + editor confirmados sem novo upload',
                        )
                except Exception as reconcile_exc:
                    log(
                        conn, row_id, 'TRANSFER_RECONCILIATION', 'PENDING',
                        f'Reconciliacao DOM inconclusiva: {safe_error("upload_reconcile", reconcile_exc)}',
                    )
            upload_status = draft.get('_upload_status') or ('TRANSFER_CONFIRMED' if getattr(adapter, 'transfer_confirmed', False) else None)
            processing_status = draft.get('_processing_status')
            if upload_status:
                conn.execute("UPDATE publisher_drafts SET upload_status=?,processing_status=?,updated_at=? WHERE id=?",
                             (upload_status, processing_status or 'UNKNOWN', now_iso(), row_id))
                if upload_status in {'TRANSFER_IN_PROGRESS', 'TRANSFER_CONFIRMED', 'TRANSFER_UNKNOWN'}:
                    conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION',updated_at=? WHERE id=?",
                                 (now_iso(), asset['id']))
                    log(conn, row_id, 'TRANSFER_RECONCILIATION', 'PENDING',
                        'Possivel efeito TikTok detectado; nenhum novo upload sera permitido sem reconciliacao')
                conn.commit()
            evidence("ERROR", "Preparacao interrompida; consulte o log")
            if getattr(adapter,'save_attempted',False) and not get_row(conn,'publisher_drafts',row_id).get('confirmed_at'):
                conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION',updated_at=? WHERE id=?",(now_iso(),asset['id']))
                conn.commit()
                raise PreparationError('MANUAL_ACTION_REQUIRED','Salvar draft foi tentado; confirme no Studio antes de qualquer novo upload') from exc
            raise


def recover_sessions(conn):
    recover_auth(conn)
    with conn:
        for row in conn.execute("SELECT id,status FROM publisher_drafts WHERE status IN ('PREPARING','READY_FOR_REVIEW') AND confirmed_at IS NULL").fetchall():
            transition(conn, row["id"], "MANUAL_ACTION_REQUIRED", error="Servidor reiniciado; confirme o estado no Studio antes de preparar novamente")
        conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE status='PROCESSING'")
        conn.execute("UPDATE publisher_execution_items SET status='WAITING_MANUAL',error_code='SERVER_RESTARTED' WHERE status IN ('QUEUED','PROCESSING')")
        conn.execute("UPDATE publisher_batch_executions SET status='PAUSED',finished_at=? WHERE status IN ('QUEUED','RUNNING')",(now_iso(),))
