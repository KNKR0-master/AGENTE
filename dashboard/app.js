let state = {
  products: [],
  tasks: [],
  content: [],
  contentSummary: null,
  integration: null,
  affiliate: null,
  reconciliation: { contents: [], products: [], local_contents: [], local_products: [] },
  drive: { folders: [], inventory: [], mapping_health: {}, classification_totals: {} },
  reportPreview: null,
  selectedId: null,
  productDetailTab: "overview",
  activeTab: "integration",
};

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

function moneyMinor(value) {
  return money.format(Number(value || 0) / 100);
}

async function api(path, options = {}) {
  const headers = options.body instanceof FormData ? {} : { "Content-Type": "application/json" };
  const response = await fetch(path, { headers, ...options });
  if (!response.ok) {
    const text = await response.text();
    try {
      const payload = JSON.parse(text);
      throw new Error(payload.error || `Erro ${response.status}`);
    } catch (error) {
      if (error instanceof SyntaxError) throw new Error(text || `Erro ${response.status}`);
      throw error;
    }
  }
  return response.json();
}

async function load() {
  const period = document.querySelector("#affiliatePeriod")?.value || "30";
  const orderType = document.querySelector("#affiliateOrderType")?.value || "ALL";
  const settlement = document.querySelector("#affiliateSettlement")?.value || "ALL";
  const affiliateQuery = new URLSearchParams({ days: period, order_type: orderType, settlement_status: settlement });
  const [summary, contentSummary, integration, affiliate, reconciliation, drive] = await Promise.all([
    api("/api/summary"),
    api("/api/content/summary"),
    api("/api/integrations/tiktok-shop/status"),
    api(`/api/integrations/tiktok-shop/affiliate-summary?${affiliateQuery}`),
    api("/api/integrations/tiktok-shop/reconciliation"),
    api("/api/drive/inventory"),
  ]);
  state.products = summary.products;
  state.tasks = summary.tasks;
  state.contentSummary = contentSummary;
  state.integration = integration;
  state.affiliate = affiliate;
  state.reconciliation = reconciliation;
  state.drive = drive;
  if (!state.selectedId && state.products.length) state.selectedId = state.products[0].id;
  renderSummary();
  renderProducts();
  renderProductOptions();
  if (state.selectedId) await renderDetail(state.selectedId);
  await loadContent();
  await renderNextContent();
  renderIntegration();
  renderDrive();
}

async function loadContent() {
  const filter = document.querySelector("#contentProductFilter")?.value || "";
  const data = await api(filter ? `/api/content?product_id=${filter}` : "/api/content");
  state.content = data.contents;
  renderContentList();
}

function renderSummary() {
  const today = state.affiliate?.windows?.today || {};
  const queued = state.contentSummary?.queued || 0;
  const ready = state.contentSummary?.drive_available || 0;

  document.querySelector("#summaryMetrics").innerHTML = [
    ["GMV hoje", moneyMinor(today.gmv_minor)],
    ["Pedidos hoje", number.format(Number(today.orders || 0))],
    ["Recebido hoje", moneyMinor(today.final_received_minor)],
    ["Disponíveis Drive", number.format(ready)],
    ["Na fila", number.format(queued)],
  ]
    .map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
}

function renderProducts() {
  document.querySelector("#productCount").textContent = state.products.length;
  document.querySelector("#products").innerHTML = state.products
    .map((product) => {
      const tag = product.status === "validated" ? "green" : product.decision === "descartar" ? "red" : "amber";
      return `
        <article class="product-row ${product.id === state.selectedId ? "active" : ""}" data-product-id="${product.id}">
          <h3>${escapeHtml(product.name)}</h3>
          <div class="product-meta">
            <span>${money.format(Number(product.revenue_total || 0))}</span>
            <span>${number.format(Number(product.sales_total || 0))} vendas</span>
            <span class="tag ${tag}">${escapeHtml(product.decision || product.status)}</span>
          </div>
        </article>
      `;
    })
    .join("");

  document.querySelectorAll(".product-row").forEach((row) => {
    row.addEventListener("click", async () => {
      state.selectedId = Number(row.dataset.productId);
      renderProducts();
      await renderDetail(state.selectedId);
    });
  });
}

function renderProductOptions() {
  const options = state.products
    .map((product) => `<option value="${product.id}">${escapeHtml(product.name)}</option>`)
    .join("");
  const importSelect = document.querySelector("#contentImportForm select[name='product_id']");
  if (importSelect) {
    importSelect.innerHTML = options;
    loadImportCaptions(importSelect.value).catch(() => {});
  }

  const filter = document.querySelector("#contentProductFilter");
  if (filter) {
    const current = filter.value;
    filter.innerHTML = `<option value="">Todos os produtos</option>${options}`;
    filter.value = current;
  }
}

async function loadImportCaptions(productId, selectedCaptionId = "") {
  const select = document.querySelector("#contentImportForm select[name='caption_id']");
  if (!select || !productId) return;
  const data = await api(`/api/product?id=${productId}`);
  select.innerHTML = `<option value="">Sem legenda salva</option>${(data.captions || []).map((caption) => `<option value="${caption.id}" ${String(caption.id) === String(selectedCaptionId) ? "selected" : ""}>${escapeHtml(caption.title)} - ${escapeHtml(caption.status)}</option>`).join("")}`;
}

async function renderDetailLegacy(productId) {
  const data = await api(`/api/product?id=${productId}`);
  const product = data.product;
  if (!product) return;
  const latest = data.snapshots[0] || {};
  const affiliate = data.affiliate_performance?.totals || {};
  const drive = data.drive_inventory;

  document.querySelector("#detail").innerHTML = `
    <div class="detail-header">
      <h2>${escapeHtml(product.name)}</h2>
      <div class="product-meta">
        <span>${escapeHtml(product.category || "Categoria não informada")}</span>
        <span>${escapeHtml(latest.date_range || "Sem janela")}</span>
        <span class="tag ${product.status === "validated" ? "green" : "amber"}">${escapeHtml(product.status)}</span>
      </div>
      <div class="detail-actions">
        <button data-action="validate">Marcar validado</button>
        <button data-action="monitor">Monitorar</button>
        <button data-action="discard">Descartar</button>
      </div>
    </div>

    <div class="detail-grid">
      ${metric("Receita total", money.format(Number(latest.revenue_total || 0)))}
      ${metric("Vendas", number.format(Number(latest.sales_total || 0)))}
      ${metric("Vídeos do mercado", number.format(Number(latest.video_count || 0)))}
      ${metric("Criadores", number.format(Number(latest.creator_count || 0)))}
      ${metric("GPM vídeos", money.format(Number(latest.gpm_video_export || 0)))}
      ${metric("CPA ads", money.format(Number(latest.cpa_ads || 0)))}
      ${metric("ROAS bruto", decimal.format(Number(latest.roas || 0)))}
      ${metric("Receita live", money.format(Number(latest.revenue_live || 0)))}
    </div>

    <div class="panel">
      <h3>Resultado afiliado próprio</h3>
      <div class="detail-grid compact-metrics">
        ${metric("GMV atribuído", money.format(Number(affiliate.gmv || 0)))}
        ${metric("Pedidos", number.format(Number(affiliate.orders || 0)))}
        ${metric("Comissão real", money.format(Number(affiliate.actual_commission || 0)))}
        ${metric("Reembolsos", money.format(Number(affiliate.refunds || 0)))}
      </div>
    </div>

    <div class="panel">
      <h3>Produção mínima</h3>
      <div class="task-grid">${renderTasks(data.tasks)}</div>
    </div>

    <div class="panel">
      <h3>Estoque próprio</h3>
      ${renderProductContent(data.contents)}
    </div>

    <div class="panel">
      <h3>Pastas e disponibilidade no Drive</h3>
      ${drive ? `
        <div class="detail-grid compact-metrics">
          ${metric("Disponíveis", number.format(drive.available_count || 0))}
          ${metric("Na fila", number.format(drive.queued_count || 0))}
          ${metric("Cobertura", drive.coverage_days == null ? "Sem taxa" : `${decimal.format(drive.coverage_days)} dias`)}
          ${metric("Saúde", escapeHtml(drive.content_supply_status || "-"))}
        </div>
        <div class="folder-path-list">
          ${(drive.folders || []).map((folder) => `<div><strong>${escapeHtml(folder.name)}</strong><span>${escapeHtml(folder.relative_path)}</span></div>`).join("") || '<div class="empty-mini">Nenhuma pasta confirmada para este produto.</div>'}
        </div>
      ` : '<div class="empty-mini">Inventário do Drive ainda não disponível.</div>'}
    </div>

    <div class="panel">
      <h3>Top criadores</h3>
      ${table(
        ["Criador", "Receita", "Vendas", "Vídeos", "Views"],
        data.creators.map((creator) => [
          `@${escapeHtml(creator.creator_username || creator.creator_name || "-")}`,
          money.format(Number(creator.revenue || 0)),
          number.format(Number(creator.sales || 0)),
          number.format(Number(creator.video_count || 0)),
          number.format(Number(creator.views || 0)),
        ]),
      )}
    </div>

    <div class="panel">
      <h3>Top vídeos do mercado</h3>
      ${table(
        ["Criador", "Receita", "Vendas", "Views", "Duração", "Gancho"],
        data.videos.map((video) => [
          `@${escapeHtml(video.creator_username || "-")}`,
          money.format(Number(video.revenue || 0)),
          number.format(Number(video.sales || 0)),
          number.format(Number(video.views || 0)),
          escapeHtml(video.duration || "-"),
          escapeHtml(video.description || "-"),
        ]),
      )}
    </div>
  `;

  document.querySelector("[data-action='validate']").addEventListener("click", () =>
    setProductStatus(product.id, "validated", "produzir"),
  );
  document.querySelector("[data-action='monitor']").addEventListener("click", () =>
    setProductStatus(product.id, "monitoring", "monitorar"),
  );
  document.querySelector("[data-action='discard']").addEventListener("click", () =>
    setProductStatus(product.id, "discarded", "descartar"),
  );
  document.querySelectorAll("[data-save-task]").forEach((button) => {
    button.addEventListener("click", () => saveTask(Number(button.dataset.saveTask)));
  });
  bindContentActions(document.querySelector("#detail"));
}

