// Küçük iyileştirmeler; JavaScript kapalıyken de her şey çalışır.

// data-confirm olan formlarda onay sor
document.addEventListener("submit", (e) => {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});

// data-autosubmit olan select/checkbox değişince formu gönder
document.addEventListener("change", (e) => {
  if (e.target.matches("[data-autosubmit]")) e.target.form.requestSubmit();
});

// data-move="up|down" butonu bulunduğu satırı listede kaydırır (pano düzeni)
document.addEventListener("click", (e) => {
  const btn = e.target.closest("[data-move]");
  if (!btn) return;
  e.preventDefault();
  const row = btn.closest("li");
  if (btn.dataset.move === "up" && row.previousElementSibling) row.parentNode.insertBefore(row, row.previousElementSibling);
  if (btn.dataset.move === "down" && row.nextElementSibling) row.parentNode.insertBefore(row.nextElementSibling, row);
});

// data-show-for="alan=değer1,değer2": formdaki alanın değeri listedeyse gösterir; değilse gizler ve
// içindeki alanları devre dışı bırakır (gönderilmez, zorunlu alan engeli olmaz). JS yoksa hepsi görünür.
function syncShowFor(form) {
  const blocks = form.querySelectorAll("[data-show-for]");
  blocks.forEach((el) => {
    const [name, values] = el.dataset.showFor.split("=");
    const field = form.elements[name];
    if (field) el.classList.toggle("hidden", !values.split(",").includes(field.value));
  });
  blocks.forEach((el) => el.querySelectorAll("input, select, textarea").forEach((input) => {
    input.disabled = input.closest("[data-show-for].hidden") !== null;
  }));
}
document.querySelectorAll("form").forEach((form) => {
  if (form.querySelector("[data-show-for]")) {
    syncShowFor(form);
    form.addEventListener("change", () => syncShowFor(form));
  }
});

// Açılır menüler (kullanıcı menüsü, ☰ Modüller): dışına tıklayınca ya da Esc ile kapanır, biri açılınca öteki kapanır
const DROPDOWNS = "details.usermenu, details.dropdown";
document.addEventListener("click", (e) => {
  document.querySelectorAll(DROPDOWNS).forEach((d) => {
    if (d.open && !d.contains(e.target)) d.removeAttribute("open");
  });
});
document.querySelectorAll(DROPDOWNS).forEach((d) => d.addEventListener("toggle", () => {
  if (!d.open) return;
  document.querySelectorAll(DROPDOWNS).forEach((o) => { if (o !== d) o.removeAttribute("open"); });
  const filter = d.querySelector(".navmenu-filter");
  if (filter && window.matchMedia("(pointer: fine)").matches) filter.focus();
}));
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") document.querySelectorAll(DROPDOWNS).forEach((d) => d.removeAttribute("open"));
});

// Türkçe harf ve büyük/küçük harf duyarsız eşleştirme ("har" -> "Harcamalar", "is" -> "İzleme")
function fold(s) {
  return (s || "").toLocaleLowerCase("tr").normalize("NFD").replace(/[\u0300-\u036f]/g, "")
    .replace(/ı/g, "i").replace(/ş/g, "s").replace(/ğ/g, "g").replace(/ç/g, "c").replace(/ö/g, "o").replace(/ü/g, "u");
}

// Modül listesini süz (☰ Modüller menüsü ve Menü sayfası); Enter ilk eşleşmeyi açar
function bindFilter(input, itemSelector, groupSelector) {
  if (!input) return;
  const scope = input.closest(".navmenu-panel") || document;
  input.addEventListener("input", () => {
    const q = fold(input.value.trim());
    scope.querySelectorAll(itemSelector).forEach((el) => el.classList.toggle("hidden", !!q && !fold(el.dataset.name).includes(q)));
    scope.querySelectorAll(groupSelector).forEach((g) => g.classList.toggle("hidden", !g.querySelector(itemSelector + ":not(.hidden)")));
  });
  input.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !input.value.trim()) return;
    const first = scope.querySelector(itemSelector + ":not(.hidden) a");
    if (first) { e.preventDefault(); location.href = first.href; }
  });
}
bindFilter(document.querySelector(".navmenu-filter"), ".navmenu-item", ".navmenu-group");
bindFilter(document.querySelector(".menu-filter"), ".tile-wrap", ".menu-group");

// Hızlı geçiş: Ctrl+K / ⌘K / "/" — modül adı yaz, Enter; eşleşme yoksa her yerde ara
(() => {
  const dlg = document.getElementById("quickjump");
  if (!dlg || typeof dlg.showModal !== "function") return;
  const input = dlg.querySelector("input");
  const list = dlg.querySelector(".quickjump-list");
  const items = JSON.parse(document.getElementById("quickjump-data").textContent);
  let sel = 0;

  function render() {
    const q = fold(input.value.trim());
    const scored = items.map((it) => {
      const name = fold(it.t);
      const score = !q ? 1 : name.startsWith(q) ? 3 : name.split(/[\s/]+/).some((w) => w.startsWith(q)) ? 2 : name.includes(q) ? 1 : 0;
      return { it, score };
    }).filter((x) => x.score > 0).sort((a, b) => b.score - a.score).slice(0, q ? 8 : 12);
    list.replaceChildren();
    const rows = scored.map(({ it }) => ({ href: it.u, icon: it.i, text: it.t, hint: it.g }));
    if (q) rows.push({ href: dlg.querySelector("form").action + "?q=" + encodeURIComponent(input.value.trim()),
                       icon: "🔍", text: `“${input.value.trim()}” için her yerde ara`, hint: "Arama" });
    sel = Math.min(sel, rows.length - 1);
    rows.forEach((r, i) => {
      const li = document.createElement("li");
      if (i === sel) li.className = "sel";
      const a = document.createElement("a");
      a.href = r.href;
      const icon = document.createElement("span");
      icon.textContent = r.icon;
      const text = document.createElement("span");
      text.textContent = r.text;
      const hint = document.createElement("span");
      hint.className = "g";
      hint.textContent = r.hint;
      a.append(icon, text, hint);
      li.append(a);
      list.append(li);
    });
  }

  function open() {
    if (dlg.open) return;
    input.value = "";
    sel = 0;
    render();
    dlg.showModal();
    input.focus();
  }

  document.querySelectorAll("[data-quickjump]").forEach((b) => b.addEventListener("click", (e) => { e.preventDefault(); open(); }));
  document.addEventListener("keydown", (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
    if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) { e.preventDefault(); open(); }
    else if (e.key === "/" && !typing && !dlg.open) { e.preventDefault(); open(); }
  });
  input.addEventListener("input", () => { sel = 0; render(); });
  input.addEventListener("keydown", (e) => {
    const n = list.children.length;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      sel = n ? (sel + (e.key === "ArrowDown" ? 1 : n - 1)) % n : 0;
      render();
    } else if (e.key === "Enter") {
      const a = list.children[sel] && list.children[sel].querySelector("a");
      if (a) { e.preventDefault(); location.href = a.href; }
    }
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });  // dışına tıklayınca kapat
})();

// Uygulama gibi yüklenebilmesi için service worker
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
