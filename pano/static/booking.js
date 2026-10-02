// 📅 Randevu Sayfası: sayfa adresini panoya kopyala.
// JS kapalıyken düğme gizli kalır; adres tek dokunuşla seçilir (user-select: all).
(() => {
  document.querySelectorAll("[data-copy]").forEach((btn) => {
    btn.dataset.label = btn.textContent;
    btn.classList.remove("hidden");
  });
  document.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const el = document.getElementById(btn.dataset.copy);
    try {
      await navigator.clipboard.writeText(el.textContent.trim());
      btn.textContent = "✅ Kopyalandı";
    } catch (err) {
      // İzin yoksa (http, eski tarayıcı) metin seçilir; kullanıcı kendisi kopyalar
      const range = document.createRange();
      range.selectNodeContents(el);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      btn.textContent = "Seçildi, kopyala";
    }
    setTimeout(() => { btn.textContent = btn.dataset.label; }, 2000);
  });
})();
