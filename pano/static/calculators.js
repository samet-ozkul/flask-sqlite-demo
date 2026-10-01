// 🧮 Hesaplayıcılar: bütün hesaplar tarayıcıda, yazdıkça güncellenir; sunucuya bir şey gönderilmez.
// Son girilen değerler bu cihazın localStorage'ında durur. Hesap fonksiyonları saftır (DOM'a dokunmaz) ve
// Node ile denenebilir: node tests/calculators_check.js
(() => {
  const isNum = (v) => typeof v === "number" && Number.isFinite(v);
  const nonNeg = (v) => isNum(v) && v >= 0; // null >= 0 JS'te true olduğu için

  // ---------- Sayı: "1.234,56" / "1234,56" / "1234.56" (utils.parse_number ile aynı kurallar) ----------
  function parseNumber(value) {
    if (value === null || value === undefined) return null;
    let s = String(value).trim().replace(/[\s₺%]/g, "");
    if (!s) return null;
    if (s.includes(",") && s.includes(".")) {
      // hangisi sondaysa o ondalık ayırıcıdır
      s = s.lastIndexOf(",") > s.lastIndexOf(".") ? s.replace(/\./g, "").replace(/,/g, ".") : s.replace(/,/g, "");
    } else if (s.includes(",")) {
      s = s.replace(/,/g, ".");
    } else if (/^-?\d{1,3}(\.\d{3})+$/.test(s)) {
      // Sadece nokta ve 3'lü gruplar: Türkçe binlik ayırıcı ("1.000", "12.500", "1.234.567")
      s = s.replace(/\./g, "");
    }
    if (!/^[-+]?(\d+\.?\d*|\.\d+)$/.test(s)) return null;
    const n = Number(s);
    return isNum(n) ? n : null;
  }

  // utils.fmt_number gibi: küsurat yoksa "1.000", varsa "1.000,50"; sayı değilse "—"
  const formatters = new Map();
  function fmt(n, decimals = 2) {
    if (!isNum(n)) return "—";
    const key = Math.abs(n - Math.round(n)) < 0.5 * 10 ** -decimals ? 0 : decimals;
    if (Math.abs(n) < 0.5 * 10 ** -key) n = 0; // "-0" yazmasın
    if (!formatters.has(key)) {
      formatters.set(key, new Intl.NumberFormat("tr-TR", { minimumFractionDigits: key, maximumFractionDigits: key }));
    }
    return formatters.get(key).format(n);
  }

  // Birim çevirisinde küsurat sayısı değere göre değişir (0,0254 m · 1.609,344 m): 10 anlamlı basamak
  const sigFormatter = new Intl.NumberFormat("tr-TR", { maximumSignificantDigits: 10 });
  const fmtSig = (n) => (isNum(n) ? sigFormatter.format(Math.abs(n) < 1e-12 ? 0 : n) : "—");

  // ---------- Kredi (anüite) ----------
  const LOAN_TAX = 0.30; // KKDF %15 + BSMV %15, faizin üstüne (ihtiyaç/taşıt; konut kredisinde yok)
  const MAX_MONTHS = 600;

  // Aylık faiz yüzde olarak (3 = %3). Vergiliyse efektif aylık oran = faiz × 1,30.
  function loan(amount, monthlyRate, months, withTax) {
    if (!(amount > 0) || !nonNeg(monthlyRate) || !Number.isInteger(months) || months < 1 || months > MAX_MONTHS) return null;
    const r = monthlyRate / 100;
    const eff = r * (withTax ? 1 + LOAN_TAX : 1);
    const payment = eff === 0 ? amount / months : (amount * eff) / (1 - Math.pow(1 + eff, -months));
    const schedule = [];
    let balance = amount;
    let interest = 0;
    let tax = 0;
    for (let month = 1; month <= months; month++) {
      const i = balance * r;
      const t = withTax ? i * LOAN_TAX : 0;
      const principal = month === months ? balance : payment - i - t; // son ayda kuruş farkı kalmasın
      balance = month === months ? 0 : balance - principal;
      interest += i;
      tax += t;
      schedule.push({ month, payment, interest: i, tax: t, principal, balance });
    }
    return { payment, total: payment * months, interest, tax, effRate: eff * 100, schedule };
  }

  // ---------- Mevduat: brüt = tutar × oran × gün / 365 ----------
  function deposit(amount, annualRate, days, withholding) {
    if (!(amount > 0) || !nonNeg(annualRate) || !(days > 0) || !(nonNeg(withholding) && withholding <= 100)) return null;
    const gross = (amount * annualRate / 100) * days / 365;
    const tax = gross * withholding / 100;
    return { gross, tax, net: gross - tax, final: amount + gross - tax };
  }

  // ---------- KDV ----------
  function vat(amount, rate, included) {
    if (!isNum(amount) || !nonNeg(rate)) return null;
    const net = included ? amount / (1 + rate / 100) : amount;
    const tax = included ? amount - net : net * rate / 100;
    return { net, vat: tax, gross: net + tax };
  }

  // ---------- Yüzde ----------
  const percentOf = (x, y) => (isNum(x) && isNum(y) ? x * y / 100 : null);              // X'in %Y'si
  const whatPercent = (x, y) => (isNum(x) && isNum(y) && y !== 0 ? x / y * 100 : null);  // X, Y'nin yüzde kaçı
  const percentChange = (x, y) => (isNum(x) && isNum(y) && x !== 0 ? (y - x) / Math.abs(x) * 100 : null);
  const percentAddSub = (x, y) => (isNum(x) && isNum(y) ? { plus: x * (1 + y / 100), minus: x * (1 - y / 100) } : null);

  // ---------- Tarih (gün numarası = 1970'ten beri UTC gün; yaz saati kaymaz) ----------
  const DAY = 86400000;
  const WEEKDAYS = ["Pazar", "Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi"];
  const MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"];

  function parseIso(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    if (!m) return null;
    const y = +m[1], mo = +m[2], d = +m[3];
    const t = new Date(Date.UTC(y, mo - 1, d));
    if (y < 1000 || t.getUTCMonth() !== mo - 1 || t.getUTCDate() !== d) return null; // 2026-02-30 gibi
    return { y, m: mo, d, day: t.getTime() / DAY };
  }
  const dayNumber = (iso) => (parseIso(iso) || { day: null }).day;
  const isoFromDay = (n) => new Date(n * DAY).toISOString().slice(0, 10);
  const weekdayOf = (n) => WEEKDAYS[(((n + 4) % 7) + 7) % 7]; // 1 Ocak 1970 perşembe
  const longDate = (iso) => {
    const p = parseIso(iso);
    return p ? `${p.d} ${MONTHS[p.m - 1]} ${p.y}` : "—";
  };

  // first gününden başlayan count gün içinde cumartesi-pazar olmayanlar (resmi tatiller düşülmez)
  function workdays(first, count) {
    let n = Math.floor(count / 7) * 5;
    const start = (((first + 4) % 7) + 7) % 7;
    for (let i = 0; i < count % 7; i++) {
      const dow = (start + i) % 7;
      if (dow !== 0 && dow !== 6) n++;
    }
    return n;
  }

  // İki tarih arası: başlangıç günü sayılmaz, bitiş sayılır; inclusive ise ikisi de sayılır (+1 gün)
  function dateDiff(startIso, endIso, inclusive) {
    let a = dayNumber(startIso);
    let b = dayNumber(endIso);
    if (a === null || b === null) return null;
    const reversed = b < a;
    if (reversed) [a, b] = [b, a];
    const days = b - a + (inclusive ? 1 : 0);
    return {
      days, reversed, weeks: days / 7, months: days / 30.436875, years: days / 365.2425,
      workdays: workdays(inclusive ? a : a + 1, days),
    };
  }

  function addDays(iso, n) {
    const a = dayNumber(iso);
    if (a === null || !Number.isInteger(n) || Math.abs(n) > 1000000) return null;
    const out = isoFromDay(a + n);
    return /^\d{4}-/.test(out) ? { iso: out, weekday: weekdayOf(a + n) } : null;
  }

  // Doğum tarihi + n ay; gün o ayda yoksa ay sonuna kırpılır (31 Ocak + 1 ay = 28/29 Şubat, 29 Şubat → 28 Şubat)
  function addMonthsClamped(p, n) {
    const idx = p.m - 1 + n;
    const y = p.y + Math.floor(idx / 12);
    const m = ((idx % 12) + 12) % 12;
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    return Date.UTC(y, m, Math.min(p.d, last)) / DAY;
  }

  // Yaş (yıl, ay, gün) ve sonraki doğum günü. 29 Şubat doğumlular artık olmayan yılda 28 Şubat'ta kutlar
  // (Önemli Günler modülüyle aynı).
  function age(birthIso, todayIso) {
    const b = parseIso(birthIso);
    const t = parseIso(todayIso);
    if (!b || !t || b.day > t.day) return null;
    let months = (t.y - b.y) * 12 + (t.m - b.m);
    let anchor = addMonthsClamped(b, months);
    if (anchor > t.day) anchor = addMonthsClamped(b, --months);
    let years = t.y - b.y;
    let next = addMonthsClamped(b, years * 12);
    if (next < t.day || years === 0) next = addMonthsClamped(b, ++years * 12);
    return {
      years: Math.floor(months / 12), months: months % 12, days: t.day - anchor, totalDays: t.day - b.day,
      nextIn: next - t.day, nextAge: years, nextIso: isoFromDay(next),
    };
  }

  // ---------- Birim: her birimin temel birim karşılığı (m, kg, L, m²); sıcaklık °C üzerinden ----------
  const UNITS = {
    length: { label: "Uzunluk", pair: ["km", "mi"], units: {
      mm: ["mm", 0.001], cm: ["cm", 0.01], m: ["m", 1], km: ["km", 1000],
      in: ["inç", 0.0254], ft: ["ft", 0.3048], mi: ["mil", 1609.344] } },
    weight: { label: "Ağırlık", pair: ["kg", "lb"], units: {
      g: ["g", 0.001], kg: ["kg", 1], t: ["ton", 1000], oz: ["ons", 0.028349523125], lb: ["lb", 0.45359237] } },
    temperature: { label: "Sıcaklık", pair: ["C", "F"], units: { C: ["°C"], F: ["°F"], K: ["K"] } },
    volume: { label: "Hacim", pair: ["l", "gal"], units: {
      ml: ["ml", 0.001], l: ["L", 1], m3: ["m³", 1000], gal: ["galon (US)", 3.785411784] } },
    area: { label: "Alan", pair: ["m2", "donum"], units: {
      m2: ["m²", 1], donum: ["dönüm", 1000], ha: ["hektar", 10000], km2: ["km²", 1000000], ft2: ["ft²", 0.09290304] } },
  };
  const TO_C = { C: (v) => v, F: (v) => (v - 32) * 5 / 9, K: (v) => v - 273.15 };
  const FROM_C = { C: (c) => c, F: (c) => c * 9 / 5 + 32, K: (c) => c + 273.15 };

  function convertUnit(category, value, from, to) {
    const cat = UNITS[category];
    if (!cat || !(from in cat.units) || !(to in cat.units) || !isNum(value)) return null;
    if (category === "temperature") {
      const c = TO_C[from](value);
      return c < -273.15 - 1e-9 ? null : FROM_C[to](c); // mutlak sıfırın altı yok
    }
    return value * cat.units[from][1] / cat.units[to][1];
  }

  // ---------- Döviz: rates = {USD: 48.85, ..., XAU: gram altın} (1 birimin TL karşılığı) ----------
  function convertCurrency(amount, from, to, rates) {
    const rate = (c) => (c === "TRY" ? 1 : rates && isNum(rates[c]) && rates[c] > 0 ? rates[c] : null);
    const a = rate(from);
    const b = rate(to);
    return isNum(amount) && a !== null && b !== null ? amount * a / b : null;
  }

  // ---------- Hesap bölüşme ----------
  function splitBill(amount, people, tipPct) {
    if (!(amount > 0) || !Number.isInteger(people) || people < 1 || !nonNeg(tipPct)) return null;
    const tip = amount * tipPct / 100;
    return { tip, total: amount + tip, perPerson: (amount + tip) / people };
  }

  // ---------- Yakıt: litre fiyatı boşsa sadece litre ----------
  function fuelCost(distance, per100, price, people, roundTrip) {
    if (!(distance > 0) || !(per100 > 0)) return null;
    const km = distance * (roundTrip ? 2 : 1);
    const liters = km * per100 / 100;
    const cost = price > 0 ? liters * price : null;
    const n = Number.isInteger(people) && people >= 1 ? people : 1;
    return { km, liters, cost, perPerson: cost === null ? null : cost / n };
  }

  // ======================= Sayfa =======================
  function initPage(root) {
    const STORE = "pano-hesapla-v1";
    const SYMBOLS = { TRY: "₺", USD: "$", EUR: "€", GBP: "£", XAU: "gr altın" };
    const rates = JSON.parse(document.getElementById("calc-rates").textContent);
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(STORE)) || {};
    } catch (e) {
      saved = {}; // özel pencere, engellenmiş depolama
    }
    if (typeof saved !== "object" || Array.isArray(saved)) saved = {};

    const money = (n) => (isNum(n) ? fmt(n) + " ₺" : "—");
    const pct = (p) => {
      if (!isNum(p)) return "—";
      const s = fmt(Math.abs(p));
      return (p < 0 && s !== "0" ? "−" : "") + "%" + s;
    };
    const val = (f, name) => parseNumber(f.elements[name].value);
    const put = (f, name, text) => f.querySelectorAll(`[data-out="${name}"]`).forEach((el) => { el.textContent = text; });
    const today = () => {
      const d = new Date();
      return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    };
    function listItems(f, items) {
      f.querySelector("[data-list]").replaceChildren(...items.map((text) => {
        const li = document.createElement("li");
        li.textContent = text;
        return li;
      }));
    }

    function fillUnits(f, reset) {
      const cat = UNITS[f.elements.cat.value] || UNITS.length;
      cat.pair.forEach((def, i) => {
        const sel = f.elements[i ? "to" : "from"];
        const prev = sel.value;
        sel.replaceChildren(...Object.entries(cat.units).map(([k, [label]]) => new Option(label, k)));
        sel.value = !reset && prev in cat.units ? prev : def;
      });
    }

    function plan(f, r, withTax) {
      const body = f.querySelector("tbody");
      if (!r || !f.querySelector("details").open) {
        body.replaceChildren();
        return;
      }
      body.replaceChildren(...r.schedule.map((row) => {
        const tr = document.createElement("tr");
        const cells = [String(row.month), fmt(row.payment), fmt(row.interest)];
        if (withTax) cells.push(fmt(row.tax));
        cells.push(fmt(row.principal), fmt(row.balance));
        for (const text of cells) {
          const td = document.createElement("td");
          td.className = "num";
          td.textContent = text;
          tr.append(td);
        }
        return tr;
      }));
    }

    const CALCS = {
      kredi(f) {
        const withTax = f.elements.tax.checked;
        const r = loan(val(f, "amount"), val(f, "rate"), val(f, "months"), withTax);
        put(f, "payment", r ? money(r.payment) : "—");
        put(f, "total", r ? money(r.total) : "—");
        put(f, "interest", r ? money(r.interest) : "—");
        put(f, "tax", r ? money(r.tax) : "—");
        put(f, "eff", r && withTax ? `Efektif aylık oran ${pct(r.effRate)}` : "");
        put(f, "cost", r && withTax ? `Faiz + vergi: ${money(r.interest + r.tax)}` : "");
        f.querySelectorAll(".tax-only").forEach((el) => el.classList.toggle("hidden", !withTax));
        plan(f, r, withTax);
      },
      mevduat(f) {
        const w = val(f, "withholding");
        const r = deposit(val(f, "amount"), val(f, "rate"), val(f, "days"), w === null ? 0 : w);
        for (const k of ["gross", "tax", "net", "final"]) put(f, k, r ? money(r[k]) : "—");
      },
      kdv(f) {
        const choice = f.elements.rate.value;
        const rate = choice === "custom" ? val(f, "custom") : parseNumber(choice);
        const r = vat(val(f, "amount"), rate, f.elements.mode.value === "inc");
        for (const k of ["net", "vat", "gross"]) put(f, k, r ? money(r[k]) : "—");
      },
      yuzde(f) {
        put(f, "of", fmt(percentOf(val(f, "x1"), val(f, "y1"))));
        put(f, "what", pct(whatPercent(val(f, "x2"), val(f, "y2"))));
        const c = percentChange(val(f, "x3"), val(f, "y3"));
        const cs = c === null ? null : fmt(Math.abs(c));
        put(f, "change", cs === null ? "—" : cs === "0" ? "%0 (değişmedi)" : `${c > 0 ? "+" : "−"}%${cs} ${c > 0 ? "artış" : "azalış"}`);
        const s = percentAddSub(val(f, "x4"), val(f, "y4"));
        put(f, "plus", s ? fmt(s.plus) : "—");
        put(f, "minus", s ? fmt(s.minus) : "—");
      },
      tarih(f) {
        const d = dateDiff(f.elements.d1.value, f.elements.d2.value, f.elements.inclusive.checked);
        put(f, "days", d ? `${fmt(d.days)} gün` : "—");
        put(f, "approx", d ? `≈ ${fmt(d.weeks, 1)} hafta · ${fmt(d.months, 1)} ay · ${fmt(d.years, 1)} yıl` +
          (d.reversed ? " (bitiş başlangıçtan önce)" : "") : "");
        put(f, "workdays", d ? `${fmt(d.workdays)} iş günü` : "—");
        const n = val(f, "n");
        const a = addDays(f.elements.base.value, n === null ? null : f.elements.op.value === "sub" ? -n : n);
        put(f, "added", a ? `${longDate(a.iso)}, ${a.weekday}` : "—");
        const g = age(f.elements.birth.value, today());
        put(f, "age", g ? `${g.years} yıl ${g.months} ay ${g.days} gün` : "—");
        put(f, "next", !g ? "" : g.nextIn === 0 ? `Bugün doğum günü! 🎂 (${g.nextAge}. yaş)` :
          `Sonraki doğum gününe ${fmt(g.nextIn)} gün (${longDate(g.nextIso)}, ${g.nextAge}. yaş) · toplam ${fmt(g.totalDays)} gün`);
      },
      birim(f, e) {
        if (e && e.target.name === "cat") fillUnits(f, true);
        const cat = f.elements.cat.value;
        const from = f.elements.from.value;
        const v = val(f, "value");
        const units = UNITS[cat].units;
        const r = convertUnit(cat, v, from, f.elements.to.value);
        put(f, "result", r === null ? "—" : `${fmtSig(r)} ${units[f.elements.to.value][0]}`);
        listItems(f, Object.keys(units).filter((k) => k !== from).map((k) => `${fmtSig(convertUnit(cat, v, from, k))} ${units[k][0]}`));
      },
      doviz(f) {
        const amount = val(f, "amount");
        const from = f.elements.from.value;
        const to = f.elements.to.value;
        const r = convertCurrency(amount, from, to, rates);
        put(f, "result", r === null ? "—" : `${fmt(r)} ${SYMBOLS[to]}`);
        const codes = [...f.elements.to.options].map((o) => o.value).filter((c) => c !== from);
        listItems(f, codes.map((c) => `${fmt(convertCurrency(amount, from, c, rates))} ${SYMBOLS[c]}`));
      },
      bolus(f) {
        const r = splitBill(val(f, "amount"), val(f, "people"), parseNumber(f.elements.tip.value));
        put(f, "per", r ? money(r.perPerson) : "—");
        put(f, "tip", r ? money(r.tip) : "—");
        put(f, "total", r ? money(r.total) : "—");
      },
      yakit(f) {
        const r = fuelCost(val(f, "distance"), val(f, "per100"), val(f, "price"), val(f, "people"), f.elements.round.checked);
        put(f, "liters", r ? `${fmt(r.liters)} L` : "—");
        put(f, "km", r ? `${fmt(r.km)} km` : "");
        put(f, "cost", r ? money(r.cost) : "—");
        put(f, "per", r ? money(r.perPerson) : "—");
      },
    };

    // ---------- Son değerler (localStorage) ----------
    function collect(f) {
      const out = {};
      for (const el of f.elements) {
        if (!el.name || el.type === "button") continue;
        if (el.type === "checkbox") out[el.name] = el.checked;
        else if (el.type === "radio") { if (el.checked) out[el.name] = el.value; }
        else out[el.name] = el.value;
      }
      return out;
    }
    function apply(f, values) {
      for (const el of f.elements) {
        if (!el.name || !(el.name in values)) continue;
        const v = values[el.name];
        if (el.type === "checkbox") el.checked = v === true;
        else if (el.type === "radio") el.checked = el.value === v;
        else if (typeof v !== "string") continue;
        else if (el.tagName === "SELECT") { if ([...el.options].some((o) => o.value === v)) el.value = v; }
        else el.value = v.slice(0, 40);
      }
    }
    function remember(f) {
      saved[f.dataset.calc] = collect(f);
      try {
        localStorage.setItem(STORE, JSON.stringify(saved));
      } catch (e) {
        /* depolama kapalı ya da dolu: hatırlamadan devam */
      }
    }

    for (const f of root.querySelectorAll("form[data-calc]")) {
      const calc = CALCS[f.dataset.calc];
      if (!calc) continue;
      const values = saved[f.dataset.calc] || {};
      if (f.dataset.calc === "birim") {
        f.elements.cat.replaceChildren(...Object.entries(UNITS).map(([k, c]) => new Option(c.label, k)));
        if (values.cat in UNITS) f.elements.cat.value = values.cat;
        fillUnits(f, true);
      }
      apply(f, values);
      for (const name of ["d1", "base"]) if (f.elements[name] && !f.elements[name].value) f.elements[name].value = today();
      const update = (e) => {
        calc(f, e);
        remember(f);
      };
      f.addEventListener("input", update);
      f.addEventListener("change", update);
      f.addEventListener("submit", (e) => e.preventDefault()); // Enter sayfayı yenilemesin
      f.querySelectorAll("[data-swap]").forEach((btn) => btn.addEventListener("click", () => {
        const a = f.elements.from.value;
        f.elements.from.value = f.elements.to.value;
        f.elements.to.value = a;
        update();
      }));
      f.querySelectorAll("details").forEach((d) => d.addEventListener("toggle", () => calc(f)));
      calc(f);
    }
  }

  if (typeof document !== "undefined") {
    const root = document.getElementById("calc");
    if (root) initPage(root);
  }

  if (typeof module !== "undefined") {
    module.exports = {
      parseNumber, fmt, fmtSig, loan, deposit, vat, percentOf, whatPercent, percentChange, percentAddSub,
      dayNumber, dateDiff, workdays, addDays, age, longDate, UNITS, convertUnit, convertCurrency, splitBill, fuelCost,
    };
  }
})();
