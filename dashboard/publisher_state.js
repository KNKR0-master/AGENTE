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

  return {create, normalizeSearch, searchProducts};
});
