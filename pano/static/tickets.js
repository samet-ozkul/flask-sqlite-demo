// 🎟️ Bilet ekranı: rezervasyon kodunu kopyala, sayfa açıkken ekranın kararmasını engelle.
// JS kapalıyken Kopyala düğmesi gizli kalır; kod tek dokunuşla seçilir (user-select: all).
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

  // Ekran kilidi (destekleyen tarayıcılarda): kapıda beklerken ekran kararmasın. Sekmeden çıkınca tarayıcı
  // kilidi bırakır; sayfaya dönünce yeniden istenir.
  const note = document.querySelector("[data-wakelock]");
  if (!note || !("wakeLock" in navigator)) return;
  const keepAwake = async () => {
    try {
      const lock = await navigator.wakeLock.request("screen");
      note.classList.remove("hidden");
      lock.addEventListener("release", () => note.classList.add("hidden"));
    } catch (err) {
      // izin verilmedi ya da pil tasarrufu: sessizce geç
    }
  };
  keepAwake();
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") keepAwake();
  });
})();
