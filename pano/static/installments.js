// 💳 Taksitler: alışveriş formunda, kaydetmeden önce taksit planı ve ilk ekstrenin kendiliğinden hesaplanması.
// Sunucu: POST /taksitler/plan (formun kendisi) -> {ok, first_auto, rows: [{no, period, due, amount}], total, error}.
// JS yoksa form yine çalışır: ilk ekstre boş ya da önerilenle aynıysa sunucuda hesaplanır, plan kayıttan sonra görünür.
document.querySelectorAll("form[data-plan-url]").forEach((form) => {
  const box = form.querySelector("#plan-preview");
  const first = form.elements.first_statement;
  const auto = form.elements.first_auto;
  if (!box || !first || !auto) return;
  let timer = null;
  let seq = 0;

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }

  function render(body) {
    box.replaceChildren();
    if (!body.ok) {
      // Tutar henüz yazılmadıysa hata gösterme (sayfa ilk açıldığında)
      if (body.error && form.elements.total.value.trim()) box.append(el("p", body.error, "muted small"));
      return;
    }
    box.append(el("p", `Taksit planı (kaydetmeden önce) · toplam ${body.total}`, "muted small"));
    const wrap = el("div", undefined, "table-wrap");
    const table = el("table", undefined, "ins-plan");
    const head = el("tr");
    ["Taksit", "Ekstre", "Tutar"].forEach((h, i) => head.append(el("th", h, i === 2 ? "num" : "")));
    table.append(el("thead"));
    table.tHead.append(head);
    const tbody = el("tbody");
    body.rows.forEach((r) => {
      const tr = el("tr");
      tr.append(el("td", r.no));
      const td = el("td", r.period);
      td.append(el("small", `son ödeme ${r.due}`));
      tr.append(td, el("td", r.amount, "num"));
      tbody.append(tr);
    });
    table.append(tbody);
    wrap.append(table);
    box.append(wrap);
  }

  async function refresh() {
    const mine = ++seq;
    let body = null;
    try {
      const r = await fetch(form.dataset.planUrl, { method: "POST", body: new FormData(form), credentials: "same-origin",
                                                   headers: { Accept: "application/json" } });
      body = await r.json();
    } catch (e) {
      return;  // oturum kapanmış ya da ağ yok: önizleme olmadan da kaydedilebilir
    }
    if (mine !== seq || !body) return;
    // İlk ekstre elle değiştirilmediyse (boş ya da önerilenle aynı) yeni öneriyi yaz
    if (body.first_auto) {
      if (!first.value || first.value === auto.value) first.value = body.first_auto;
      auto.value = body.first_auto;
    }
    render(body);
  }

  function later() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 350);
  }

  form.addEventListener("input", later);
  form.addEventListener("change", later);
  if (form.closest("details") === null || form.closest("details").open) refresh();
  const details = form.closest("details");
  if (details) details.addEventListener("toggle", () => { if (details.open) refresh(); });
});
