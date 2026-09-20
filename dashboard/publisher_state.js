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
    scored.sort((a, b) => b.score - a.score || a.label.localeCompare(b.label, 'pt-BR'));
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
      librarySelectionSummary() {
        const details = this.selectedLibraryDetails();
        const withoutProduct = details.filter(item => !item.product_name).length;
        const attention = details.filter(item => item.status !== 'AVAILABLE').length;
        return {
          selected: librarySelected.size,
          known: details.length,
          details,
          productCount: new Set(details.map(item => item.product_name || 'Sem produto')).size,
          ready: details.filter(item => item.status === 'AVAILABLE').length,
          withoutProduct,
          attention,
          canCreate: librarySelected.size > 0 && details.length === librarySelected.size && withoutProduct === 0 && attention === 0,
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

  return {create, normalizeSearch, searchProducts, localDateTime, protectionPresentation, ambiguousDraftPresentation};
});