async function renderDetail(productId) {
  const data = await api(`/api/product?id=${productId}`);
  const product = data.product;
  if (!product) return;
  const latest = data.snapshots[0] || {};
  const affiliate = data.affiliate_performance?.totals || {};
  const drive = data.drive_inventory;
  const tabs = [["overview", "Visao geral"], ["videos", "Videos"], ["prompts", "Prompts"], ["captions", "Legendas"], ["performance", "Performance"], ["drive", "Drive"]];
  document.querySelector("#detail").innerHTML = `
    <div class="detail-header">
      <h2>${escapeHtml(product.name)}</h2>
      <div class="product-meta"><span>${escapeHtml(product.category || "Categoria nao informada")}</span><span>${escapeHtml(latest.date_range || "Sem janela")}</span><span class="tag ${product.status === "validated" ? "green" : "amber"}">${escapeHtml(product.status)}</span></div>
      <div class="detail-actions"><button data-action="validate">Marcar validado</button><button data-action="monitor">Monitorar</button><button data-action="discard">Descartar</button></div>
    </div>
    <nav class="product-detail-tabs" aria-label="Areas do produto">${tabs.map(([value, label]) => `<button class="ghost-button ${state.productDetailTab === value ? "selected" : ""}" data-product-tab="${value}">${label}</button>`).join("")}</nav>
    <div class="product-section ${state.productDetailTab === "overview" ? "active" : ""}" data-product-section="overview">
      <div class="detail-grid">${metric("Receita total", money.format(Number(latest.revenue_total || 0)))}${metric("Vendas", number.format(Number(latest.sales_total || 0)))}${metric("Videos do mercado", number.format(Number(latest.video_count || 0)))}${metric("Criadores", number.format(Number(latest.creator_count || 0)))}${metric("GPM videos", money.format(Number(latest.gpm_video_export || 0)))}${metric("CPA ads", money.format(Number(latest.cpa_ads || 0)))}${metric("ROAS bruto", decimal.format(Number(latest.roas || 0)))}${metric("Receita live", money.format(Number(latest.revenue_live || 0)))}</div>
      <div class="panel"><h3>Resultado afiliado proprio</h3><div class="detail-grid compact-metrics">${metric("GMV atribuido", money.format(Number(affiliate.gmv || 0)))}${metric("Pedidos", number.format(Number(affiliate.orders || 0)))}${metric("Comissao real", money.format(Number(affiliate.actual_commission || 0)))}${metric("Reembolsos", money.format(Number(affiliate.refunds || 0)))}</div></div>
      <div class="panel"><h3>Producao minima</h3><div class="task-grid">${renderTasks(data.tasks)}</div></div>
    </div>
    <div class="product-section ${state.productDetailTab === "videos" ? "active" : ""}" data-product-section="videos"><div class="panel"><h3>Estoque proprio</h3>${renderProductContent(data.contents)}</div></div>
    <div class="product-section ${state.productDetailTab === "prompts" ? "active" : ""}" data-product-section="prompts"><div class="panel product-asset-panel"><div class="section-heading"><h3>Prompts do Drive</h3><input type="search" data-prompt-search placeholder="Buscar nos prompts" /></div><div>${renderProductPrompts(data.prompts || [])}</div></div></div>
    <div class="product-section ${state.productDetailTab === "captions" ? "active" : ""}" data-product-section="captions"><div class="panel product-asset-panel"><div class="section-heading"><h3>Legendas de publicacao</h3><span>${number.format((data.captions || []).filter((item) => item.status === "READY").length)} prontas</span></div>${renderCaptionEditor(product.id)}<div>${renderProductCaptions(data.captions || [])}</div></div></div>
    <div class="product-section ${state.productDetailTab === "drive" ? "active" : ""}" data-product-section="drive"><div class="panel"><h3>Pastas e disponibilidade no Drive</h3>${drive ? `<div class="detail-grid compact-metrics">${metric("Estoque no Drive", number.format(drive.drive_stock_count || 0))}${metric("Prontos", number.format(drive.available_count || 0))}${metric("A revisar", number.format(drive.needs_review_count || 0))}${metric("Na fila", number.format(drive.queued_count || 0))}${metric("Prompts", number.format(drive.prompt_count || 0))}${metric("Legendas prontas", number.format(drive.ready_caption_count || 0))}${metric("Cobertura", drive.coverage_days == null ? "Sem taxa" : `${decimal.format(drive.coverage_days)} dias`)}${metric("Saude", escapeHtml(drive.content_supply_status || "-"))}</div><div class="folder-path-list">${(drive.folders || []).map((folder) => `<div><strong>${escapeHtml(folder.name)}</strong><span>${escapeHtml(folder.relative_path)}</span></div>`).join("") || '<div class="empty-mini">Nenhuma pasta confirmada para este produto.</div>'}</div>` : '<div class="empty-mini">Inventario do Drive ainda nao disponivel.</div>'}</div></div>
    <div class="product-section ${state.productDetailTab === "performance" ? "active" : ""}" data-product-section="performance"><div class="panel"><h3>Top criadores</h3>${table(["Criador", "Receita", "Vendas", "Videos", "Views"], data.creators.map((creator) => [`@${escapeHtml(creator.creator_username || creator.creator_name || "-")}`, money.format(Number(creator.revenue || 0)), number.format(Number(creator.sales || 0)), number.format(Number(creator.video_count || 0)), number.format(Number(creator.views || 0))]))}</div><div class="panel"><h3>Top videos do mercado</h3>${table(["Criador", "Receita", "Vendas", "Views", "Duracao", "Gancho"], data.videos.map((video) => [`@${escapeHtml(video.creator_username || "-")}`, money.format(Number(video.revenue || 0)), number.format(Number(video.sales || 0)), number.format(Number(video.views || 0)), escapeHtml(video.duration || "-"), escapeHtml(video.description || "-")]))}</div></div>`;

  document.querySelector("[data-action='validate']").addEventListener("click", () => setProductStatus(product.id, "validated", "produzir"));
  document.querySelector("[data-action='monitor']").addEventListener("click", () => setProductStatus(product.id, "monitoring", "monitorar"));
  document.querySelector("[data-action='discard']").addEventListener("click", () => setProductStatus(product.id, "discarded", "descartar"));
  document.querySelectorAll("[data-save-task]").forEach((button) => button.addEventListener("click", () => saveTask(Number(button.dataset.saveTask))));
  bindProductAssetActions(product.id, data.captions || []);
  bindContentActions(document.querySelector("#detail"));
}

function renderProductPrompts(prompts) {
  if (!prompts.length) return `<div class="empty-mini">Nenhum prompt sincronizado nas pastas vinculadas.</div>`;
  return `<div class="asset-list">${prompts.map((prompt) => `<article class="asset-item" data-prompt-card data-search="${escapeAttr(normalizeSearchText(`${prompt.title} ${prompt.source_filename} ${prompt.prompt_text}`))}"><div class="asset-heading"><div><strong>${escapeHtml(prompt.title)}</strong><small>${escapeHtml(prompt.folder_path || "-")} / ${escapeHtml(prompt.source_filename)}</small></div>${statusPill(prompt.status)}</div><textarea readonly>${escapeHtml(prompt.prompt_text)}</textarea><div class="asset-actions"><button data-copy-text>Copiar prompt</button><span class="copy-feedback" aria-live="polite"></span></div></article>`).join("")}</div>`;
}

function renderCaptionEditor(productId) {
  return `<form class="caption-editor" data-caption-form><input type="hidden" name="product_id" value="${productId}" /><input type="hidden" name="caption_id" /><input name="title" placeholder="Titulo da legenda" required /><textarea name="caption_text" rows="4" placeholder="Texto da legenda" required></textarea><input name="hashtags" placeholder="#hashtags" /><select name="status"><option value="DRAFT">Rascunho</option><option value="READY">Pronta</option><option value="USED">Usada</option></select><div class="asset-actions"><button type="submit">Salvar legenda</button><button type="reset" class="ghost-button">Limpar</button></div></form>`;
}

function renderProductCaptions(captions) {
  if (!captions.length) return `<div class="empty-mini">Nenhuma legenda criada para este produto.</div>`;
  return `<div class="asset-list">${captions.map((caption) => `<article class="asset-item" data-caption-card="${caption.id}"><div class="asset-heading"><div><strong>${escapeHtml(caption.title)}</strong><small>${number.format(caption.usage_count || 0)} usos</small></div>${statusPill(caption.status)}</div><textarea readonly>${escapeHtml(joinCaption(caption.caption_text, caption.hashtags))}</textarea><div class="asset-actions"><button data-copy-text>Copiar</button><button data-edit-caption class="ghost-button">Editar</button><button data-duplicate-caption class="ghost-button">Duplicar</button><button data-use-caption class="ghost-button">Usar no proximo video</button><button data-archive-caption class="ghost-button">Arquivar</button><span class="copy-feedback" aria-live="polite"></span></div></article>`).join("")}</div>`;
}

