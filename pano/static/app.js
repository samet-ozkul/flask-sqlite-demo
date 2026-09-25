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
