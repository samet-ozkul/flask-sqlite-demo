// 🗂️ Kanban: kartları sürükle-bırak ile taşır (fare: HTML5 sürükle-bırak, dokunmatik: basılı tutup sürükle)
// ve ⋯ menüsündeki ← → ↑ ↓ butonlarını sayfa yenilemeden uygular. JS yoksa aynı butonlar formla çalışır.
// Sunucu: POST /kanban/kart/<id>/tasi (column_id, position) -> JSON; sütunlar 0..n yeniden numaralanır.
(() => {
  const board = document.getElementById("kboard");
  if (!board) return;

  const LONG_PRESS_MS = 350;  // dokunmatikte sürükleme için basılı tutma süresi
  const SLOP = 8;             // bundan fazla kayarsa sürükleme değil, kaydırma sayılır (px)
  const EDGE = 48;            // pano kenarına bu kadar yaklaşınca kendiliğinden yatay kaydır (px)
  const columns = () => [...board.querySelectorAll(".kcol[data-col]")];
  const listOf = (col) => col.querySelector(".kcards");
  const cardsIn = (col, except) => [...listOf(col).children].filter((el) => el.classList.contains("kcard") && el !== except);
  const where = (card) => {
    const col = card.closest(".kcol");
    return { col, index: cardsIn(col).indexOf(card) };
  };

  // ---------- Sunucuya bildir ----------
  async function send(card, col, position) {
    const fd = new FormData();
    fd.append("_csrf", board.dataset.csrf);
    fd.append("column_id", col.dataset.col);
    fd.append("position", String(position));
    try {
      const url = board.dataset.moveUrl.replace("/0/", `/${card.dataset.id}/`);
      const r = await fetch(url, { method: "POST", body: fd, credentials: "same-origin", headers: { Accept: "application/json" } });
      let body = null;
      try { body = await r.json(); } catch (e) { /* HTML hata sayfası ya da oturum kapanmış */ }
      if (!r.ok || !body || !body.ok) throw new Error((body && body.error) || "Kart taşınamadı.");
    } catch (err) {
      alert(`${err.message} Sayfa yenileniyor.`);
      location.reload();
    }
  }

  // Sayaçları ve menü oklarını DOM'daki sıraya göre günceller
  function refresh() {
    const cols = columns();
    cols.forEach((col, ci) => {
      const cards = cardsIn(col);
      const count = col.querySelector(".kcount");
      if (count) count.textContent = cards.length;
      cards.forEach((card, i) => {
        const allow = { left: ci > 0, right: ci < cols.length - 1, up: i > 0, down: i < cards.length - 1 };
        card.querySelectorAll(".kmenu button[name=dir]").forEach((b) => { b.disabled = !allow[b.value]; });
      });
    });
  }

  // Kartı sütunun `index`. sırasına koyar (kart hariç sayılır)
  function place(card, col, index) {
    listOf(col).insertBefore(card, cardsIn(col, card)[index] || null);
  }

  // Sürükleme/menü bitti: yeri değiştiyse sunucuya bildir
  function commit(card, from) {
    const to = where(card);
    refresh();
    if (to.col !== from.col || to.index !== from.index) send(card, to.col, to.index);
  }

  // Kartı imlecin/parmağın altındaki sıraya taşır (gerçek kart yer tutucu olarak kullanılır)
  function follow(card, col, y) {
    const others = cardsIn(col, card);
    let index = others.findIndex((el) => {
      const r = el.getBoundingClientRect();
      return y < r.top + r.height / 2;
    });
    if (index < 0) index = others.length;
    if (card.closest(".kcol") === col && cardsIn(col).indexOf(card) === index) return;
    place(card, col, index);
  }

  // ---------- Menü: ← → ↑ ↓ ----------
  board.addEventListener("click", (e) => {
    const btn = e.target.closest(".kmenu button[name=dir]");
    if (!btn) return;
    e.preventDefault();
    const card = btn.closest(".kcard");
    const from = where(card);
    const cols = columns();
    let col = from.col;
    let index = from.index;
    if (btn.value === "up") index -= 1;
    else if (btn.value === "down") index += 1;
    else {
      col = cols[cols.indexOf(from.col) + (btn.value === "left" ? -1 : 1)];
      if (!col) return;
      index = cardsIn(col, card).length;  // yeni sütunun sonuna
    }
    if (index < 0 || index > cardsIn(col, card).length) return;
    btn.closest("details").open = false;
    place(card, col, index);
    commit(card, from);
    card.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
  });

  // ---------- Fare: HTML5 sürükle-bırak ----------
  let drag = null;  // {card, from, dropped}

  board.addEventListener("dragstart", (e) => {
    const card = e.target.closest && e.target.closest(".kcard");
    if (!card || touch) return;
    drag = { card, from: where(card), dropped: false };
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", card.dataset.id);  // Firefox veri olmadan sürüklemez
    board.classList.add("dragging");
    requestAnimationFrame(() => card.classList.add("ghost"));  // sürükleme resmi soluk olmasın
  });

  board.addEventListener("dragover", (e) => {
    if (!drag) return;
    const col = e.target.closest(".kcol[data-col]");
    if (!col) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    follow(drag.card, col, e.clientY);
  });

  board.addEventListener("drop", (e) => {
    if (!drag) return;
    e.preventDefault();
    drag.dropped = true;
  });

  board.addEventListener("dragend", () => {
    if (!drag) return;
    const { card, from, dropped } = drag;
    drag = null;
    card.classList.remove("ghost");
    board.classList.remove("dragging");
    if (dropped) commit(card, from);
    else place(card, from.col, from.index);  // pano dışına bırakıldı ya da Esc: eski yerine
  });

  // ---------- Dokunmatik: basılı tut, sürükle (Pointer Events) ----------
  let press = null;  // {card, id, x, y, timer} basılı tutma bekleniyor
  let touch = null;  // {card, from, float, ox, oy, x, y, raf} sürükleniyor
  let suppressClick = false;

  function cancelPress() {
    if (press) clearTimeout(press.timer);
    press = null;
  }

  board.addEventListener("pointerdown", (e) => {
    const card = e.target.closest(".kcard");
    if (!card) return;
    const inMenu = e.target.closest(".kmenu") !== null;
    // Sadece fareyle yerleşik sürükleme; dokunmatikte tarayıcının kendi sürüklemesi/bağlam menüsü karışmasın
    card.draggable = e.pointerType === "mouse" && !inMenu;
    if (e.pointerType === "mouse" || inMenu || touch || !e.isPrimary) return;
    cancelPress();
    press = { card, id: e.pointerId, x: e.clientX, y: e.clientY, timer: setTimeout(startTouch, LONG_PRESS_MS) };
  });

  function startTouch() {
    const { card, x, y } = press;
    press = null;
    const r = card.getBoundingClientRect();
    const float = card.cloneNode(true);
    float.removeAttribute("id");
    float.classList.add("floating");
    Object.assign(float.style, { width: `${r.width}px`, left: `${r.left}px`, top: `${r.top}px` });
    document.body.appendChild(float);
    card.classList.add("ghost");
    board.classList.add("dragging");
    touch = { card, from: where(card), float, ox: x - r.left, oy: y - r.top, x, y, raf: 0 };
    if (navigator.vibrate) navigator.vibrate(15);
    tick();
  }

  // Her karede: kopyayı parmağa taşı, kenardaysa kaydır, altındaki sütuna yerleştir
  function tick() {
    if (!touch) return;
    const { card, float, x, y } = touch;
    float.style.left = `${x - touch.ox}px`;
    float.style.top = `${y - touch.oy}px`;
    const b = board.getBoundingClientRect();
    if (x < b.left + EDGE) board.scrollLeft -= 12;
    else if (x > b.right - EDGE) board.scrollLeft += 12;
    if (y < 110) window.scrollBy(0, -12);                       // üst çubuğun altı
    else if (y > window.innerHeight - 110) window.scrollBy(0, 12); // alt menünün üstü
    const el = document.elementFromPoint(x, y);
    const col = el && el.closest(".kcol[data-col]");
    if (col && board.contains(col)) follow(card, col, y);
    touch.raf = requestAnimationFrame(tick);
  }

  function endTouch(keep) {
    const { card, from, float, raf } = touch;
    touch = null;
    cancelAnimationFrame(raf);
    float.remove();
    card.classList.remove("ghost");
    board.classList.remove("dragging");
    if (keep) commit(card, from);
    else place(card, from.col, from.index);
    // Parmak kalkınca karta dokunma (bağlantıyı açma) sayılmasın
    suppressClick = true;
    setTimeout(() => { suppressClick = false; }, 400);
  }

  // Kart DOM'da yer değiştirince olaylar karttan kopabilir; bu yüzden belge düzeyinde dinlenir
  document.addEventListener("pointermove", (e) => {
    if (press && e.pointerId === press.id && Math.hypot(e.clientX - press.x, e.clientY - press.y) > SLOP) cancelPress();
    if (touch) {
      touch.x = e.clientX;
      touch.y = e.clientY;
    }
  });
  document.addEventListener("pointerup", () => {
    cancelPress();
    if (touch) endTouch(true);
  });
  document.addEventListener("pointercancel", () => {
    cancelPress();
    if (touch) endTouch(false);  // tarayıcı hareketi devraldı: kart eski yerine
  });
  // Sürüklerken sayfa kaymasın (passive: false olmadan preventDefault çalışmaz)
  document.addEventListener("touchmove", (e) => { if (touch) e.preventDefault(); }, { passive: false });
  board.addEventListener("contextmenu", (e) => { if (press || touch) e.preventDefault(); });
  board.addEventListener("click", (e) => {
    if (suppressClick) {
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);

  refresh();
})();
