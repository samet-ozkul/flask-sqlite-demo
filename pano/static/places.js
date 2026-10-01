// 🗺️ Harita: Leaflet + OpenStreetMap ile şehirler, yerler ve park yeri; yer formunda konum seçme.
// Leaflet (CDN) yüklenemezse harita atlanır: liste, "📍 Şu anki konumum" ve "🅿️ Park ettim" yine çalışır.
// Kullanıcı verisi (ad, not) DOM'a sadece textContent ile girer.
(() => {
  const $ = (sel, root = document) => root.querySelector(sel);
  const fix = (n) => Number(n).toFixed(6);
  const dirUrl = (lat, lon) => `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
  const osmUrl = (lat, lon) => `https://www.openstreetmap.org/?mlat=${lat}&mlon=${lon}#map=17/${lat}/${lon}`;
  const STATUS = { visited: "✅ Gezdim", wish: "🔖 Gitmek istiyorum" };

  // el("a", {href: "/x"}, "metin", başkaEleman) — metinler text node olarak eklenir
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k === "cls" ? "class" : k, v);
    for (const c of children) if (c !== null && c !== undefined && c !== "") node.append(c);
    return node;
  }

  function locate() {
    return new Promise((resolve, reject) => {
      if (!window.isSecureContext || !navigator.geolocation) {
        reject(new Error("Bu tarayıcıda konum alınamıyor (site HTTPS ile açılmalı)."));
        return;
      }
      navigator.geolocation.getCurrentPosition((pos) => resolve(pos.coords), (err) => reject(new Error(
        err.code === 1 ? "Konum izni verilmedi. Tarayıcı ayarlarından bu siteye konum izni ver."
          : err.code === 3 ? "Konum zamanında alınamadı; tekrar dene." : "Konum alınamadı.")),
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 });
    });
  }

  // ---------- Yer formu: konum alanları ----------
  const form = $("form[data-place-form]");
  let onPick = null; // harita hazırsa seçili noktanın işaretçisini taşır

  function setLocation(lat, lon, note) {
    form.elements.lat.value = lat === null ? "" : fix(lat);
    form.elements.lon.value = lon === null ? "" : fix(lon);
    $("[data-loc-status]", form).textContent = lat === null
      ? "Konum yok: haritaya dokun, konumunu kullan ya da link yapıştır. Konumsuz yer listede görünür."
      : `📍 ${Number(lat).toFixed(5)}, ${Number(lon).toFixed(5)}${note ? " · " + note : ""}`;
    if (onPick) onPick(lat, lon);
  }

  if (form) {
    const locBtn = $("[data-locate]", form);
    const clearBtn = $("[data-clear-loc]", form);
    locBtn.classList.remove("hidden");
    clearBtn.classList.remove("hidden");
    locBtn.addEventListener("click", async () => {
      locBtn.disabled = true;
      $("[data-loc-status]", form).textContent = "📡 Konum alınıyor…";
      try {
        const c = await locate();
        setLocation(c.latitude, c.longitude, `şu anki konum (±${Math.round(c.accuracy)} m)`);
      } catch (e) {
        $("[data-loc-status]", form).textContent = e.message;
      } finally {
        locBtn.disabled = false;
      }
    });
    clearBtn.addEventListener("click", () => {
      form.elements.maplink.value = "";
      setLocation(null, null);
    });
  }

  // ---------- Park ettim: önce tarayıcı konumu, sonra form gönderilir ----------
  document.querySelectorAll("form[data-park-form]").forEach((park) => {
    park.addEventListener("submit", async (e) => {
      if (park.elements.lat.value) return;
      e.preventDefault();
      const status = $("[data-park-status]", park);
      const btn = $("button", park);
      btn.disabled = true;
      status.style.color = "";
      status.textContent = "📡 Konum alınıyor…";
      try {
        const c = await locate();
        park.elements.lat.value = fix(c.latitude);
        park.elements.lon.value = fix(c.longitude);
        status.textContent = `Kaydediliyor… (±${Math.round(c.accuracy)} m)`;
        park.submit();
      } catch (err) {
        status.textContent = err.message;
        status.style.color = "var(--danger)";
        btn.disabled = false;
      }
    });
  });

  // ---------- Harita ----------
  const mapEl = $("[data-map]");
  const dataEl = document.getElementById("map-data");
  if (!mapEl || !dataEl || !window.L) return; // Leaflet yüklenemedi: yedek metin görünür kalır
  const data = JSON.parse(dataEl.textContent);
  mapEl.textContent = "";
  mapEl.classList.remove("off");

  const map = L.map(mapEl, { scrollWheelZoom: false });
  map.once("focus", () => map.scrollWheelZoom.enable()); // sayfa kaydırırken harita yakınlaşmasın
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    referrerPolicy: "strict-origin-when-cross-origin", // OSM döşemeleri Referer ister; site kökü yeterli
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
  }).addTo(map);

  function pin(content, cls, size) {
    return L.divIcon({
      html: el("span", { cls: "pin " + cls }, content), className: "pin-wrap",
      iconSize: [size, size], iconAnchor: [size / 2, size / 2], popupAnchor: [0, -size / 2],
    });
  }

  const links = (lat, lon) => el("div", { cls: "pop-links" },
    el("a", { href: dirUrl(lat, lon), target: "_blank", rel: "noopener" }, "🧭 Yol tarifi"),
    el("a", { href: osmUrl(lat, lon), target: "_blank", rel: "noopener" }, "🗺️ Haritada aç"));

  const points = [];
  for (const c of data.cities) {
    L.marker([c.lat, c.lon], { icon: pin("🏙️", "city " + c.status, 38), title: c.name, zIndexOffset: -100 })
      .bindPopup(el("div", {},
        el("a", { href: c.url, cls: "pop-title" }, c.name),
        el("div", { cls: "muted small" }, [c.country, STATUS[c.status], c.count ? `${c.count} yer` : ""].filter(Boolean).join(" · "))))
      .addTo(map);
    points.push([c.lat, c.lon]);
  }
  for (const p of data.places) {
    L.marker([p.lat, p.lon], { icon: pin(p.icon, p.status, 30), title: p.name })
      .bindPopup(el("div", {},
        el("a", { href: p.url, cls: "pop-title" }, `${p.icon} ${p.name}`),
        el("div", { cls: "muted small" }, `${p.category} · ${STATUS[p.status]}`),
        p.rating ? el("div", { cls: "stars" }, "★".repeat(p.rating) + "☆".repeat(5 - p.rating)) : null,
        links(p.lat, p.lon)))
      .addTo(map);
    points.push([p.lat, p.lon]);
  }
  let parkMarker = null;
  if (data.parking) {
    const k = data.parking;
    parkMarker = L.marker([k.lat, k.lon], { icon: pin("🚗", "park", 36), title: "Arabam", zIndexOffset: 500 })
      .bindPopup(el("div", {},
        el("div", { cls: "pop-title" }, "🅿️ Arabam burada"),
        el("div", { cls: "muted small" }, k.saved),
        k.note ? el("div", {}, k.note) : null,
        links(k.lat, k.lon)))
      .addTo(map);
    points.push([k.lat, k.lon]);
  }

  if (points.length > 1) map.fitBounds(points, { padding: [30, 30], maxZoom: data.max_zoom });
  else if (points.length === 1) map.setView(points[0], Math.min(data.max_zoom, 12));
  else map.setView([data.view.lat, data.view.lon], data.view.zoom);

  const focusPark = $("[data-focus-parking]");
  if (focusPark && parkMarker) {
    focusPark.addEventListener("click", (e) => {
      e.preventDefault();
      mapEl.scrollIntoView({ behavior: "smooth", block: "center" });
      map.setView(parkMarker.getLatLng(), 17);
      parkMarker.openPopup();
    });
  }

  // ---------- Konum seçme: haritaya dokun, işaretçiyi sürükle ----------
  const pickForm = mapEl.dataset.pick ? document.getElementById(mapEl.dataset.pick) : null;
  if (form && pickForm === form) {
    let marker = null;
    onPick = (lat, lon) => {
      if (lat === null) {
        if (marker) marker.remove();
        marker = null;
        return;
      }
      if (!marker) {
        marker = L.marker([lat, lon], { icon: pin("📌", "pick", 34), draggable: true, zIndexOffset: 1000, title: "Seçilen konum" })
          .addTo(map);
        marker.on("dragend", () => {
          const ll = marker.getLatLng();
          setLocation(ll.lat, ll.lng, "haritadan");
        });
      } else {
        marker.setLatLng([lat, lon]);
      }
      if (!map.getBounds().contains([lat, lon])) map.setView([lat, lon], Math.max(map.getZoom(), 15));
    };
    map.on("click", (e) => setLocation(e.latlng.lat, e.latlng.lng, "haritadan"));
    mapEl.style.cursor = "crosshair";
    if (form.elements.lat.value) onPick(Number(form.elements.lat.value), Number(form.elements.lon.value));
  }
})();