function bindProductAssetActions(productId, captions) {
  document.querySelectorAll("[data-product-tab]").forEach((button) => button.addEventListener("click", () => {
    state.productDetailTab = button.dataset.productTab;
    document.querySelectorAll("[data-product-tab]").forEach((item) => item.classList.toggle("selected", item === button));
    document.querySelectorAll("[data-product-section]").forEach((section) => section.classList.toggle("active", section.dataset.productSection === state.productDetailTab));
  }));
  document.querySelector("[data-prompt-search]")?.addEventListener("input", (event) => {
    const query = normalizeSearchText(event.target.value.trim());
    document.querySelectorAll("[data-prompt-card]").forEach((card) => { card.hidden = Boolean(query && !card.dataset.search.includes(query)); });
  });
  document.querySelectorAll("[data-copy-text]").forEach((button) => button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.closest("[data-prompt-card], [data-caption-card]").querySelector("textarea").value);
    const feedback = button.parentElement.querySelector(".copy-feedback");
    feedback.textContent = "Copiado.";
    setTimeout(() => { feedback.textContent = ""; }, 1600);
  }));
  const form = document.querySelector("[data-caption-form]");
  form?.addEventListener("submit", async (event) => {
    event.preventDefault();
    await api("/api/product/caption", { method: "POST", body: JSON.stringify(Object.fromEntries(new FormData(form))) });
    await renderDetail(productId);
  });
  document.querySelectorAll("[data-edit-caption]").forEach((button) => button.addEventListener("click", () => {
    const caption = captions.find((item) => String(item.id) === button.closest("[data-caption-card]").dataset.captionCard);
    if (!caption || !form) return;
    ["caption_id", "title", "caption_text", "hashtags", "status"].forEach((name) => { form.elements[name].value = caption[name] || ""; });
    form.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }));
  document.querySelectorAll("[data-duplicate-caption], [data-archive-caption]").forEach((button) => button.addEventListener("click", async () => {
    const captionId = button.closest("[data-caption-card]").dataset.captionCard;
    const action = button.hasAttribute("data-duplicate-caption") ? "duplicate" : "archive";
    await api("/api/product/caption", { method: "POST", body: JSON.stringify({ action, caption_id: captionId }) });
    await renderDetail(productId);
  }));
  document.querySelectorAll("[data-use-caption]").forEach((button) => button.addEventListener("click", async () => {
    const captionId = button.closest("[data-caption-card]").dataset.captionCard;
    const productSelect = document.querySelector("#contentImportForm [name='product_id']");
    productSelect.value = String(productId);
    await loadImportCaptions(productId, captionId);
    switchTab("content");
  }));
}

function renderProductContent(contents) {
  if (!contents.length) return `<div class="empty-mini">Nenhum vídeo importado para este produto.</div>`;
  return `<div class="compact-content">${contents.map(contentCard).join("")}</div>`;
}

function renderContentList() {
  const target = document.querySelector("#contentList");
  if (!target) return;
  if (!state.content.length) {
    target.innerHTML = `<div class="empty-state small">Nenhum conteúdo encontrado para o filtro atual.</div>`;
    return;
  }
  target.innerHTML = state.content.map(contentCard).join("");
  bindContentActions(target);
}

function contentCard(item) {
  const status = statusLabel(item.status);
  const validation = validationLabel(item.validation_status);
  return `
    <article class="content-item" data-content-id="${item.id}">
      <div class="content-main">
        <video controls preload="metadata" src="${item.video_url}"></video>
        <div>
          <div class="content-title">
            <strong>${escapeHtml(item.content_code)}</strong>
            <span>${escapeHtml(item.product_name || "")}</span>
          </div>
          <div class="product-meta">
            <span class="tag ${status.className}">${status.text}</span>
            <span class="tag ${validation.className}">${validation.text}</span>
            <span>${escapeHtml(item.resolution || "-")}</span>
            <span>${decimal.format(Number(item.duration || 0))}s</span>
          </div>
          <p class="caption-preview">${escapeHtml(joinCaption(item.caption, item.hashtags) || "Sem legenda registrada.")}</p>
          <div class="content-stats">
            <span>${number.format(Number(item.views || 0))} views</span>
            <span>${number.format(Number(item.orders || 0))} pedidos</span>
            <span>${money.format(Number(item.gmv || 0))} GMV</span>
            <span>${money.format(Number(item.actual_commission || item.estimated_commission || 0))} comissão</span>
            <span>${money.format(Number(item.refunds || 0))} reembolsos</span>
          </div>
          ${item.tiktok_video_id ? `<div class="source-line">TikTok ID ${escapeHtml(item.tiktok_video_id)}${item.tiktok_url ? ` · <a href="${escapeAttr(item.tiktok_url)}" target="_blank" rel="noreferrer">abrir publicação</a>` : ""}</div>` : ""}
          ${item.attribution_status === "UNAVAILABLE" ? `<div class="attribution-note">Atribuição de vendas indisponível</div>` : item.attribution_status === "OFFICIAL" ? `<div class="source-line">Fonte oficial · sincronizado ${formatDateTime(item.synced_at)}</div>` : `<div class="source-line">Atribuição por ID importado · fonte ${escapeHtml(item.performance_provider || "externa")}</div>`}
        </div>
      </div>
      <div class="content-actions">
        ${item.status === "NEEDS_REVIEW" ? `<button data-content-action="ready">Aprovar</button>` : ""}
        ${item.status === "READY" ? `<button data-content-action="queue">Colocar na fila</button>` : ""}
        ${item.status === "QUEUED" ? `<button data-content-action="unqueue">Tirar da fila</button>` : ""}
        ${item.status === "QUEUED" ? `<button data-content-action="sent">Enviado ao iPhone</button>` : ""}
        ${["QUEUED", "SENT_TO_PHONE"].includes(item.status) ? `<button data-content-action="published">Marcar publicado</button>` : ""}
        <button data-content-action="copy">Copiar legenda</button>
        <button data-content-action="drive">Salvar no Drive</button>
        <a class="button-link" href="${item.video_url}" target="_blank" rel="noreferrer">Abrir vídeo</a>
      </div>
    </article>
  `;
}

async function loadAffiliateSummary() {
  const period = document.querySelector("#affiliatePeriod").value;
  const orderType = document.querySelector("#affiliateOrderType").value;
  const settlement = document.querySelector("#affiliateSettlement").value;
  const query = new URLSearchParams({ days: period, order_type: orderType, settlement_status: settlement });
  state.affiliate = await api(`/api/integrations/tiktok-shop/affiliate-summary?${query}`);
  renderIntegration();
  renderSummary();
}

