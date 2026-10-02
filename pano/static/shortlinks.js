// 🔗 Kısa Link & QR: kısa adresi panoya kopyala, Wi-Fi kartını yazdır.
// JS kapalıyken düğmeler gizli kalır; kısa adres tek dokunuşla seçilir (user-select: all).
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

  const print = document.getElementById("wifi-print");
  if (print) {
    print.classList.remove("hidden");
    print.addEventListener("click", () => window.print());
  }
})();
