// 🔐 Şifreli kasa: bütün şifreleme tarayıcıda (Web Crypto). Kasa parolası ve açık metin sunucuya gitmez.
//
// Kasa parolası --PBKDF2-SHA256 (600.000 tur, rastgele tuz)--> sarma anahtarı
// sarma anahtarı --AES-256-GCM--> rastgele veri anahtarı (sunucuda sarılı hali durur)
// veri anahtarı  --AES-256-GCM--> her kayıt: {"t": başlık, "b": içerik}  (12 bayt IV + şifreli metin, base64)
(() => {
  const root = document.getElementById("vault");
  if (!root) return;

  const ITERATIONS = 600000;
  const IDLE_MS = 5 * 60 * 1000;
  const te = new TextEncoder();
  const td = new TextDecoder();
  const AAD_KEY = te.encode("pano-kasa-anahtar-v1");
  const AAD_ITEM = te.encode("pano-kasa-kayit-v1");
  const $ = (id) => document.getElementById(id);

  let meta = JSON.parse($("vault-meta").textContent);
  let items = JSON.parse($("vault-items").textContent); // [{id, data, updated_at}]
  let dataKey = null;          // CryptoKey (dışa aktarılamaz); kilitliyken null
  const plain = new Map();     // id -> {t, b}
  let idleTimer = null;

  // ---------- Yardımcılar ----------
  function b64(bytes) {
    let s = "";
    for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }
  const unb64 = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));

  async function deriveWrapKey(passphrase, salt, iterations) {
    const base = await crypto.subtle.importKey("raw", te.encode(passphrase), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey({ name: "PBKDF2", salt, iterations, hash: "SHA-256" }, base,
      { name: "AES-GCM", length: 256 }, false, ["encrypt", "decrypt"]);
  }

  async function seal(key, bytes, aad) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv, additionalData: aad }, key, bytes));
    const out = new Uint8Array(iv.length + ct.length);
    out.set(iv);
    out.set(ct, iv.length);
    return b64(out);
  }

  async function unseal(key, text, aad) {
    const all = unb64(text);
    return new Uint8Array(await crypto.subtle.decrypt({ name: "AES-GCM", iv: all.subarray(0, 12), additionalData: aad },
      key, all.subarray(12)));
  }

  const importDataKey = (raw) => crypto.subtle.importKey("raw", raw, "AES-GCM", false, ["encrypt", "decrypt"]);

  async function unwrapRaw(passphrase) {
    const wrapKey = await deriveWrapKey(passphrase, unb64(meta.salt), meta.iterations);
    try {
      return await unseal(wrapKey, meta.wrapped_key, AAD_KEY);
    } catch (e) {
      throw new Error("Kasa parolası hatalı.");
    }
  }

  async function wrapFields(raw, passphrase, hint) {
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const wrapKey = await deriveWrapKey(passphrase, salt, ITERATIONS);
    return { salt: b64(salt), iterations: String(ITERATIONS), wrapped_key: await seal(wrapKey, raw, AAD_KEY), hint };
  }

  async function post(url, fields) {
    const fd = new FormData();
    fd.append("_csrf", root.dataset.csrf);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    const r = await fetch(url, { method: "POST", body: fd, credentials: "same-origin", headers: { Accept: "application/json" } });
    if (r.redirected) throw new Error("Oturum kapanmış; sayfayı yenileyip tekrar giriş yap.");
    let body = null;
    try { body = await r.json(); } catch (e) { /* HTML hata sayfası */ }
    if (!r.ok || body === null) throw new Error((body && body.error) || `İşlem başarısız (HTTP ${r.status}). Sayfayı yenileyip tekrar dene.`);
    return body;
  }

  function status(text, isError) {
    const el = $("v-status");
    el.textContent = text || "";
    el.style.color = isError ? "var(--danger)" : "";
  }

  function show(state) {
    for (const id of ["v-setup", "v-locked", "v-open"]) $(id).classList.toggle("hidden", id !== state);
    const lockBtn = $("v-lock-btn");
    if (lockBtn) lockBtn.classList.toggle("hidden", state !== "v-open");
  }

  async function busy(form, fn) {
    const btns = form.querySelectorAll("button");
    btns.forEach((b) => (b.disabled = true));
    try {
      await fn();
    } catch (e) {
      status(e.message || String(e), true);
    } finally {
      btns.forEach((b) => (b.disabled = false));
    }
  }

  // ---------- Kilit ----------
  function lock(reason) {
    dataKey = null;
    plain.clear();
    $("v-list").replaceChildren();
    $("v-edit-form").reset();
    $("v-edit-form").classList.add("hidden");
    clearTimeout(idleTimer);
    show("v-locked");
    status(reason || "");
  }

  function touch() {
    if (!dataKey) return;
    clearTimeout(idleTimer);
    idleTimer = setTimeout(() => lock("5 dakika işlem yapılmadığı için kasa kilitlendi."), IDLE_MS);
  }

  async function openWith(raw) {
    dataKey = await importDataKey(raw);
    raw.fill(0);
    plain.clear();
    let broken = 0;
    for (const it of items) {
      try {
        plain.set(it.id, JSON.parse(td.decode(await unseal(dataKey, it.data, AAD_ITEM))));
      } catch (e) {
        broken += 1;
      }
    }
    show("v-open");
    render();
    touch();
    status(broken ? `${broken} kayıt açılamadı (bozuk ya da başka bir anahtarla şifrelenmiş).` : "", broken > 0);
  }

  // ---------- Liste ----------
  function render() {
    const list = $("v-list");
    const q = $("v-filter").value.trim().toLocaleLowerCase("tr");
    list.replaceChildren();
    const rows = items.filter((it) => plain.has(it.id))
      .map((it) => ({ it, p: plain.get(it.id) }))
      .filter(({ p }) => !q || (p.t + "\n" + p.b).toLocaleLowerCase("tr").includes(q))
      .sort((a, b) => a.p.t.localeCompare(b.p.t, "tr"));
    for (const { it, p } of rows) list.append(row(it, p));
    $("v-empty").classList.toggle("hidden", items.length > 0);
  }

  function button(label, onClick, cls) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "btn sm " + (cls || "");
    b.textContent = label;
    b.addEventListener("click", onClick);
    return b;
  }

  function row(it, p) {
    const li = document.createElement("li");
    li.className = "vault-item";
    li.style.display = "block";
    const title = document.createElement("div");
    title.className = "title";
    title.textContent = p.t;
    const body = document.createElement("div");
    body.className = "body hidden";
    body.textContent = p.b;
    const acts = document.createElement("div");
    acts.className = "acts";
    const toggle = button("👁️ Göster", () => {
      const hidden = body.classList.toggle("hidden");
      toggle.textContent = hidden ? "👁️ Göster" : "🙈 Gizle";
    });
    acts.append(toggle);
    if (p.b && navigator.clipboard) {
      acts.append(button("📋 Kopyala", async () => {
        await navigator.clipboard.writeText(p.b);
        status("Kopyalandı. Yapıştırdıktan sonra panoyu temizlemeyi unutma.");
      }));
    }
    acts.append(button("✏️ Düzenle", () => edit(it.id)));
    acts.append(button("Sil", async () => {
      if (!confirm(`“${p.t}” silinsin mi? (Çöp kutusundan 30 gün içinde geri getirilebilir.)`)) return;
      try {
        await post(root.dataset.itemUrl.replace(/0$/, it.id) + "/sil", {});
        items = items.filter((x) => x.id !== it.id);
        plain.delete(it.id);
        render();
        status("Kayıt çöp kutusuna taşındı.");
      } catch (e) {
        status(e.message, true);
      }
    }, "danger"));
    li.append(title, body, acts);
    return li;
  }

  function edit(id) {
    const form = $("v-edit-form");
    const p = id ? plain.get(id) : { t: "", b: "" };
    form.elements.id.value = id || "";
    form.elements.title.value = p.t;
    form.elements.body.value = p.b;
    form.classList.remove("hidden");
    form.elements.title.focus();
  }

  // ---------- Olaylar ----------
  if (!window.crypto || !crypto.subtle || !window.isSecureContext) {
    $("v-unsupported").classList.remove("hidden");
    return;
  }
  show(meta ? "v-locked" : "v-setup");

  $("v-setup-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    busy(f, async () => {
      const pass = f.elements.pass.value;
      if (pass.length < 10) throw new Error("Kasa parolası en az 10 karakter olmalı.");
      if (pass !== f.elements.pass2.value) throw new Error("Parolalar eşleşmiyor.");
      status("Kasa oluşturuluyor…");
      const raw = crypto.getRandomValues(new Uint8Array(32));
      const fields = await wrapFields(raw, pass, f.elements.hint.value.trim());
      await post(root.dataset.setupUrl, fields);
      meta = { salt: fields.salt, iterations: ITERATIONS, wrapped_key: fields.wrapped_key, hint: fields.hint };
      f.reset();
      await openWith(raw);
      status("Kasa hazır. Kasa parolanı güvenli bir yere not et; unutulursa kayıtlar açılamaz.");
    });
  });

  $("v-unlock-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    busy(f, async () => {
      status("Açılıyor…");
      const raw = await unwrapRaw(f.elements.pass.value);
      f.reset();
      await openWith(raw);
    });
  });

  $("v-edit-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    busy(f, async () => {
      const id = f.elements.id.value ? Number(f.elements.id.value) : null;
      const p = { t: f.elements.title.value.trim(), b: f.elements.body.value };
      if (!p.t) throw new Error("Başlık boş olamaz.");
      const data = await seal(dataKey, te.encode(JSON.stringify(p)), AAD_ITEM);
      if (id) {
        const r = await post(root.dataset.itemUrl.replace(/0$/, id), { data });
        items = items.map((x) => (x.id === id ? { ...x, data, updated_at: r.updated_at } : x));
        plain.set(id, p);
      } else {
        const r = await post(root.dataset.createUrl, { data });
        items.push({ id: r.id, data, updated_at: r.updated_at });
        plain.set(r.id, p);
      }
      f.reset();
      f.classList.add("hidden");
      render();
      status("Şifrelendi ve kaydedildi.");
    });
  });

  $("v-change-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const f = e.target;
    busy(f, async () => {
      const pass = f.elements.pass.value;
      if (pass.length < 10) throw new Error("Yeni kasa parolası en az 10 karakter olmalı.");
      if (pass !== f.elements.pass2.value) throw new Error("Yeni parolalar eşleşmiyor.");
      status("Değiştiriliyor…");
      const raw = await unwrapRaw(f.elements.old.value);
      const fields = await wrapFields(raw, pass, f.elements.hint.value.trim());
      raw.fill(0);
      await post(root.dataset.changeUrl, { ...fields, password: f.elements.password.value });
      meta = { salt: fields.salt, iterations: ITERATIONS, wrapped_key: fields.wrapped_key, hint: fields.hint };
      f.reset();
      f.closest("details").open = false;
      status("Kasa parolası değişti. Kayıtlar yeniden şifrelenmedi; yeni parolayla açılırlar.");
    });
  });

  $("v-new-btn").addEventListener("click", () => edit(null));
  $("v-cancel-btn").addEventListener("click", () => {
    $("v-edit-form").reset();
    $("v-edit-form").classList.add("hidden");
  });
  $("v-filter").addEventListener("input", render);
  const lockBtn = $("v-lock-btn");
  if (lockBtn) lockBtn.addEventListener("click", () => lock("Kasa kilitlendi."));
  for (const ev of ["click", "keydown", "input", "touchstart"]) document.addEventListener(ev, touch, { passive: true });
  // Sekme arka planda 5 dakikadan uzun kalırsa (zamanlayıcı uyumuş olabilir) dönüşte kilitle
  let hiddenAt = 0;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) hiddenAt = Date.now();
    else if (dataKey && hiddenAt && Date.now() - hiddenAt > IDLE_MS) lock("Kasa kilitlendi.");
  });
})();