function renderIntegration() {
  const status = state.integration || {};
  const affiliate = state.affiliate || { totals: {}, top_products: [], top_contents: [] };
  const account = status.account;
  const connected = Boolean(account && !["NOT_CONNECTED", "TOKEN_EXPIRED"].includes(status.status));
  const canConnect = status.enabled && status.configured;
  const labels = {
    DISABLED: status.availability_reason === "BR_NOT_OPEN" ? "Provider oficial indisponível para BR" : "Desativada por configuração",
    NOT_CONNECTED: "Não conectada",
    CONNECTED: "Conectada",
    MISSING_SCOPE: "Escopos pendentes",
    API_NOT_APPROVED: "Aguardando aprovação da API",
    TOKEN_EXPIRED: "Autorização expirada",
    ERROR: "Erro de integração",
  };

  document.querySelector("#integrationIdentity").innerHTML = account
    ? `<strong>${escapeHtml(account.display_name || account.creator_username || "Creator")}</strong><span>@${escapeHtml(account.creator_username || "-")} · ${escapeHtml(account.market || "BR")}</span>`
    : `<strong>Affiliate Orders XLSX</strong><span>Provider ativo · TikTok Shop BR</span>`;

  const reportImport = status.report_import || {};
  const lastReport = reportImport.last_import;
  const reportHealthy = lastReport && ["COMPLETED", "COMPLETED_WITH_ERRORS"].includes(lastReport.status);
  const noticeClass = reportHealthy ? "success" : status.status === "ERROR" ? "error" : "warning";
  const detail = reportHealthy
    ? `${lastReport.file_name} · ${number.format(lastReport.rows_total || 0)} linhas · ${formatDateTime(lastReport.imported_at || lastReport.updated_at)}`
    : "Envie o relatório oficial Affiliate Orders para atualizar os indicadores comerciais.";
  document.querySelector("#integrationNotice").innerHTML = `
    <div class="integration-notice ${noticeClass}">
      <strong>${reportHealthy ? "Dados atualizados" : "Atualização pendente"}</strong>
      ${detail ? `<span>${escapeHtml(detail)}</span>` : ""}
    </div>`;

  const totals = affiliate.totals || {};
  document.querySelector("#affiliateMetrics").innerHTML = [
    ["GMV", moneyMinor(totals.gmv_minor)],
    ["Pedidos", number.format(Number(totals.orders || 0))],
    ["Itens vendidos", number.format(Number(totals.items_sold || 0))],
    ["Itens reembolsados", number.format(Number(totals.items_refunded || 0))],
    ["Comissão estimada", moneyMinor(totals.estimated_commission_minor)],
    ["Comissão real", moneyMinor(totals.actual_commission_minor)],
    ["Valor final recebido", moneyMinor(totals.final_received_minor)],
  ].map(([label, value]) => metric(label, value)).join("");

  const period = document.querySelector("#affiliatePeriod")?.value || "30";
  document.querySelector("#commercialPeriodLabel").textContent = period === "1" ? "Hoje" : `Últimos ${period} dias`;
  document.querySelector("#affiliateComparisons").innerHTML = [
    comparisonCard("Hoje vs ontem", affiliate.comparisons?.today_vs_yesterday),
    comparisonCard("7 dias vs anteriores", affiliate.comparisons?.days_7_vs_previous),
    comparisonCard("30 dias vs anteriores", affiliate.comparisons?.days_30_vs_previous),
  ].join("");

  const pipeline = affiliate.pipeline || {};
  document.querySelector("#pipelineMetrics").innerHTML = [
    ["Prontos", pipeline.ready],
    ["Na fila", pipeline.queued],
    ["Publicados hoje", pipeline.published_today],
    ["Publicados em 7 dias", pipeline.published_7d],
  ].map(([label, value]) => metric(label, number.format(Number(value || 0)))).join("");

  document.querySelector("#nextToPublish").innerHTML = table(
    ["Conteúdo", "Produto", "Status", "GMV produto 7d", "Pedidos 7d"],
    (affiliate.next_to_publish || []).map((item) => [
      escapeHtml(item.content_code),
      escapeHtml(item.product_name),
      `<span class="tag ${item.status === "QUEUED" ? "blue" : "green"}">${item.status === "QUEUED" ? "Na fila" : "Pronto"}</span>`,
      moneyMinor(item.product_gmv_7d_minor),
      number.format(Number(item.product_orders_7d || 0)),
    ]),
  );

  document.querySelector("#productionOpportunities").innerHTML = table(
    ["Produto", "Nível", "Score", "GMV 7d", "Prontos + fila", "Motivos"],
    (affiliate.production_opportunities || []).filter((item) => item.gmv_7d_minor > 0 || item.score >= 35).slice(0, 20).map((item) => [
      escapeHtml(item.name),
      `<span class="tag ${item.level === "HIGH" ? "red" : item.level === "MEDIUM" ? "amber" : "green"}">${escapeHtml(item.level)}</span>`,
      number.format(Number(item.score || 0)),
      moneyMinor(item.gmv_7d_minor),
      number.format(Number(item.ready_contents || 0) + Number(item.queued_contents || 0)),
      escapeHtml((item.reasons || []).join(" · ")),
    ]),
  );

  document.querySelector("#affiliateProducts").innerHTML = table(
    ["Produto", "Pedidos", "Itens", "GMV", "GMV 7d", "Comissão", "Recebido", "Reembolsos", "IDs vendendo"],
    (affiliate.top_products || []).map((item) => [
      escapeHtml(item.product_name),
      number.format(Number(item.orders || 0)),
      number.format(Number(item.items_sold || 0)),
      moneyMinor(item.gmv_minor),
      moneyMinor(item.gmv_7d_minor),
      moneyMinor(item.actual_commission_minor),
      moneyMinor(item.final_received_minor),
      number.format(Number(item.items_refunded || 0)),
      number.format(Number(item.selling_content_ids || 0)),
    ]),
  );
  const contentSort = document.querySelector("#affiliateContentSort")?.value || "gmv_minor";
  const topContents = [...(affiliate.top_contents || [])].sort(
    (a, b) => Number(b[contentSort] || 0) - Number(a[contentSort] || 0),
  );
  document.querySelector("#affiliateContent").innerHTML = table(
    ["Conteúdo / TikTok ID", "Tipo", "Produto", "Pedidos", "Itens", "GMV", "Comissão", "Recebido"],
    topContents.map((item) => [
      item.tiktok_url
        ? `<a href="${escapeAttr(item.tiktok_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.content_code || item.external_content_id)}</a>`
        : escapeHtml(item.content_code || item.external_content_id),
      escapeHtml(contentTypeLabel(item.content_type)),
      escapeHtml(item.product_name || "-"),
      number.format(Number(item.orders || 0)),
      number.format(Number(item.items_sold || 0)),
      moneyMinor(item.gmv_minor),
      moneyMinor(item.actual_commission_minor),
      moneyMinor(item.final_received_minor),
    ]),
  );

  document.querySelector("#affiliateShops").innerHTML = table(
    ["Loja", "Pedidos", "GMV", "Comissão", "Recebido"],
    (affiliate.top_shops || []).map((item) => [
      escapeHtml(item.shop_name || item.shop_code || "-"),
      number.format(Number(item.orders || 0)),
      moneyMinor(item.gmv_minor),
      moneyMinor(item.actual_commission_minor),
      moneyMinor(item.final_received_minor),
    ]),
  );
  document.querySelector("#affiliateContentTypes").innerHTML = table(
    ["Tipo", "Pedidos", "Itens", "GMV", "Comissão", "Recebido"],
    (affiliate.content_types || []).map((item) => [
      escapeHtml(contentTypeLabel(item.content_type)),
      number.format(Number(item.orders || 0)),
      number.format(Number(item.items_sold || 0)),
      moneyMinor(item.gmv_minor),
      moneyMinor(item.actual_commission_minor),
      moneyMinor(item.final_received_minor),
    ]),
  );

  renderReconciliation();
  renderReportPreview();
  document.querySelector("#videoAnalyticsStatus").innerHTML = `
    <strong>Video Analytics aguardando relatório oficial</strong>
    <span>CTR, conversão por view, GMV por mil views e pedidos por mil views permanecem como WAITING_FOR_VIDEO_ANALYTICS.</span>`;

  const run = status.last_sync_run;
  document.querySelector("#lastSyncSummary").textContent = run
    ? `Última execução: ${run.status} · ${number.format(run.orders_seen || 0)} pedidos · ${formatDateTime(run.finished_at || run.started_at)}`
    : "Sem sincronização oficial executada";
  document.querySelector("#reportImportPath").textContent = lastReport
    ? `Último: ${lastReport.file_name} · ${lastReport.status} · ${formatDateTime(lastReport.imported_at || lastReport.updated_at)}`
    : `Entrada automática: ${reportImport.directory || "não configurada"}`;
  document.querySelector("#connectTikTok").disabled = !canConnect;
  document.querySelector("#syncTikTok").disabled = !status.enabled || !connected;
  document.querySelector("#fullSyncTikTok").disabled = !status.enabled || !connected;
  document.querySelector("#disconnectTikTok").disabled = !account;
  document.querySelector("#connectTikTok").textContent = account ? "Reautorizar" : "Conectar Creator";
}

