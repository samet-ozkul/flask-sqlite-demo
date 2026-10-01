// 🌐 Profil sayfası: link satırı ekle / sil (sıralama app.js'teki data-move ile).
// JS kapalıyken de çalışır: her kayıtta bir boş satır gelir, boşaltılan satır silinir.
(() => {
  const list = document.getElementById("pf-links");
  if (!list) return;
  const tpl = document.getElementById("pf-link-row");
  const add = document.getElementById("pf-add");
  const max = Number(list.dataset.max);
  const sync = () => add.classList.toggle("hidden", list.children.length >= max);

  add.addEventListener("click", () => {
    list.appendChild(tpl.content.cloneNode(true));
    list.lastElementChild.querySelector("input").focus();
    sync();
  });
  list.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-remove]");
    if (!btn) return;
    btn.closest("li").remove();
    sync();
  });
  sync();
})();
