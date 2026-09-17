(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PublisherUIState = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
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

  return {create};
});