function renderDriveLegacy() {
  const drive = state.drive || {};
  const scan = drive.latest_scan;
  const classes = drive.classification_totals || {};
  const health = drive.mapping_health || {};
  const scanStatus = scan?.status || "NEVER_SCANNED";
  const statusLabels = {
    COMPLETE: "Completo",
    PARTIAL: "Parcial",
    ERROR: "Erro",
    NEVER_SCANNED: "Nunca verificado",
  };

  document.querySelector("#driveIdentity").textContent = [
    drive.account_email || "Conta não configurada",
    drive.root_folder_id ? `raiz ${drive.root_folder_id}` : "raiz não configurada",
    scan?.finished_at ? `última leitura ${formatDateTime(scan.finished_at)}` : null,
  ].filter(Boolean).join(" · ");

  let noticeClass = "warning";
  let noticeTitle = "Inventário aguardando leitura";
  let noticeDetail = "Configure a origem oficial e execute a atualização.";
  if (scanStatus === "COMPLETE") {
    noticeClass = "success";
    noticeTitle = "Árvore do Drive verificada";
    noticeDetail = `${number.format(scan.folders_succeeded || 0)} pastas lidas sem erro.`;
  } else if (scanStatus === "PARTIAL") {
    noticeTitle = "Leitura parcial do Drive";
    noticeDetail = `${number.format(scan.folders_failed || 0)} pasta(s) falharam; os resultados das demais foram preservados.`;
  } else if (scanStatus === "ERROR") {
    noticeClass = "error";
    noticeTitle = "Origem do Drive indisponível";
    noticeDetail = scan.error_message || "Não foi possível acessar a pasta raiz.";
  }
  document.querySelector("#driveNotice").innerHTML = `
    <div class="integration-notice ${noticeClass}">
      <strong>${escapeHtml(noticeTitle)}</strong>
      <span>${escapeHtml(noticeDetail)}</span>
    </div>`;

  document.querySelector("#driveMetrics").innerHTML = [
    ["Disponíveis", classes.AVAILABLE || 0],
    ["Na fila", classes.ALREADY_QUEUED || 0],
    ["Publicados", classes.ALREADY_PUBLISHED || 0],
    ["Revisar", classes.NEEDS_REVIEW || 0],
    ["Inválidos", classes.INVALID || 0],
    ["Duplicados", classes.DUPLICATE || 0],
  ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${number.format(value)}</strong></div>`).join("");

  document.querySelector("#driveOpportunities").innerHTML = table(
    ["Produto", "GMV 7d", "Pedidos 7d", "Disponíveis", "Fila", "Cobertura", "Tendência", "Estoque", "Prioridade"],
    (drive.inventory || []).map((item) => [
      `<strong>${escapeHtml(item.product_name)}</strong><small class="cell-detail">${escapeHtml((item.priority_reasons || []).join(" · ") || "Sem sinais suficientes")}</small>`,
      moneyMinor(item.gmv_7d_minor),
      number.format(item.orders_7d || 0),
      number.format(item.available_count || 0),
      number.format(item.queued_count || 0),
      item.coverage_days == null ? "-" : `${decimal.format(item.coverage_days)} d`,
      statusPill(item.momentum),
      statusPill(item.content_supply_status),
      `<strong>${number.format(item.priority_score || 0)}</strong><small class="cell-detail">${escapeHtml(item.data_confidence)} confiança</small>`,
    ]),
  );

  const mapped = health.MAPPED_TO_PRODUCT || 0;
  const review = health.NEEDS_REVIEW || 0;
  const confidence = health.average_confidence == null ? "-" : `${decimal.format(health.average_confidence * 100)}%`;
  document.querySelector("#driveMappingHealth").textContent = `${number.format(mapped)} mapeadas · ${number.format(review)} para revisar · confiança média ${confidence}`;
  document.querySelector("#driveFolders").innerHTML = renderDriveFolders(drive.folders || [], drive.products || []);
  bindDriveMappingActions();
  document.querySelector("#scanDrive").disabled = false;
  document.querySelector("#scanDrive").textContent = "Atualizar Drive";
  document.querySelector("#scanDrive").title = `Status atual: ${statusLabels[scanStatus] || scanStatus}`;
}

function renderDrive() {
  const drive = state.drive || {};
  const scan = drive.latest_scan;
  const classes = drive.classification_totals || {};
  const health = drive.mapping_health || {};
  const scanStatus = scan?.status || "NEVER_SCANNED";
  document.querySelector("#driveIdentity").textContent = [drive.account_email || "Conta nao configurada", scan?.operational_root_folder_name || "Perfil ativo nao configurado", scan?.finished_at ? `ultima leitura ${formatDateTime(scan.finished_at)}` : null].filter(Boolean).join(" · ");
  const notice = scanStatus === "COMPLETE"
    ? ["success", "Arvore operacional verificada", `${number.format(scan.folders_succeeded || 0)} pastas do perfil ativo lidas sem erro.`]
    : scanStatus === "PARTIAL"
      ? ["warning", "Leitura parcial do perfil ativo", `${number.format(scan.folders_failed || 0)} pasta(s) falharam.`]
      : scanStatus === "ERROR"
        ? ["error", "Origem do Drive indisponivel", scan?.error_message || "Falha de acesso."]
        : ["warning", "Inventario aguardando leitura", "Execute a atualizacao do perfil ativo."];
  document.querySelector("#driveNotice").innerHTML = `<div class="integration-notice ${notice[0]}"><strong>${escapeHtml(notice[1])}</strong><span>${escapeHtml(notice[2])}</span></div>`;
  document.querySelector("#driveMetrics").innerHTML = [["Disponiveis", classes.AVAILABLE || 0], ["Na fila", classes.ALREADY_QUEUED || 0], ["Publicados", classes.ALREADY_PUBLISHED || 0], ["Revisar", classes.NEEDS_REVIEW || 0], ["Invalidos", classes.INVALID || 0], ["Duplicados", classes.DUPLICATE || 0]].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${number.format(value)}</strong></div>`).join("");
  document.querySelector("#driveOpportunities").innerHTML = table(
    ["Produto", "GMV 7d", "Pedidos", "Estoque Drive", "Prontos", "Revisar", "Fila", "Prompts", "Legendas", "Cobertura", "Tendencia", "Status", "Prioridade"],
    (drive.inventory || []).map((item) => [
      `<strong>${escapeHtml(item.product_name)}</strong><small class="cell-detail">${escapeHtml((item.priority_reasons || []).join(" · ") || "Sem sinais suficientes")}</small>`, moneyMinor(item.gmv_7d_minor), number.format(item.orders_7d || 0), number.format(item.drive_stock_count || 0), number.format(item.available_count || 0), number.format(item.needs_review_count || 0), number.format(item.queued_count || 0), number.format(item.prompt_count || 0), number.format(item.ready_caption_count || 0), item.coverage_days == null ? "-" : `${decimal.format(item.coverage_days)} d`, statusPill(item.momentum), statusPill(item.content_supply_status), `<strong>${number.format(item.priority_score || 0)}</strong><small class="cell-detail">${escapeHtml(item.data_confidence)} confianca</small>`,
    ]),
  );
  const confidence = health.average_confidence == null ? "-" : `${decimal.format(health.average_confidence * 100)}%`;
  document.querySelector("#driveMappingHealth").textContent = `${number.format(health.product_folders || 0)} pastas de produto · ${number.format(health.mapped || 0)} mapeadas · ${number.format(health.needs_review || 0)} para revisar · ${number.format(health.with_prompts || 0)} com prompt · confianca ${confidence}`;
  document.querySelector("#driveProfiles").innerHTML = (drive.profiles || []).map((profile) => `<div class="drive-profile"><strong>${escapeHtml(profile.name)}</strong>${statusPill(profile.status)}</div>`).join("") || `<div class="empty-mini">Perfis ainda nao descobertos.</div>`;
  document.querySelector("#driveTree").innerHTML = renderDriveTree(drive.folders || []);
  const candidates = (drive.folders || []).filter((folder) => ["PRODUCT", "VARIANT", "UNKNOWN"].includes(folder.folder_type));
  document.querySelector("#driveFolders").innerHTML = renderDriveFolders(candidates, drive.products || [], drive.inventory || []);
  bindDriveMappingActions();
  document.querySelector("#driveFolderSearch")?.addEventListener("input", (event) => {
    const query = normalizeSearchText(event.target.value.trim());
    document.querySelectorAll("[data-drive-folder]").forEach((row) => { row.hidden = Boolean(query && !row.dataset.search.includes(query)); });
  });
  document.querySelector("#scanDrive").disabled = false;
  document.querySelector("#scanDrive").textContent = "Atualizar Drive";
}

function renderDriveTree(folders) {
  if (!folders.length) return `<div class="empty-mini">Arvore ativa ainda nao inventariada.</div>`;
  const ids = new Set(folders.map((folder) => folder.drive_folder_id));
  const children = new Map();
  folders.forEach((folder) => {
    const parent = ids.has(folder.parent_drive_folder_id) ? folder.parent_drive_folder_id : "ROOT";
    if (!children.has(parent)) children.set(parent, []);
    children.get(parent).push(folder);
  });
  const branch = (parent, depth = 0) => (children.get(parent) || []).sort((a, b) => a.name.localeCompare(b.name, "pt-BR")).map((folder) => {
    const nested = branch(folder.drive_folder_id, depth + 1);
    const label = `<span>${escapeHtml(folder.name)}</span>${statusPill(folder.folder_type)}<small>${number.format(folder.direct_video_count || 0)} videos · ${number.format(folder.direct_prompt_count || 0)} prompts</small>`;
    return nested ? `<details ${depth < 1 ? "open" : ""}><summary>${label}</summary><div class="drive-tree-children">${nested}</div></details>` : `<div class="drive-tree-leaf">${label}</div>`;
  }).join("");
  return branch("ROOT");
}

function renderDriveFoldersLegacy(folders, products) {
  if (!folders.length) return `<div class="empty-mini">Nenhuma pasta inventariada.</div>`;
  const rows = folders.map((folder) => {
    const currentValue = folder.product_id
      ? String(folder.product_id)
      : folder.suggested_product_id
        ? String(folder.suggested_product_id)
        : `status:${folder.mapping_status || "NEEDS_REVIEW"}`;
    const productOptions = products.map((product) =>
      `<option value="${product.id}" ${String(product.id) === currentValue ? "selected" : ""}>${escapeHtml(product.name)}</option>`,
    ).join("");
    const stateOptions = [
      ["NEEDS_REVIEW", "Precisa revisão"],
      ["CATEGORY_ONLY", "Somente categoria"],
      ["ADMINISTRATIVE", "Administrativa"],
      ["IGNORED", "Ignorar"],
    ].map(([value, label]) => `<option value="status:${value}" ${currentValue === `status:${value}` ? "selected" : ""}>${label}</option>`).join("");
    const suggestion = folder.suggested_product_name
      ? `Sugestão: ${folder.suggested_product_name} (${decimal.format(Number(folder.suggestion_confidence || 0) * 100)}%)`
      : folder.mapping_source || "Sem sugestão";
    return `<tr data-drive-folder="${escapeAttr(folder.drive_folder_id)}">
      <td><strong>${escapeHtml(folder.name)}</strong><small class="cell-detail">${escapeHtml(folder.relative_path)}</small></td>
      <td>${statusPill(folder.folder_type)}</td>
      <td class="number">${number.format(folder.direct_video_count || 0)}</td>
      <td>${statusPill(folder.scan_status)}${folder.scan_error ? `<small class="cell-detail error-text">${escapeHtml(folder.scan_error)}</small>` : ""}</td>
      <td><small>${escapeHtml(suggestion)}</small></td>
      <td>
        <div class="mapping-control">
          <select data-folder-product aria-label="Produto ou classificação da pasta">${stateOptions}${productOptions}</select>
          <button data-map-folder title="Confirmar mapeamento">Confirmar</button>
        </div>
        <div class="mapping-create">
          <input data-new-product placeholder="Novo produto" aria-label="Nome do novo produto" />
          <button data-create-folder-product class="ghost-button" title="Criar produto e mapear">Criar</button>
        </div>
      </td>
    </tr>`;
  }).join("");
  return `<table>
    <thead><tr><th>Pasta</th><th>Tipo</th><th>Vídeos</th><th>Scan</th><th>Diagnóstico</th><th>Mapeamento</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>`;
}

