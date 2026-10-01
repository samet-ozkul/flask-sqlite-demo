// 📤 Aktar: listeyi birkaç saniyede bir yeniler, metni panoya kopyalar, dosya seçimini/sürüklemeyi gösterir.
(() => {
  const list = document.getElementById("t-list");
  if (!list) return;

  // ---------- Kendiliğinden yenileme (sadece sekme görünürken) ----------
  let last = null;
  async function refresh() {
    if (document.hidden) return;
    try {
      const r = await fetch(list.dataset.url, { credentials: "same-origin", headers: { Accept: "text/html" } });
      if (!r.ok || r.redirected) return;
      const html = await r.text();
      if (last !== null && html === last) return;
      last = html;
      if (!list.contains(document.activeElement)) list.innerHTML = html;
    } catch (e) {
      /* çevrimdışı: bir sonraki turda yeniden dener */
    }
  }
  setInterval(refresh, 8000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

  // ---------- Kopyala ----------
  list.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-copy]");
    if (!btn) return;
    const el = document.getElementById(btn.dataset.copy);
    try {
      await navigator.clipboard.writeText(el.textContent);
      btn.textContent = "✅ Kopyalandı";
    } catch (err) {
      const range = document.createRange();
      range.selectNodeContents(el);
      getSelection().removeAllRanges();
      getSelection().addRange(range);
      btn.textContent = "Seçildi, kopyala";
    }
    setTimeout(() => { btn.textContent = "📋 Kopyala"; }, 2000);
  });

  // ---------- Dosya seçimi: boyut bilgisi, sınır uyarısı, sürükle-bırak ----------
  const form = document.getElementById("t-form");
  const input = form.querySelector("input[type=file]");
  const info = document.getElementById("t-files-info");
  const drop = document.getElementById("t-drop");
  const maxBytes = Number(input.dataset.maxMb) * 1024 * 1024;
  const size = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

  function describe() {
    const files = [...input.files];
    if (!files.length) { info.textContent = ""; return; }
    const big = files.filter((f) => f.size > maxBytes);
    const total = files.reduce((s, f) => s + f.size, 0);
    info.textContent = `${files.length} dosya, ${size(total)}` +
      (big.length ? ` — ⚠️ sınırı aşan: ${big.map((f) => f.name).join(", ")} (gönderilmeyecek)` : "");
    info.style.color = big.length ? "var(--danger)" : "";
  }
  input.addEventListener("change", describe);
  for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); });
  for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, () => drop.classList.remove("over"));
  drop.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer && e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      describe();
    }
  });

  form.addEventListener("submit", () => {
    const btn = document.getElementById("t-send");
    btn.disabled = true;
    btn.textContent = input.files.length ? "Yükleniyor…" : "Gönderiliyor…";
  });
})();
