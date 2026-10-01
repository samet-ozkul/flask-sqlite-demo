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

// Kullanıcı menüsü dışına tıklanınca kapat
document.addEventListener("click", (e) => {
  document.querySelectorAll("details.usermenu[open]").forEach((d) => {
    if (!d.contains(e.target)) d.removeAttribute("open");
  });
});

// Uygulama gibi yüklenebilmesi için service worker
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}
