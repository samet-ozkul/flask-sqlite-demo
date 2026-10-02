// 📄 Belge tarayıcı: çekilen/seçilen sayfaları listede tutar (sıra, döndürme, kaldırma) ve hepsini
// tek "files" alanında sırayla gönderir. JS yoksa ya da tarayıcı DataTransfer bilmiyorsa iki alan doğrudan gönderilir.
(() => {
  const form = document.getElementById("s-form");
  if (!form) return;
  try {
    new DataTransfer();
  } catch (e) {
    return;
  }
  const $ = (id) => document.getElementById(id);
  const camera = $("s-camera");
  const gallery = $("s-gallery");
  const combined = $("s-files");
  const list = $("s-pages");
  const count = $("s-count");
  const status = $("s-status");
  const submit = $("s-submit");
  const clearBtn = $("s-clear");
  const maxPages = Number(form.dataset.maxPages);
  const maxBytes = Number(form.dataset.maxMb) * 1024 * 1024;
  const IMAGE_NAME = /\.(jpe?g|png|webp|gif|bmp|tiff?|heic|heif|avif)$/i;
  const pages = []; // {file | inbox (Telegram kutusu id), size, rot, url, el, img, no}
  const size = (n) => (n >= 1048576 ? (n / 1048576).toFixed(1).replace(".", ",") + " MB" : Math.max(1, Math.round(n / 1024)) + " KB");

  camera.removeAttribute("name");
  gallery.removeAttribute("name");
  combined.name = "files";
  form.classList.add("s-ready");
  document.documentElement.classList.add("s-js");  // kutudaki sayfalar listede; karttaki küçük resimler gizlenir

  // ---------- Seçimi hatırla (görünüm, hedef) ----------
  const KEY = "tara-secim-v2";  // v2: varsayılan görünüm orijinal renk oldu; eski kayıtlı "belge" seçimi geçersiz
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    for (const name of ["mode", "target"]) {
      form.querySelectorAll(`input[name=${name}]`).forEach((r) => { if (r.value === saved[name]) r.checked = true; });
    }
  } catch (e) {
    /* depolama kapalı: varsayılanlar kalır */
  }
  form.addEventListener("change", (e) => {
    if (!["mode", "target"].includes(e.target.name)) return;
    try {
      localStorage.setItem(KEY, JSON.stringify({ mode: form.elements.mode.value, target: form.elements.target.value }));
    } catch (err) {
      /* önemsiz */
    }
  });

  // ---------- Sayfa listesi ----------
  function button(text, title, act, cls = "btn sm") {
    const b = document.createElement("button");
    b.type = "button";
    b.className = cls;
    b.textContent = text;
    b.title = title;
    b.setAttribute("aria-label", title);
    b.dataset.act = act;
    return b;
  }

  function makeItem(p) {
    const li = document.createElement("li");
    li.className = "s-page";
    const box = document.createElement("div");
    box.className = "s-thumb";
    const img = document.createElement("img");
    img.src = p.url;
    img.alt = "";
    img.decoding = "async";
    const no = document.createElement("span");
    no.className = "s-no";
    box.append(img, no, button("✕", "Sayfayı kaldır", "del", "btn sm danger s-del"));
    if (p.inbox) {
      const src = document.createElement("span");
      src.className = "s-src";
      src.textContent = "📲";
      src.title = "Telegram'dan";
      box.append(src);
    }
    const bar = document.createElement("div");
    bar.className = "s-bar";
    bar.append(button("←", "Öne al", "left"), button("⟳", "90° döndür", "rot"), button("→", "Geriye al", "right"));
    li.append(box, bar);
    Object.assign(p, { el: li, img, no });
  }

  function say(text, danger = false) {
    status.textContent = text;
    status.style.color = danger ? "var(--danger)" : "";
  }

  function render() {
    list.replaceChildren(...pages.map((p) => p.el));
    pages.forEach((p, i) => {
      p.no.textContent = String(i + 1);
      p.el.querySelector("[data-act=left]").disabled = i === 0;
      p.el.querySelector("[data-act=right]").disabled = i === pages.length - 1;
    });
    const total = pages.reduce((s, p) => s + p.size, 0);
    const full = pages.length >= maxPages;
    const tooBig = total > maxBytes;
    count.textContent = pages.length
      ? `${pages.length} sayfa · ${size(total)}` + (full ? ` · en fazla ${maxPages} sayfa` : "") +
        (tooBig ? ` · ⚠️ toplam ${form.dataset.maxMb} MB'ı aşıyor, birkaç sayfayı kaldır` : "")
      : `Henüz sayfa yok. Her “Fotoğraf çek” bir sayfa ekler; en fazla ${maxPages} sayfa.`;
    count.style.color = tooBig ? "var(--danger)" : "";
    camera.disabled = gallery.disabled = full;
    clearBtn.classList.toggle("hidden", !pages.length);
  }

  function add(files) {
    let skipped = 0;
    let over = 0;
    for (const file of files) {
      if (!(file.type.startsWith("image/") || IMAGE_NAME.test(file.name))) { skipped++; continue; }
      if (pages.length >= maxPages) { over++; continue; }
      const p = { file, size: file.size, rot: 0, url: URL.createObjectURL(file) };
      makeItem(p);
      pages.push(p);
    }
    render();
    const notes = [];
    if (skipped) notes.push(`${skipped} dosya resim olmadığı için eklenmedi.`);
    if (over) notes.push(`En fazla ${maxPages} sayfa; ${over} fotoğraf eklenmedi.`);
    say(notes.join(" "), notes.length > 0);
  }

  for (const input of [camera, gallery]) {
    input.addEventListener("change", () => {
      add([...input.files]);
      input.value = ""; // aynı fotoğraf yeniden seçilebilsin, kamera bir sonraki sayfa için hazır olsun
    });
  }

  list.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    if (!btn) return;
    const i = pages.findIndex((p) => p.el.contains(btn));
    if (i < 0) return;
    const p = pages[i];
    if (btn.dataset.act === "rot") {
      p.rot = (p.rot + 90) % 360;
      p.img.style.transform = p.rot ? `rotate(${p.rot}deg)` : "";
      return;
    }
    if (btn.dataset.act === "del") {
      if (p.file) URL.revokeObjectURL(p.url);  // Telegram sayfası sadece bu PDF'ten çıkar, kutuda kalır
      pages.splice(i, 1);
    } else {
      const j = btn.dataset.act === "left" ? i - 1 : i + 1;
      if (j < 0 || j >= pages.length) return;
      [pages[i], pages[j]] = [pages[j], pages[i]];
    }
    render();
    say("");
  });

  clearBtn.addEventListener("click", () => {
    pages.splice(0).forEach((p) => { if (p.file) URL.revokeObjectURL(p.url); });
    render();
    say("");
  });

  // ---------- Gönderme ----------
  let timer = null;
  function busy(on) {
    submit.disabled = on;
    submit.textContent = on ? "⏳ PDF hazırlanıyor…" : "📄 PDF yap";
    if (!on && timer) { clearInterval(timer); timer = null; }
  }

  form.addEventListener("submit", (e) => {
    const total = pages.filter((p) => p.file).reduce((s, p) => s + p.size, 0);  // yüklenecek kısım
    if (!pages.length || total > maxBytes) {
      e.preventDefault();
      say(pages.length ? `Toplam boyut ${form.dataset.maxMb} MB'ı aşıyor; birkaç sayfayı kaldır.` : "Önce en az bir sayfa ekle.", true);
      return;
    }
    try {
      const dt = new DataTransfer();
      pages.forEach((p) => { if (p.file) dt.items.add(p.file); });
      combined.files = dt.files;
    } catch (err) {
      e.preventDefault();
      say("Bu tarayıcı sayfaları birleştiremiyor; tarayıcını güncelle.", true);
      return;
    }
    $("s-rotations").value = pages.map((p) => p.rot).join(",");
    // Sıra: "i12" Telegram kutusundaki sayfa, "f" yüklenen bir sonraki dosya
    $("s-order").value = pages.map((p) => (p.inbox ? "i" + p.inbox : "f")).join(",");
    busy(true);
    say(`${pages.length} sayfa yükleniyor ve işleniyor…`);
    if (form.elements.target.value === "download") {
      // İndirmede sayfa yerinde kalır: sunucunun koyduğu çerez görülünce iş bitmiş demektir
      const token = Math.random().toString(36).slice(2, 12).padEnd(6, "0");
      $("s-dl").value = token;
      const started = Date.now();
      timer = setInterval(() => {
        if (document.cookie.split("; ").includes("tara_dl=" + token)) {
          busy(false);
          say("✅ PDF hazır; indirilenler klasörüne bak.");
        } else if (Date.now() - started > 180000) {
          busy(false);
          say("");
        }
      }, 700);
    } else {
      $("s-dl").value = "";
    }
  });

  // Geri tuşuyla dönülünce (sayfa önbellekten gelir) buton takılı kalmasın
  window.addEventListener("pageshow", (e) => { if (e.persisted) busy(false); });

  // Telegram'dan gelen sayfalar listenin başında
  try {
    for (const it of JSON.parse($("s-inbox").textContent || "[]")) {
      const p = { inbox: it.id, size: it.size, rot: 0, url: it.url };
      makeItem(p);
      pages.push(p);
    }
  } catch (e) {
    /* liste yoksa yalnızca yüklenen sayfalar */
  }

  render();
  say("");
})();
