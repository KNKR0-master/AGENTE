(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PublisherUIState = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function normalizeSearch(value) {
    return String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase()
      .replace(/[^a-z0-9]+/g, ' ').trim();
  }

  function stem(token) {
    return token.replace(/(acoes|acao|antes|ante|es|s)$/g, '');
  }

  function searchProducts(products, query, context, limit = 10) {
    const queryTokens = normalizeSearch(query).split(' ').filter(Boolean);
    const contextTokens = normalizeSearch(context).split(' ').filter(token => token.length > 2);
    const scored = (products || []).filter(product => product.active).map(product => {
      const primary = normalizeSearch(product.short_name || product.name);
      const full = normalizeSearch([product.short_name, product.name, ...(product.aliases || [])].join(' '));
      const words = full.split(' ').filter(Boolean);
      const matches = token => full.includes(token) || words.some(word => stem(word).includes(stem(token)) || stem(token).includes(stem(word)));
      if (queryTokens.length && !queryTokens.every(matches)) return null;
      let score = queryTokens.length ? 100 : 0;
      if (queryTokens.length && primary.startsWith(queryTokens.join(' '))) score += 30;
      score += contextTokens.filter(matches).length * 3;
      return {product, score, label: product.short_name || product.name};
    }).filter(Boolean);
    scored.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, 'pt-BR') || Number(a.product.id) - Number(b.product.id));
    return scored.slice(0, Math.max(1, Math.min(10, limit))).map(item => item.product);
  }

  const protectionLabels = {
    RATE_LIMITED: 'Operação pausada temporariamente',
    POSTING_LIMIT_REACHED: 'Limite de atividade atingido',
    SPAM_RISK: 'Risco de spam detectado',
    POSTING_BANNED: 'Publicações bloqueadas na conta',
    MANUAL_REVIEW_REQUIRED: 'Revisão manual necessária',
  };

  const reasonLabels = {
    RATE_LIMIT_EXCEEDED: 'O TikTok pediu uma pausa temporária nas ações desta conta.',
    POSTING_LIMIT_REACHED: 'O limite seguro de atividade desta conta foi atingido.',
    TOO_MANY_PENDING_SHARES: 'Há compartilhamentos pendentes demais para continuar com segurança.',
    SPAM_RISK: 'O TikTok apresentou um sinal de restrição de atividade.',
    ACCOUNT_POSTING_BANNED: 'O TikTok bloqueou novas publicações nesta conta.',
  };

  function localDateTime(value, now = new Date()) {
    if (!value) return null;
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return null;
    const sameDay = date.toDateString() === now.toDateString();
    const tomorrow = new Date(now); tomorrow.setDate(now.getDate() + 1);
    const time = date.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'});
    if (sameDay) return `Hoje às ${time}`;
    if (date.toDateString() === tomorrow.toDateString()) return `Amanhã às ${time}`;
    return `${date.toLocaleDateString('pt-BR', {day: '2-digit', month: '2-digit'})} às ${time}`;
  }

  function protectionPresentation(protection, now = new Date()) {
    if (!protection || protection.state === 'NORMAL') return null;
    const release = localDateTime(protection.cooldown_until, now);
    const cooldownExpired = !!protection.cooldown_until && new Date(protection.cooldown_until) <= now;
    let message = 'Novas ações no TikTok estão pausadas para proteger esta conta.';
    let nextAction = 'Revise o estado da conta antes de continuar.';
    if (protection.state === 'RATE_LIMITED' && release && !cooldownExpired) {
      message = 'Operação pausada temporariamente para proteger esta conta.';
      nextAction = `Novas ações estarão disponíveis após ${release}.`;
    } else if (protection.state === 'RATE_LIMITED' && cooldownExpired) {
      message = 'O período de pausa terminou.';
      nextAction = 'A próxima ação ainda será validada pelo sistema antes de continuar.';
    } else if (!protection.cooldown_until) {
      nextAction = 'Esta pausa não possui horário automático de liberação. Revise o estado da conta antes de continuar.';
    }
    return {
      title: protectionLabels[protection.state] || 'Conta protegida',
      message,
      nextAction,
      stateLabel: protectionLabels[protection.state] || protection.state,
      reasonLabel: reasonLabels[protection.reason_code] || 'O sistema registrou uma condição que exige revisão.',
      detectedAt: localDateTime(protection.observed_at || protection.activated_at, now) || 'Não informado',
      evidence: protection.evidence_summary || 'Nenhuma evidência adicional disponível.',
    };
  }

  function ambiguousDraftPresentation(draft) {
    if (!draft) return null;
    if (draft.upload_status === 'TRANSFER_UNKNOWN') return {
      title: 'Envio aguardando verificação',
      message: 'O TikTok pode ter recebido este vídeo. Não tente novamente enquanto a verificação estiver pendente.',
      action: 'Verificar status',
    };
    if (draft.media_status === 'NEEDS_RECONCILIATION' || draft.processing_status === 'NEEDS_RECONCILIATION') return {
      title: 'Rascunho aguardando verificação',
      message: 'Ainda não foi possível confirmar se o rascunho foi salvo. Como o TikTok pode já ter recebido o vídeo, não envie novamente. Use a verificação de status antes de qualquer nova tentativa.',
      action: 'Verificar status',
    };
    return null;
  }

  function create() {
    const librarySelected = new Set();
    const librarySelectionDetails = new Map();
    const messages = new Map();
    let sequence = 0;

    function visibleMessage() {
      const values = [...messages.values()];
      const errors = values.filter(message => message.error).sort((a, b) => a.sequence - b.sequence);
      if (errors.length) return errors[errors.length - 1];
      values.sort((a, b) => a.sequence - b.sequence);
      return values[values.length - 1] || null;
    }

    return {
      librarySelected,
      setLibrarySelection(id, checked, details) {
        const key = String(id);
        if (checked) {
          librarySelected.add(key);
          if (details) librarySelectionDetails.set(key, {...details, drive_file_id: key});
        } else {
          librarySelected.delete(key);
          librarySelectionDetails.delete(key);
        }
        return librarySelected.size;
      },
      setLibraryPageSelection(items, checked) {
        for (const item of items) {
          const details = item && typeof item === 'object' ? item : undefined;
          const id = details ? details.drive_file_id : item;
          this.setLibrarySelection(id, checked, details);
        }
        return librarySelected.size;
      },
      clearLibrarySelection() {
        librarySelected.clear();
        librarySelectionDetails.clear();
        return 0;
      },
      selectedLibraryDetails() {
        return [...librarySelectionDetails.values()];
      },
      librarySelectionSummary(maxDraftsPerBatch = Infinity) {
        const details = this.selectedLibraryDetails();
        const withoutProduct = details.filter(item => !item.product_name).length;
        const attention = details.filter(item => item.status !== 'AVAILABLE' || item.product_archived).length;
        const products = new Map();
        for (const item of details) {
          const key = item.product_id == null ? item.product_name || 'UNASSIGNED' : String(item.product_id);
          const group = products.get(key) || {key, name: item.product_short_name || item.product_name || 'Sem Product', count: 0};
          group.count++;
          products.set(key, group);
        }
        return {
          selected: librarySelected.size,
          known: details.length,
          details,
          productCount: products.size,
          products: [...products.values()],
          ready: details.filter(item => item.status === 'AVAILABLE' && !item.product_archived).length,
          withoutProduct,
          attention,
          canCreate: librarySelected.size > 0 && librarySelected.size <= maxDraftsPerBatch && details.length === librarySelected.size && withoutProduct === 0 && attention === 0,
        };
      },
      setMessage(scope, text, error) {
        if (!text) messages.delete(scope);
        else messages.set(scope, {scope, text: String(text), error: !!error, sequence: ++sequence});
        return visibleMessage();
      },
      clearMessage(scope) {
        messages.delete(scope);
        return visibleMessage();
      },
      visibleMessage,
    };
  }

  function driveSyncPresentation(record = {}, result = null, phase = 'IDLE') {
    const status = record.cursor_status && record.cursor_status !== 'READY'
      ? record.cursor_status : result?.status || record.cursor_status || 'UNINITIALIZED';
    const poll = record.polling || {};
    const counts = ['changes_seen', 'files_upserted', 'folders_upserted', 'items_marked_unavailable']
      .map(key => Number(result?.[key] || 0));
    const summary = result?.status === 'READY'
      ? counts[0] === 0 ? 'Tudo atualizado. Nenhuma alteração nova encontrada.'
        : `${counts[0]} alterações · ${counts[1]} arquivos · ${counts[2]} pastas · ${counts[3]} indisponíveis`
      : '';
    if (phase === 'SYNCING') return {label: 'Sincronizando', message: 'Consultando alterações no Drive.', needsRebuild: false, summary: ''};
    if (record.sync_in_progress || poll.status === 'RUNNING' || status === 'SYNC_IN_PROGRESS') return {
      label: 'Sincronizando', message: 'Já existe uma sincronização em andamento.',
      needsRebuild: false, manualSyncBlocked: true, summary: '',
    };
    if (status === 'FULL_SCAN_REQUIRED' || status === 'UNINITIALIZED') return {
      label: 'Verificação necessária', needsRebuild: true, summary: '',
      message: 'A sincronização incremental precisa ser reconstruída. Execute uma verificação completa do Drive.',
    };
    if (poll.enabled && poll.status === 'BACKOFF') return {
      label: 'Aguardando nova tentativa', needsRebuild: false, summary: '',
      message: 'Drive indisponível temporariamente. Uma nova tentativa está agendada.',
    };
    if (poll.enabled && poll.status === 'PAUSED' && ['AUTH_REQUIRED', 'ACCOUNT_MISMATCH'].includes(poll.paused_reason)) return {
      label: 'Sincronização pausada', needsRebuild: false, manualSyncBlocked: true, summary: '',
      message: poll.paused_reason === 'AUTH_REQUIRED' ? 'Google Drive precisa ser reconectado.'
        : 'A conta do Google Drive precisa ser revisada.',
    };
    if (status === 'AUTH_REQUIRED') return {
      label: 'Reconexão necessária', needsRebuild: false, manualSyncBlocked: true, summary: '',
      message: 'Reconecte o Google Drive para continuar. A sincronização não será repetida automaticamente.',
    };
    if (status === 'ERROR' || status === 'SYNC_IN_PROGRESS' || status === 'TRANSIENT_ERROR') return {
      label: 'Atenção necessária', needsRebuild: false, summary: '',
      message: status === 'SYNC_IN_PROGRESS' ? 'Já existe uma sincronização em andamento.'
        : 'Sincronização não concluída. Seus arquivos do Drive não foram modificados. Verifique o diagnóstico antes de tentar novamente.',
    };
    const lastSync = Date.parse(record.last_successful_sync_at || '');
    if (poll.enabled && poll.status === 'SCHEDULED' && Number.isFinite(lastSync) &&
        Date.now() - lastSync > 15 * 60 * 1000) return {
      label: 'Atenção necessária', needsRebuild: false, summary: '',
      message: 'O inventário do Drive não é atualizado há mais de 15 minutos. Confira a sincronização antes de usar as mídias.',
    };
    return {label: 'Atualizado', message: summary || 'Sincronização pronta.', summary, needsRebuild: false};
  }

  function drivePollingPresentation(poll = {}) {
    if (!poll.enabled) return 'Desativada';
    const actions = {
      AUTH_REQUIRED: 'Reconecte o Google Drive para continuar.',
      ACCOUNT_MISMATCH: 'Verifique a conta Google conectada.',
      FULL_SCAN_REQUIRED: 'É necessária uma verificação completa do Drive.',
      DATABASE_ERROR: 'Verifique o armazenamento local antes de continuar.',
      UNINITIALIZED: 'É necessária uma verificação completa do Drive.',
    };
    if (poll.status === 'PAUSED') return `Sincronização automática pausada. ${actions[poll.paused_reason] || 'Verifique os detalhes técnicos.'}`;
    if (poll.status === 'BACKOFF') return 'Drive indisponível temporariamente. Nova tentativa será feita mais tarde.';
    if (poll.status === 'RUNNING') return 'Verificando alterações no Drive.';
    return 'Sincronização automática ativa.';
  }

  function queueGroups(snapshot, filters = {}) {
    const executions = snapshot.executions || [];
    const items = snapshot.execution_items || [];
    const draftsById = new Map((snapshot.drafts || []).map(draft => [draft.id, draft]));
    const activeDraftIds = new Set();
    const groups = [];
    const matches = (draft, batchId) => queueMatches(draft, filters, null, snapshot);

    for (const execution of executions.filter(item => !['COMPLETED', 'SUCCESS'].includes(item.status))) {
      const executionItems = items.filter(item => item.execution_id === execution.id)
        .sort((a, b) => (a.execution_position ?? Number.MAX_SAFE_INTEGER) - (b.execution_position ?? Number.MAX_SAFE_INTEGER) || a.draft_id - b.draft_id);
      const all = executionItems.map(item => draftsById.get(item.draft_id)).filter(draft => draft && draft.status !== 'ARCHIVED');
      if (!all.length) continue;
      const positions = new Map(executionItems.map(item => [item.draft_id, item.execution_position]));
      const batchIds = [...new Set(all.map(draft => draft.batch_id).filter(id => id != null))];
      const batchId = batchIds.length === 1 ? batchIds[0] : null;
      const filtered = all.filter(draft => matches(draft, draft.batch_id));
      for (const draft of all) activeDraftIds.add(draft.id);
      groups.push({key: `execution:${execution.id}`, source: 'execution', batchId, batchIds,
        all, drafts: filtered, execution, frozen: true, positions,
        preflight: batchId == null ? null : snapshot.queue_preflights?.[batchId],
        fullyVisible: filtered.length === all.length});
    }

    const byBatch = new Map();
    for (const draft of snapshot.drafts || []) {
      if (activeDraftIds.has(draft.id) || ['ARCHIVED', 'PUBLISHED', 'SCHEDULED'].includes(draft.status)) continue;
      const key = draft.batch_id == null ? 'unbatched' : String(draft.batch_id);
      if (!byBatch.has(key)) byBatch.set(key, {batchId: draft.batch_id, all: []});
      byBatch.get(key).all.push(draft);
    }
    for (const {batchId, all} of byBatch.values()) {
      all.sort((a, b) => (a.queue_position ?? Number.MAX_SAFE_INTEGER) - (b.queue_position ?? Number.MAX_SAFE_INTEGER) || a.id - b.id);
      const positions = new Map(all.map(draft => [draft.id, draft.queue_position]));
      const source = all.every(draft => ['DRAFT', 'CAPTION_MISSING'].includes(draft.status)) ? 'pending' : 'review';
      const filtered = all.filter(draft => matches(draft, batchId));
      groups.push({key: `drafts:${batchId ?? 'unbatched'}`, source, batchId,
        batchIds: batchId == null ? [] : [batchId], all, drafts: filtered,
        execution: null, frozen: false, positions,
        preflight: batchId == null ? null : snapshot.queue_preflights?.[batchId],
        fullyVisible: filtered.length === all.length});
    }
    return groups.filter(group => group.drafts.length).sort((a, b) => {
      if (a.source === 'execution' && b.source !== 'execution') return -1;
      if (b.source === 'execution' && a.source !== 'execution') return 1;
      return Number(b.batchId || 0) - Number(a.batchId || 0) || String(a.key).localeCompare(String(b.key));
    });
  }

  function queueIdentity(item, executionItem = null) {
    const id = item.id ?? item.draft_id;
    const display = {...(item.queue_display || {label: 'Estado a verificar', kind: 'attention', message: ''})};
    if (executionItem?.item_status === 'FAILED' || executionItem?.status === 'FAILED') {
      display.label = 'Falhou nesta execução';
      display.kind = 'attention';
      display.message ||= 'Confira os detalhes antes de qualquer nova ação.';
    }
    return {
      filename: item.filename || `Vídeo do Draft ${id ?? 'sem identificação'}`,
      product: item.product_short_name || item.product_name || 'Produto não atribuído',
      batch: item.batch_name || 'Lote não informado',
      folder: item.folder_name || 'Pasta não disponível no inventário local',
      thumbnailUrl: item.remote_file_id ? `/api/publisher/library/thumbnail/${encodeURIComponent(item.remote_file_id)}` : null,
      display,
    };
  }

  function queueMatches(item, filters = {}, executionItem = null, snapshot = {}) {
    const display = queueIdentity(item, executionItem).display;
    const status = item.status ?? item.draft_status, batchId = item.batch_id ?? item.draft_batch_id;
    const draftId = item.id ?? item.draft_id;
    const preflight = snapshot.queue_preflights?.[batchId]?.items?.find(row => row.draft_id === draftId);
    const categories = {
      READY: ['DRAFT', 'READY_FOR_REVIEW'].includes(status) && display.kind !== 'attention' && item.persistence_status !== 'PERSISTED' && preflight?.ready !== false,
      RUNNING: status === 'PREPARING' || ['PROCESSING', 'CLEANUP'].includes(executionItem?.status ?? executionItem?.item_status),
      SAVED: item.persistence_status === 'PERSISTED' || ['PUBLISHED', 'SCHEDULED'].includes(status),
      ATTENTION: display.kind === 'attention',
      FAILED: ['FAILED', 'UPLOAD_FAILED'].includes(status) || (executionItem?.status ?? executionItem?.item_status ?? item.item_status) === 'FAILED',
    };
    const query = normalizeSearch(filters.search);
    return (!filters.status || (Object.hasOwn(categories, filters.status) ? categories[filters.status] : status === filters.status)) &&
      (!filters.attention || (filters.attention === 'REQUIRED') === (display.kind === 'attention')) &&
      (!filters.batch || batchId === Number(filters.batch)) &&
      (!filters.account || item.account_id === Number(filters.account)) &&
      (!filters.product || item.product_id === Number(filters.product)) &&
      (!filters.readiness || (filters.readiness === 'READY') === !!preflight?.ready) &&
      (!query || normalizeSearch([item.filename, item.product_short_name, item.product_name, item.caption, item.batch_name, item.folder_name].join(' ')).includes(query));
  }

  function queueHiddenReasons(snapshot, item, executionId = null) {
    const draftId = item.id ?? item.draft_id, batchId = item.batch_id ?? item.draft_batch_id;
    return (snapshot.queue_presentation?.targets || []).filter(target =>
      (target.target_type === 'DRAFT' && target.target_id === draftId) ||
      (target.target_type === 'BATCH' && target.target_id === batchId) ||
      (target.target_type === 'EXECUTION' && target.target_id === executionId));
  }

  function queueVisibleGroups(snapshot, filters = {}) {
    return queueGroups(snapshot).map(group => {
      const drafts = group.all.filter(draft => !queueHiddenReasons(snapshot, draft, group.execution?.id).length)
        .filter(draft => queueMatches(draft, filters,
          (snapshot.execution_items || []).find(item => item.execution_id === group.execution?.id && item.draft_id === draft.id), snapshot));
      return {...group, drafts, fullyVisible: drafts.length === group.all.length};
    }).filter(group => group.drafts.length);
  }

  function queueHiddenGroups(snapshot, history = [], filters = {}) {
    const groups = [], represented = new Set();
    const add = (group, candidates) => {
      const hidden = candidates.map(item => ({...item,
        queue_display: queueIdentity(item, group.source === 'history' ? item :
          (snapshot.execution_items || []).find(row => row.execution_id === group.execution?.id && row.draft_id === item.id)).display,
        hidden_reasons: queueHiddenReasons(snapshot, item, group.execution?.id)}))
        .filter(item => item.hidden_reasons.length);
      hidden.forEach(item => represented.add(item.id ?? item.draft_id));
      const items = hidden.filter(item => queueMatches(item, filters,
        group.source === 'history' ? item : (snapshot.execution_items || []).find(row => row.execution_id === group.execution?.id && row.draft_id === item.id), snapshot));
      if (items.length) groups.push({...group, items});
    };
    for (const group of queueGroups(snapshot)) add(group, group.all);
    const executions = new Map([...history, ...(snapshot.queue_presentation?.history || [])].map(execution => [execution.id, execution]));
    for (const execution of executions.values()) add({key: `history:${execution.id}`, source: 'history', execution}, execution.items || []);
    for (const target of snapshot.queue_presentation?.targets || []) {
      if (target.target_type === 'EXECUTION') continue;
      const candidates = (snapshot.drafts || []).filter(draft => !represented.has(draft.id) &&
        (target.target_type === 'DRAFT' ? draft.id === target.target_id : draft.batch_id === target.target_id));
      add({key: `hidden:${target.target_type}:${target.target_id}`, source: 'standalone', target}, candidates);
    }
    return groups;
  }

  function queueHistoryExecutions(snapshot, history = [], filters = {}) {
    return history.map(execution => ({...execution, items: (execution.items || []).filter(item =>
      !queueHiddenReasons(snapshot, item, execution.id).length && queueMatches(item, filters, item, snapshot))}))
      .filter(execution => execution.items.length || (!(history.find(row => row.id === execution.id)?.items || []).length &&
        !(snapshot.queue_presentation?.targets || []).some(target => target.target_type === 'EXECUTION' && target.target_id === execution.id) &&
        !Object.values(filters).some(Boolean)));
  }

  function queueDeepLinkLocation(snapshot, history, draftId, mediaId, batchId) {
    const draft = (snapshot.drafts || []).find(item => item.id === draftId && item.media_asset_id === mediaId && item.batch_id === batchId);
    if (!draft) return null;
    const active = queueGroups(snapshot).find(group => group.all.some(item => item.id === draftId));
    if (active) return {view: queueHiddenReasons(snapshot, draft, active.execution?.id).length ? 'HIDDEN' : 'ACTIVE',
      draftId, executionId: active.execution?.id ?? null, key: active.key};
    const executions = [...new Map([...history, ...(snapshot.queue_presentation?.history || [])].map(item => [item.id, item])).values()].sort((a, b) => b.id - a.id);
    for (const execution of executions) {
      const item = (execution.items || []).find(row => row.draft_id === draftId && row.media_asset_id === mediaId && (row.draft_batch_id ?? row.batch_id) === batchId);
      if (item) return {view: queueHiddenReasons(snapshot, item, execution.id).length ? 'HIDDEN' : 'HISTORY', draftId, executionId: execution.id};
    }
    return queueHiddenReasons(snapshot, draft).length ? {view: 'HIDDEN', draftId, executionId: null} : null;
  }

  function queueNavigationFilters(snapshot, filters, item, executionItem = null) {
    const context = executionItem?.item_status ? {...executionItem, status: executionItem.item_status} : executionItem;
    return Object.fromEntries(Object.entries(filters).map(([key, value]) =>
      [key, value && !queueMatches(item, {[key]: value}, context, snapshot) ? '' : value]));
  }

  function reorderedIds(group, draftId, targetId) {
    if (group.frozen || !group.fullyVisible || group.all.some(d => !d.queue_capabilities?.can_reorder)) return null;
    const ids = group.all.map(d => d.id);
    const from = ids.indexOf(draftId), to = ids.indexOf(targetId);
    if (from < 0 || to < 0 || from === to) return null;
    ids.splice(to, 0, ids.splice(from, 1)[0]);
    return ids;
  }

  function bulkAllowed(drafts, capability) {
    return !!drafts.length && drafts.every(d => d.queue_capabilities?.[capability]);
  }

  return {create, normalizeSearch, searchProducts, localDateTime, protectionPresentation, ambiguousDraftPresentation, driveSyncPresentation, drivePollingPresentation,
    queueGroups, queueIdentity, queueMatches, queueHiddenReasons, queueVisibleGroups, queueHiddenGroups,
    queueHistoryExecutions, queueDeepLinkLocation, queueNavigationFilters, reorderedIds, bulkAllowed};
});