function renderDriveFolders(folders, products, inventories = []) {
  if (!folders.length) return `<div class="empty-mini">Nenhuma pasta candidata a produto.</div>`;
  const inventoryByProduct = new Map(inventories.map((item) => [String(item.product_id), item]));
  const rows = folders.map((folder) => {
    const currentValue = folder.product_id ? String(folder.product_id) : folder.suggested_product_id ? String(folder.suggested_product_id) : `status:${folder.mapping_status || "NEEDS_REVIEW"}`;
    const classificationLabels = {
      "status:CATEGORY_ONLY": "Marcada como nao e produto",
      "status:ADMINISTRATIVE": "Marcada como administrativa",
      "status:IGNORED": "Pasta ignorada",
    };
    const selectedProduct = products.find((product) => String(product.id) === currentValue);
    const selectedLabel = selectedProduct?.name || classificationLabels[currentValue] || "";
    const mappedInventory = folder.product_id ? inventoryByProduct.get(String(folder.product_id)) : null;
    const isMapped = folder.mapping_status === "MAPPED_TO_PRODUCT" && folder.product_id;
    const productOptions = products.map((product) => {
      const search = normalizeSearchText([product.name, product.tiktok_product_name, product.sku_internal_code, product.tiktok_product_id, ...(product.aliases || [])].filter(Boolean).join(" "));
      const detail = [moneyMinor(product.gmv_minor || 0), product.tiktok_product_id ? `TikTok ${product.tiktok_product_id}` : null, product.sku_internal_code ? `SKU ${product.sku_internal_code}` : null].filter(Boolean).join(" · ");
      return `<button type="button" class="product-dropdown-option" role="option" data-product-option data-product-value="${product.id}" data-product-label="${escapeAttr(product.name)}" data-product-search="${escapeAttr(search)}" aria-selected="${String(product.id) === currentValue}"><strong>${escapeHtml(product.name)}</strong><small>${escapeHtml(detail)}</small></button>`;
    }).join("");
    const states = [["CATEGORY_ONLY", "Marcar como nao e produto"], ["ADMINISTRATIVE", "Marcar como administrativa"], ["IGNORED", "Ignorar esta pasta"]]
      .map(([value, label]) => `<button type="button" class="product-dropdown-option classification-option" role="option" data-product-option data-product-value="status:${value}" data-product-label="${escapeAttr(label)}" data-product-search="${escapeAttr(normalizeSearchText(label))}" aria-selected="${currentValue === `status:${value}`}"><strong>${escapeHtml(label)}</strong></button>`).join("");
    const suggestion = folder.suggested_product_name ? `Sugestao: ${folder.suggested_product_name} (${decimal.format(Number(folder.suggestion_confidence || 0) * 100)}%)` : folder.mapping_source || "Sem sugestao";
    const mappingSummary = isMapped
      ? `<div class="mapping-confirmed"><strong>Vinculado</strong><span>${escapeHtml(folder.product_name || selectedLabel)}</span><small>${number.format(mappedInventory?.drive_stock_count || 0)} no Drive · ${number.format(mappedInventory?.available_count || 0)} prontos · ${number.format(mappedInventory?.needs_review_count || 0)} a revisar</small></div>`
      : `<div class="mapping-pending"><span>Aguardando produto</span></div>`;
    const canConfirm = currentValue !== "status:NEEDS_REVIEW";
    const rowSearch = normalizeSearchText(`${folder.name} ${folder.relative_path} ${folder.product_name || ""} ${folder.suggested_product_name || ""}`);
    return `<tr data-drive-folder="${escapeAttr(folder.drive_folder_id)}" data-search="${escapeAttr(rowSearch)}">
      <td><strong>${escapeHtml(folder.name)}</strong><small class="cell-detail">${escapeHtml(folder.relative_path)}</small></td>
      <td>${statusPill(folder.folder_type)}</td>
      <td class="number">${number.format(folder.direct_video_count || 0)}<small class="cell-detail">${number.format(folder.direct_prompt_count || 0)} prompts</small></td>
      <td>${statusPill(folder.scan_status)}${folder.scan_error ? `<small class="cell-detail error-text">${escapeHtml(folder.scan_error)}</small>` : ""}</td>
      <td>${statusPill(folder.mapping_status)}<small class="cell-detail">${escapeHtml(suggestion)}</small></td>
      <td>${mappingSummary}<div class="mapping-control"><div class="product-combobox" data-product-combobox><input data-product-filter type="search" placeholder="Digite nome, ID ou SKU" aria-label="Pesquisar produto existente" role="combobox" aria-autocomplete="list" aria-expanded="false" autocomplete="off" value="${escapeAttr(selectedLabel)}" /><button type="button" class="product-dropdown-toggle" data-product-toggle title="Abrir lista de produtos" aria-label="Abrir lista de produtos">&#9662;</button><input type="hidden" data-folder-product value="${escapeAttr(currentValue)}" /><div class="product-dropdown" data-product-dropdown role="listbox" hidden><div class="product-dropdown-heading" data-product-count>Produtos do faturamento · ${number.format(products.length)}</div>${productOptions}<div class="product-dropdown-empty" data-product-empty hidden>Nenhum produto encontrado.</div><div class="product-dropdown-heading classification-heading">Classificar pasta</div>${states}</div></div><button data-map-folder title="${canConfirm ? (isMapped ? "Atualizar vínculo" : "Confirmar vínculo") : "Selecione um produto na lista"}" ${canConfirm ? "" : "disabled"}>${isMapped ? "Atualizar" : "Confirmar"}</button></div></td>
    </tr>`;
  }).join("");
  return `<table><thead><tr><th>Pasta</th><th>Tipo</th><th>Arquivos</th><th>Scan</th><th>Diagnostico</th><th>Mapeamento</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function bindDriveMappingActions() {
  const closeDropdown = (combo) => {
    combo.querySelector("[data-product-dropdown]").hidden = true;
    combo.querySelector("[data-product-filter]").setAttribute("aria-expanded", "false");
  };
  const openDropdown = (combo) => {
    document.querySelectorAll("[data-product-combobox]").forEach((item) => { if (item !== combo) closeDropdown(item); });
    combo.querySelector("[data-product-dropdown]").hidden = false;
    combo.querySelector("[data-product-filter]").setAttribute("aria-expanded", "true");
  };
  document.querySelectorAll("[data-product-combobox]").forEach((combo) => {
    const input = combo.querySelector("[data-product-filter]");
    const hiddenValue = combo.querySelector("[data-folder-product]");
    const confirmButton = combo.closest("[data-drive-folder]").querySelector("[data-map-folder]");
    const options = [...combo.querySelectorAll("[data-product-option]")];
    const filterOptions = () => {
      const query = normalizeSearchText(input.value.trim());
      let visibleProducts = 0;
      options.forEach((option) => {
        const isClassification = option.classList.contains("classification-option");
        option.hidden = Boolean(query && !option.dataset.productSearch.includes(query));
        if (!isClassification && !option.hidden) visibleProducts += 1;
      });
      combo.querySelector("[data-product-empty]").hidden = visibleProducts > 0;
      combo.querySelector("[data-product-count]").textContent = `Produtos encontrados · ${number.format(visibleProducts)}`;
    };
    input.addEventListener("focus", () => { input.select(); filterOptions(); openDropdown(combo); });
    input.addEventListener("click", () => { filterOptions(); openDropdown(combo); });
    input.addEventListener("input", () => {
      hiddenValue.value = "status:NEEDS_REVIEW";
      confirmButton.disabled = true;
      confirmButton.title = "Selecione um produto na lista";
      filterOptions();
      openDropdown(combo);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeDropdown(combo);
      if (event.key === "Enter" && !combo.querySelector("[data-product-dropdown]").hidden) {
        const firstMatch = options.find((option) => !option.hidden && !option.classList.contains("classification-option"));
        if (firstMatch) {
          event.preventDefault();
          firstMatch.click();
        }
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        options.find((option) => !option.hidden)?.focus();
      }
    });
    combo.querySelector("[data-product-toggle]").addEventListener("click", () => {
      if (combo.querySelector("[data-product-dropdown]").hidden) {
        input.focus();
        filterOptions();
        openDropdown(combo);
      } else {
        closeDropdown(combo);
      }
    });
    options.forEach((option) => option.addEventListener("click", () => {
      hiddenValue.value = option.dataset.productValue;
      input.value = option.dataset.productLabel;
      confirmButton.disabled = false;
      confirmButton.title = option.dataset.productValue.startsWith("status:") ? "Confirmar classificação" : "Confirmar vínculo";
      options.forEach((item) => item.setAttribute("aria-selected", String(item === option)));
      closeDropdown(combo);
    }));
  });
  if (!state.driveDropdownDismissBound) {
    document.addEventListener("click", (event) => {
      document.querySelectorAll("[data-product-combobox]").forEach((combo) => {
        if (!combo.contains(event.target)) closeDropdown(combo);
      });
    });
    state.driveDropdownDismissBound = true;
  }
  document.querySelectorAll("[data-map-folder]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-drive-folder]");
      const selected = row.querySelector("[data-folder-product]").value;
      if (selected === "status:NEEDS_REVIEW") {
        showToast("Selecione um produto na lista antes de confirmar.", "error");
        row.querySelector("[data-product-filter]").focus();
        return;
      }
      const payload = { folder_id: row.dataset.driveFolder };
      if (selected.startsWith("status:")) {
        payload.mapping_status = selected.slice(7);
      } else {
        payload.product_id = Number(selected);
        payload.mapping_status = "MAPPED_TO_PRODUCT";
      }
      await saveDriveFolderMapping(button, payload);
    });
  });
  document.querySelectorAll("[data-create-folder-product]").forEach((button) => {
    button.addEventListener("click", async () => {
      const row = button.closest("[data-drive-folder]");
      const name = row.querySelector("[data-new-product]").value.trim();
      if (!name) return window.alert("Informe o nome do produto.");
      await saveDriveFolderMapping(button, {
        folder_id: row.dataset.driveFolder,
        create_product_name: name,
      });
    });
  });
}

async function saveDriveFolderMapping(button, payload) {
  button.disabled = true;
  const row = button.closest("[data-drive-folder]");
  const folderName = row?.querySelector("td strong")?.textContent || "Pasta";
  const folderFilter = document.querySelector("#driveFolderSearch")?.value || "";
  button.textContent = "Salvando...";
  try {
    const result = await api("/api/drive/folder-mapping", { method: "POST", body: JSON.stringify(payload) });
    const inventory = result.inventory;
    if (inventory) {
      const productName = String(inventory.product_name || "Produto").replace(/[.\s]+$/, "");
      const stock = Number(inventory.drive_stock_count || 0);
      const ready = Number(inventory.available_count || 0);
      const review = Number(inventory.needs_review_count || 0);
      const detail = stock
        ? `${number.format(stock)} no Drive, ${number.format(ready)} prontos e ${number.format(review)} a revisar.`
        : "Esta pasta nao possui videos detectados; o estoque permanece 0.";
      showToast(`${folderName} vinculada a ${productName}. ${detail}`, "success");
    } else {
      showToast(`${folderName} atualizada com sucesso.`, "success");
    }
    await load();
    switchTab("drive");
    const search = document.querySelector("#driveFolderSearch");
    if (search && folderFilter) {
      search.value = folderFilter;
      search.dispatchEvent(new Event("input", { bubbles: true }));
    }
  } catch (error) {
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Confirmar";
  }
}

function showToast(message, kind = "success") {
  let region = document.querySelector("#toastRegion");
  if (!region) {
    region = document.createElement("div");
    region.id = "toastRegion";
    region.className = "toast-region";
    region.setAttribute("aria-live", "polite");
    document.body.appendChild(region);
  }
  const toast = document.createElement("div");
  toast.className = `toast ${kind}`;
  toast.textContent = message;
  region.appendChild(toast);
  setTimeout(() => toast.remove(), 6500);
}

function statusPill(value) {
  const normalized = String(value || "-");
  const positive = ["COMPLETE", "SUCCESS", "HEALTHY", "MAPPED_TO_PRODUCT", "STABLE"].includes(normalized);
  const negative = ["ERROR", "CRITICAL", "INVALID", "DECLINING"].includes(normalized);
  return `<span class="status-pill ${positive ? "positive" : negative ? "negative" : "neutral"}">${escapeHtml(normalized.replaceAll("_", " "))}</span>`;
}

function comparisonCard(label, values) {
  const gmv = values?.gmv_minor || {};
  const rate = gmv.change_rate;
  const direction = Number(gmv.change || 0) > 0 ? "positive" : Number(gmv.change || 0) < 0 ? "negative" : "neutral";
  return `<div class="comparison-card">
    <span>${escapeHtml(label)}</span>
    <strong>${moneyMinor(gmv.current)}</strong>
    <small class="${direction}">${rate == null ? "Sem base comparável" : `${rate >= 0 ? "+" : ""}${decimal.format(rate * 100)}% em GMV`}</small>
  </div>`;
}

function contentTypeLabel(value) {
  return { VIDEO: "Vídeo", SHOWCASE: "Vitrine", LIVE: "Live", UNKNOWN: "Desconhecido" }[value] || value || "-";
}

function renderReportPreview(value = undefined) {
  const target = document.querySelector("#reportPreview");
  if (value !== undefined) state.reportPreview = value;
  const preview = state.reportPreview;
  if (!preview) {
    target.innerHTML = "";
    return;
  }
  const duplicateMessage = preview.duplicate
    ? `<div class="preview-warning">Este arquivo já foi importado. O reprocessamento atualizará vínculos e dados alterados sem duplicar pedidos.</div>`
    : "";
  target.innerHTML = `
    <div class="report-preview-card">
      <div class="preview-title"><div><strong>${escapeHtml(preview.filename)}</strong><span>${escapeHtml(preview.report_type)} · ${escapeHtml(preview.compatibility)}</span></div></div>
      ${duplicateMessage}
      <div class="preview-grid">
        ${previewValue("Período", `${formatDateTime(preview.period_start)} a ${formatDateTime(preview.period_end)}`)}
        ${previewValue("Pedidos", number.format(preview.orders || 0))}
        ${previewValue("Produtos / SKUs", `${number.format(preview.products || 0)} / ${number.format(preview.skus || 0)}`)}
        ${previewValue("Content IDs", number.format(preview.contents || 0))}
        ${previewValue("Novos", number.format(preview.orders_new || 0))}
        ${previewValue("Atualizados", number.format(preview.orders_updated || 0))}
        ${previewValue("Sem alteração", number.format(preview.orders_unchanged || 0))}
        ${previewValue("Vídeos sem vínculo", number.format(preview.contents_unmatched || 0))}
      </div>
      <div class="preview-actions">
        <button id="processAffiliateReport">${preview.duplicate ? "Reprocessar arquivo" : "Processar atualização"}</button>
        <button id="cancelAffiliatePreview" class="ghost-button">Cancelar</button>
      </div>
    </div>`;
  document.querySelector("#processAffiliateReport").addEventListener("click", processAffiliateReport);
  document.querySelector("#cancelAffiliatePreview").addEventListener("click", () => {
    state.reportPreview = null;
    renderReportPreview();
  });
}

function previewValue(label, value) {
  return `<div><span>${label}</span><strong>${value}</strong></div>`;
}

async function processAffiliateReport(event) {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/api/integrations/tiktok-shop/reports/process", {
      method: "POST",
      body: JSON.stringify({ batch_id: state.reportPreview.batch_id, reprocess: state.reportPreview.duplicate }),
    });
    window.alert(`${number.format(result.orders_new || 0)} pedidos novos e ${number.format(result.orders_updated || 0)} atualizados.`);
    state.reportPreview = null;
    await load();
  } catch (error) {
    window.alert(error.message);
    button.disabled = false;
  }
}

function renderReconciliation() {
  const data = state.reconciliation || {};
  const contentOptions = (data.local_contents || []).map(
    (item) => `<option value="${item.id}">${escapeHtml(item.content_code)} · ${escapeHtml(item.product_name)}</option>`,
  ).join("");
  document.querySelector("#contentReconciliation").innerHTML = table(
    ["TikTok Content ID", "Tipo", "Produto", "Pedidos", "GMV", "Comissão", "Status", "Ação"],
    (data.contents || []).slice(0, 150).map((item) => {
      let action = `<span class="muted">${escapeHtml(item.content_code || "-")}</span>`;
      if (item.external_content_type === "VIDEO" && item.status === "UNMATCHED") {
        action = `<div class="reconcile-actions" data-external-content-id="${escapeAttr(item.external_content_id)}">
          <select aria-label="Content interno"><option value="">Associar...</option>${contentOptions}</select>
          <button data-reconcile-content title="Associar ao conteúdo selecionado">Associar</button>
          <button data-content-status="LEGACY" class="ghost-button">Legado</button>
          <button data-content-status="IGNORED" class="ghost-button">Ignorar</button>
        </div>`;
      }
      return [
        `<span class="id-value">${escapeHtml(item.external_content_id)}</span>`,
        escapeHtml(contentTypeLabel(item.external_content_type)),
        escapeHtml(item.product_name || "-"),
        number.format(Number(item.orders || 0)),
        moneyMinor(item.gmv_minor),
        moneyMinor(item.actual_commission_minor),
        `<span class="tag ${item.status === "MATCHED" ? "green" : item.status === "UNMATCHED" ? "amber" : ""}">${escapeHtml(item.status)}</span>`,
        action,
      ];
    }),
  );
  const productOptions = (data.local_products || []).map(
    (item) => `<option value="${item.id}">${escapeHtml(item.name)}</option>`,
  ).join("");
  document.querySelector("#productReconciliation").innerHTML = table(
    ["Produto TikTok", "Loja", "SKUs", "Pedidos", "GMV", "Ação"],
    (data.products || []).slice(0, 100).map((item) => [
      `<strong>${escapeHtml(item.external_product_name || item.external_product_id)}</strong><div class="id-value">${escapeHtml(item.external_product_id)}</div>`,
      escapeHtml(item.shop_name || "-"),
      number.format(Number(item.skus || 0)),
      number.format(Number(item.orders || 0)),
      moneyMinor(item.gmv_minor),
      `<div class="reconcile-actions" data-external-product-id="${escapeAttr(item.external_product_id)}"><select aria-label="Produto local"><option value="">Associar...</option>${productOptions}</select><button data-reconcile-product>Associar</button></div>`,
    ]),
  );
  bindReconciliationActions();
}

function bindReconciliationActions() {
  document.querySelectorAll("[data-reconcile-content]").forEach((button) => {
    button.addEventListener("click", async () => {
      const holder = button.closest("[data-external-content-id]");
      const contentId = holder.querySelector("select").value;
      if (!contentId) return window.alert("Selecione um Content interno.");
      await api("/api/integrations/tiktok-shop/reconciliation/content", { method: "POST", body: JSON.stringify({ external_content_id: holder.dataset.externalContentId, content_id: contentId }) });
      await load();
    });
  });
  document.querySelectorAll("[data-content-status]").forEach((button) => {
    button.addEventListener("click", async () => {
      const holder = button.closest("[data-external-content-id]");
      await api("/api/integrations/tiktok-shop/reconciliation/content-status", { method: "POST", body: JSON.stringify({ external_content_id: holder.dataset.externalContentId, status: button.dataset.contentStatus }) });
      await load();
    });
  });
  document.querySelectorAll("[data-reconcile-product]").forEach((button) => {
    button.addEventListener("click", async () => {
      const holder = button.closest("[data-external-product-id]");
      const productId = holder.querySelector("select").value;
      if (!productId) return window.alert("Selecione um produto local.");
      await api("/api/integrations/tiktok-shop/reconciliation/product", { method: "POST", body: JSON.stringify({ external_product_id: holder.dataset.externalProductId, product_id: productId }) });
      await load();
    });
  });
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? escapeHtml(value) : date.toLocaleString("pt-BR");
}

async function renderNextContent() {
  const target = document.querySelector("#nextContent");
  if (!target) return;
  const data = await api("/api/publishing/next");
  if (!data.content) {
    target.innerHTML = `<div class="empty-mini">Fila vazia para hoje.</div>`;
    return;
  }
  const item = data.content;
  target.innerHTML = `
    <div class="next-card" data-content-id="${item.id}">
      <video controls preload="metadata" src="${item.video_url}"></video>
      <h3>${escapeHtml(item.content_code)}</h3>
      <p>${escapeHtml(item.product.name)}</p>
      <textarea readonly>${escapeHtml(joinCaption(item.caption, item.hashtags))}</textarea>
      <div class="next-prompts">
        ${(item.prompts || []).map((prompt) => `<div class="next-prompt"><strong>${escapeHtml(prompt.title)}</strong><textarea readonly>${escapeHtml(prompt.prompt_text)}</textarea><button data-copy-next-prompt>Copiar prompt</button></div>`).join("") || '<div class="empty-mini">Sem prompt vinculado.</div>'}
      </div>
      <div class="content-actions">
        <button data-content-action="copy-next">Copiar legenda</button>
        <button data-content-action="sent">Enviado ao iPhone</button>
        <button data-content-action="published">Marcar publicado</button>
        <a class="button-link" href="${item.video_url}" target="_blank" rel="noreferrer">Abrir vídeo</a>
      </div>
    </div>
  `;
  bindContentActions(target);
  target.querySelectorAll("[data-copy-next-prompt]").forEach((button) => button.addEventListener("click", async () => {
    await navigator.clipboard.writeText(button.parentElement.querySelector("textarea").value);
    button.textContent = "Copiado";
    setTimeout(() => { button.textContent = "Copiar prompt"; }, 1600);
  }));
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
}

function renderTasks(tasks) {
  if (!tasks.length) {
    return `<div class="empty-mini">Marque o produto como validado para criar as versões 1 e 2.</div>`;
  }
  return tasks
    .map(
      (task) => `
        <article class="task" data-task="${task.id}">
          <strong>Versão ${task.version_number}</strong>
          <select name="status">
            ${["planejado", "gravado", "editado", "postado", "programado", "medido"].map(
              (status) => `<option value="${status}" ${status === task.status ? "selected" : ""}>${status}</option>`,
            )}
          </select>
          <input name="hook" placeholder="Gancho" value="${escapeAttr(task.hook || "")}" />
          <input name="format" placeholder="Formato" value="${escapeAttr(task.format || "")}" />
          <input name="posted_at" placeholder="Postado em" value="${escapeAttr(task.posted_at || "")}" />
          <input name="scheduled_at" placeholder="Programado para" value="${escapeAttr(task.scheduled_at || "")}" />
          <textarea name="notes" placeholder="Observações">${escapeHtml(task.notes || "")}</textarea>
          <button data-save-task="${task.id}">Salvar</button>
        </article>
      `,
    )
    .join("");
}

function table(headers, rows) {
  if (!rows.length) return `<div class="empty-mini">Sem dados para exibir.</div>`;
  return `
    <table>
      <thead><tr>${headers.map((header) => `<th>${header}</th>`).join("")}</tr></thead>
      <tbody>
        ${rows
          .map(
            (row) =>
              `<tr>${row
                .map((cell, index) => `<td class="${index > 0 && index < 4 ? "number" : ""}">${cell}</td>`)
                .join("")}</tr>`,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function setProductStatus(productId, status, decision) {
  await api("/api/product/status", {
    method: "POST",
    body: JSON.stringify({ product_id: productId, status, decision }),
  });
  await load();
}

async function saveTask(taskId) {
  const task = document.querySelector(`[data-task="${taskId}"]`);
  const payload = { task_id: taskId };
  task.querySelectorAll("input, select, textarea").forEach((field) => {
    payload[field.name] = field.value;
  });
  await api("/api/task", { method: "POST", body: JSON.stringify(payload) });
  await renderDetail(state.selectedId);
}

async function handleContentAction(action, contentId, container) {
  if (action === "ready") {
    await api("/api/content/status", {
      method: "POST",
      body: JSON.stringify({ content_id: contentId, status: "READY" }),
    });
  }
  if (action === "queue") {
    await api("/api/content/queue", {
      method: "POST",
      body: JSON.stringify({ content_id: contentId }),
    });
  }
  if (action === "unqueue") {
    await api("/api/content/unqueue", {
      method: "POST",
      body: JSON.stringify({ content_id: contentId }),
    });
  }
  if (action === "sent") {
    await api(`/api/publishing/${contentId}/sent-to-phone`, { method: "POST", body: "{}" });
  }
  if (action === "published") {
    const tiktokUrl = window.prompt("Cole a URL do vídeo publicado no TikTok, se tiver:");
    await api(`/api/publishing/${contentId}/published`, {
      method: "POST",
      body: JSON.stringify({ tiktok_url: tiktokUrl || null }),
    });
  }
  if (action === "drive") {
    await api("/api/content/drive/upload", {
      method: "POST",
      body: JSON.stringify({ content_id: contentId }),
    });
  }
  if (action === "copy" || action === "copy-next") {
    const text =
      container.querySelector("textarea")?.value ||
      container.querySelector(".caption-preview")?.textContent ||
      "";
    await navigator.clipboard.writeText(text);
    return;
  }
  await refreshOperationalViews();
}

function bindContentActions(root) {
  root.querySelectorAll("[data-content-action]").forEach((button) => {
    button.addEventListener("click", async () => {
      const holder = button.closest("[data-content-id]");
      const contentId = Number(holder.dataset.contentId);
      button.disabled = true;
      try {
        await handleContentAction(button.dataset.contentAction, contentId, holder);
      } catch (error) {
        window.alert(error.message);
      } finally {
        button.disabled = false;
      }
    });
  });
}

async function refreshOperationalViews() {
  state.contentSummary = await api("/api/content/summary");
  renderSummary();
  if (state.selectedId) await renderDetail(state.selectedId);
  await loadContent();
  await renderNextContent();
}

function statusLabel(status) {
  const labels = {
    DRAFT: ["Rascunho", "amber"],
    PROCESSING: ["Processando", "amber"],
    NEEDS_REVIEW: ["Revisar", "amber"],
    READY: ["Pronto", "green"],
    QUEUED: ["Na fila", "blue"],
    SENT_TO_PHONE: ["No iPhone", "blue"],
    PUBLISHED: ["Publicado", "green"],
    FAILED: ["Falhou", "red"],
    ARCHIVED: ["Arquivado", ""],
  };
  const value = labels[status] || [status, ""];
  return { text: value[0], className: value[1] };
}

function validationLabel(status) {
  const labels = {
    PASS: ["Validado", "green"],
    WARNING: ["Aviso técnico", "amber"],
    FAIL: ["Inválido", "red"],
  };
  const value = labels[status] || [status, ""];
  return { text: value[0], className: value[1] };
}

function joinCaption(caption, hashtags) {
  return [caption, hashtags].filter(Boolean).join("\n\n").trim();
}

function switchTab(tab) {
  state.activeTab = tab;
  document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));
  document.querySelector(`#${tab}Panel`).classList.add("active");
  document.querySelectorAll("[data-tab]").forEach((button) => {
    button.classList.toggle("selected", button.dataset.tab === tab);
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalizeSearchText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase();
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}

document.querySelector("#refreshButton").addEventListener("click", load);
document.querySelector("#scanDrive").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  button.textContent = "Lendo pastas...";
  try {
    const result = await api("/api/drive/scan", {
      method: "POST",
      body: JSON.stringify({ force: true }),
    });
    await load();
    switchTab("drive");
    if (!result.ok) window.alert(result.scan?.error_message || "A leitura do Drive não foi concluída.");
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Atualizar Drive";
  }
});
document.querySelector("#refreshContent").addEventListener("click", loadContent);
document.querySelector("#refreshNext").addEventListener("click", renderNextContent);
document.querySelector("#contentProductFilter").addEventListener("change", loadContent);
document.querySelector("#contentImportForm [name='product_id']").addEventListener("change", (event) => {
  loadImportCaptions(event.target.value).catch((error) => window.alert(error.message));
});
document.querySelector("#affiliatePeriod").addEventListener("change", () => {
  loadAffiliateSummary().catch((error) => window.alert(error.message));
});
document.querySelector("#affiliateOrderType").addEventListener("change", () => {
  loadAffiliateSummary().catch((error) => window.alert(error.message));
});
document.querySelector("#affiliateSettlement").addEventListener("change", () => {
  loadAffiliateSummary().catch((error) => window.alert(error.message));
});
document.querySelector("#affiliateContentSort").addEventListener("change", renderIntegration);
document.querySelector("#connectTikTok").addEventListener("click", () => {
  window.location.href = "/api/integrations/tiktok-shop/creator/authorize";
});
document.querySelector("#syncTikTok").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/integrations/tiktok-shop/sync", { method: "POST", body: "{}" });
    await load();
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#fullSyncTikTok").addEventListener("click", async (event) => {
  if (!window.confirm("Reprocessar toda a janela histórica configurada?")) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await api("/api/integrations/tiktok-shop/sync", {
      method: "POST",
      body: JSON.stringify({ full: true, confirm_full_sync: true }),
    });
    await load();
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#disconnectTikTok").addEventListener("click", async () => {
  if (!window.confirm("Desconectar a conta Creator deste dashboard?")) return;
  await api("/api/integrations/tiktok-shop/disconnect", { method: "POST", body: "{}" });
  await load();
});
document.querySelector("#affiliateCsvForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type='submit']");
  button.disabled = true;
  const formData = new FormData(form);
  const previewTarget = document.querySelector("#reportPreview");
  previewTarget.innerHTML = `<div class="preview-loading">Analisando estrutura e alterações do relatório...</div>`;
  try {
    const preview = await api("/api/integrations/tiktok-shop/reports/preview", { method: "POST", body: formData });
    form.reset();
    renderReportPreview(preview);
  } catch (error) {
    previewTarget.innerHTML = `<div class="preview-error">${escapeHtml(error.message)}</div>`;
    window.alert(error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelector("#scanAffiliateReports").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/api/integrations/tiktok-shop/reports/import", {
      method: "POST",
      body: "{}",
    });
    window.alert(`${result.imported} relatório(s) importado(s); ${result.skipped} ignorado(s).`);
    await load();
  } catch (error) {
    window.alert(error.message);
  } finally {
    button.disabled = false;
  }
});
document.querySelectorAll("[data-tab]").forEach((button) => {
  button.addEventListener("click", () => switchTab(button.dataset.tab));
});
document.querySelector("#contentImportForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const formData = new FormData(form);
  const file = formData.get("file");
  if (!(file instanceof File) || !file.name) formData.delete("file");
  if (!formData.get("path") && !file?.name) {
    window.alert("Escolha um arquivo ou informe um caminho local.");
    return;
  }
  await api("/api/content/upload", { method: "POST", body: formData });
  form.reset();
  await refreshOperationalViews();
});

switchTab("integration");
load().catch((error) => {
  document.querySelector("#detail").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
});
