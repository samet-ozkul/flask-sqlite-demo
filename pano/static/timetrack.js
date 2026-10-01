// ⏱️ Zaman takibi: çalışan sayacın canlı süresi.
// Başlangıç zamanı sunucudan gelir (data-started, ms). Tarayıcı saati ile sunucu saati arasındaki fark sayfa
// açılırken bir kez ölçülür (data-now); süre her saniye baştan hesaplandığı için sekme uyusa da doğru kalır.
(() => {
  const clock = document.querySelector(".tt-clock[data-started]");
  if (!clock) return;
  const started = Number(clock.dataset.started);
  const offset = Number(clock.dataset.now) - Date.now();
  const baseTitle = document.title;
  const pad = (n) => String(n).padStart(2, "0");

  function tick() {
    const s = Math.max(0, Math.floor((Date.now() + offset - started) / 1000));
    const text = `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
    clock.textContent = text;
    document.title = `⏱️ ${text} · ${baseTitle}`;
  }

  tick();
  setInterval(tick, 1000);
  // Arka planda zamanlayıcılar yavaşlar: sekmeye dönünce hemen güncelle
  document.addEventListener("visibilitychange", () => { if (!document.hidden) tick(); });
  // Sekme önbellekten geri gelirse (sayaç bu arada Telegram'dan durdurulmuş olabilir) sayfayı tazele
  window.addEventListener("pageshow", (e) => { if (e.persisted) location.reload(); });
})();
