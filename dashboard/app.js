let state = { products: [], tasks: [], selectedId: null };

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const number = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 0 });
const decimal = new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 2 });

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) throw new Error(`Erro ${response.status}`);
  return response.json();
}

async function load() {
  const data = await api("/api/summary");
  state.products = data.products;
  state.tasks = data.tasks;
  if (!state.selectedId && state.products.length) state.selectedId = state.products[0].id;
  renderSummary();
  renderProducts();
  if (state.selectedId) await renderDetail(state.selectedId);
}

function renderSummary() {
  const revenue = state.products.reduce((sum, product) => sum + Number(product.revenue_total || 0), 0);
  const sales = state.products.reduce((sum, product) => sum + Number(product.sales_total || 0), 0);
  const validated = state.products.filter((product) => product.status === "validated").length;
  const pendingTasks = state.tasks.filter((task) => !["postado", "programado"].includes(task.status)).length;
  const avgRoas = state.products.length
    ? state.products.reduce((sum, product) => sum + Number(product.roas || 0), 0) / state.products.length
    : 0;

  document.querySelector("#summaryMetrics").innerHTML = [
    ["Receita monitorada", money.format(revenue)],
    ["Vendas", number.format(sales)],
    ["Produtos validados", number.format(validated)],
    ["Versões pendentes", number.format(pendingTasks)],
    ["ROAS médio bruto", decimal.format(avgRoas)],
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

async function renderDetail(productId) {
  const data = await api(`/api/product?id=${productId}`);
  const product = data.product;
  if (!product) return;
  const latest = data.snapshots[0] || {};

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
      ${metric("Vídeos", number.format(Number(latest.video_count || 0)))}
      ${metric("Criadores", number.format(Number(latest.creator_count || 0)))}
      ${metric("GPM vídeos", money.format(Number(latest.gpm_video_export || 0)))}
      ${metric("CPA ads", money.format(Number(latest.cpa_ads || 0)))}
      ${metric("ROAS bruto", decimal.format(Number(latest.roas || 0)))}
      ${metric("Receita live", money.format(Number(latest.revenue_live || 0)))}
    </div>

    <div class="panel">
      <h3>Produção</h3>
      <div class="task-grid">${renderTasks(data.tasks)}</div>
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
      <h3>Top vídeos</h3>
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

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function escapeAttr(value) {
  return escapeHtml(value).replaceAll("\n", " ");
}

document.querySelector("#refreshButton").addEventListener("click", load);
load().catch((error) => {
  document.querySelector("#detail").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
});
