// Hesaplayıcıların saf fonksiyonları (pano/static/calculators.js).
// Çalıştır: node tests/calculators_check.js
const assert = require("assert");
const path = require("path");
const C = require(path.join(__dirname, "..", "pano", "static", "calculators.js"));

const near = (a, b, eps = 0.005) => assert.ok(Math.abs(a - b) <= eps, `${a} ≈ ${b} bekleniyordu`);

// ---------- Sayı ayrıştırma ve biçim ----------
const parse = {
  "1.234,56": 1234.56, "1234,56": 1234.56, "1234.56": 1234.56, "12,5": 12.5, "1.000": 1000, "12.500": 12500,
  "1.234.567": 1234567, "1.5": 1.5, "1,234.5": 1234.5, " 250 ₺ ": 250, "%18": 18, "-1.250,5": -1250.5, "0,5": 0.5,
  "": null, "abc": null, "1,2,3": null, "1.2.3": null, "1e5": null, "Infinity": null,
};
for (const [s, n] of Object.entries(parse)) assert.strictEqual(C.parseNumber(s), n, `parseNumber(${JSON.stringify(s)})`);
assert.strictEqual(C.parseNumber(null), null);
assert.strictEqual(C.fmt(10046.2085), "10.046,21");
assert.strictEqual(C.fmt(1000), "1.000");
assert.strictEqual(C.fmt(1.5), "1,50");
assert.strictEqual(C.fmt(-0.001), "0");
assert.strictEqual(C.fmt(null), "—");
assert.strictEqual(C.fmtSig(2.5399999999999996), "2,54");

// ---------- Kredi ----------
const k = C.loan(100000, 3, 12, false);
near(k.payment, 10046.21);
assert.strictEqual(C.fmt(k.payment), "10.046,21");
near(k.total, 120554.5);
near(k.interest, 20554.5);
assert.strictEqual(k.tax, 0);
assert.strictEqual(k.schedule.length, 12);
near(k.schedule[0].interest, 3000);
near(k.schedule[0].principal, 7046.21);
assert.strictEqual(k.schedule[11].balance, 0);
near(k.schedule.reduce((s, r) => s + r.principal, 0), 100000, 1e-6);
// Vergili: efektif aylık oran %3 × 1,30 = %3,9; faizin %30'u vergi
const kv = C.loan(100000, 3, 12, true);
near(kv.effRate, 3.9, 1e-9);
near(kv.payment, (100000 * 0.039) / (1 - Math.pow(1.039, -12)));
near(kv.tax, kv.interest * 0.3);
near(kv.interest + kv.tax + 100000, kv.total);
near(kv.schedule[0].tax, 900);
assert.ok(kv.payment > k.payment);
// Faizsiz: tutar / vade; geçersiz girişler
assert.strictEqual(C.loan(12000, 0, 12, true).payment, 1000);
assert.strictEqual(C.loan(12000, 0, 12, false).interest, 0);
for (const bad of [[0, 3, 12], [1000, -1, 12], [1000, 3, 0], [1000, 3, 12.5], [1000, 3, 601], [null, 3, 12]]) {
  assert.strictEqual(C.loan(...bad, false), null, `loan(${bad})`);
}

// ---------- Mevduat ----------
const m = C.deposit(100000, 40, 32, 15);
near(m.gross, 100000 * 0.4 * 32 / 365);
near(m.gross, 3506.85);
near(m.tax, 526.03);
near(m.net, 2980.82);
near(m.final, 102980.82);
assert.strictEqual(C.deposit(100000, 40, 365, 0).net, 40000);
assert.strictEqual(C.deposit(100000, 40, 0, 15), null);
assert.strictEqual(C.deposit(100000, 40, 32, 150), null);

// ---------- KDV ----------
const v1 = C.vat(1200, 20, true);
near(v1.net, 1000, 1e-9);
near(v1.vat, 200, 1e-9);
assert.strictEqual(v1.gross, 1200);
const v2 = C.vat(1000, 10, false);
near(v2.vat, 100, 1e-9);
near(v2.gross, 1100, 1e-9);
near(C.vat(101, 1, true).net, 100, 1e-9);
assert.strictEqual(C.vat(null, 20, true), null);
assert.strictEqual(C.vat(100, null, true), null);

// ---------- Yüzde ----------
assert.strictEqual(C.percentOf(250, 18), 45);
assert.strictEqual(C.whatPercent(45, 180), 25);
assert.strictEqual(C.whatPercent(45, 0), null);
assert.strictEqual(C.percentChange(80, 100), 25);
assert.strictEqual(C.percentChange(100, 80), -20);
assert.strictEqual(C.percentChange(0, 80), null);
assert.deepStrictEqual(C.percentAddSub(1500, 20), { plus: 1800, minus: 1200 });
assert.strictEqual(C.percentOf(null, 5), null);

// ---------- Tarih ----------
let d = C.dateDiff("2024-02-01", "2024-03-01", false);
assert.strictEqual(d.days, 29); // artık yıl: şubat 29 gün
assert.strictEqual(C.dateDiff("2023-02-01", "2023-03-01", false).days, 28);
assert.strictEqual(C.dateDiff("2024-01-01", "2025-01-01", false).days, 366);
assert.strictEqual(C.dateDiff("2026-03-28", "2026-03-30", false).days, 2); // yaz saati geçişi kaydırmaz
// İş günü: 5 Ekim 2026 pazartesi → 9 Ekim cuma
d = C.dateDiff("2026-10-05", "2026-10-09", false);
assert.deepStrictEqual([d.days, d.workdays], [4, 4]);
d = C.dateDiff("2026-10-05", "2026-10-09", true);
assert.deepStrictEqual([d.days, d.workdays], [5, 5]);
d = C.dateDiff("2026-10-03", "2026-10-05", false); // cumartesi → pazartesi: sadece pazartesi
assert.deepStrictEqual([d.days, d.workdays], [2, 1]);
d = C.dateDiff("2026-10-01", "2026-12-31", true); // 92 gün, 66 iş günü
assert.deepStrictEqual([d.days, d.workdays], [92, 66]);
d = C.dateDiff("2026-10-09", "2026-10-05", false); // ters sıra
assert.ok(d.reversed && d.days === 4);
assert.strictEqual(C.dateDiff("2026-02-30", "2026-03-01", false), null);
assert.strictEqual(C.dateDiff("", "2026-03-01", false), null);
// Gün ekle/çıkar, ay sonu ve artık yıl
assert.deepStrictEqual(C.addDays("2024-02-28", 1), { iso: "2024-02-29", weekday: "Perşembe" });
assert.deepStrictEqual(C.addDays("2023-02-28", 1), { iso: "2023-03-01", weekday: "Çarşamba" });
assert.deepStrictEqual(C.addDays("2026-10-01", 30), { iso: "2026-10-31", weekday: "Cumartesi" });
assert.deepStrictEqual(C.addDays("2026-12-31", 1), { iso: "2027-01-01", weekday: "Cuma" });
assert.deepStrictEqual(C.addDays("2026-03-01", -1), { iso: "2026-02-28", weekday: "Cumartesi" });
assert.strictEqual(C.addDays("2026-10-01", 1.5), null);
assert.strictEqual(C.longDate("2026-10-01"), "1 Ekim 2026");
// Yaş
let a = C.age("1990-05-15", "2026-10-01");
assert.deepStrictEqual([a.years, a.months, a.days], [36, 4, 16]);
assert.deepStrictEqual([a.nextIso, a.nextAge, a.nextIn], ["2027-05-15", 37, 226]);
a = C.age("1990-10-01", "2026-10-01");
assert.deepStrictEqual([a.years, a.months, a.days, a.nextIn, a.nextAge], [36, 0, 0, 0, 36]);
a = C.age("2000-01-31", "2001-03-01"); // ay sonu: 31 Ocak + 13 ay = 28 Şubat 2001
assert.deepStrictEqual([a.years, a.months, a.days], [1, 1, 1]);
a = C.age("2000-02-29", "2027-02-28"); // artık yıl doğumlu, artık olmayan yılda 28 Şubat
assert.deepStrictEqual([a.years, a.months, a.days, a.nextIn], [27, 0, 0, 0]);
a = C.age("2000-02-29", "2027-03-01");
assert.deepStrictEqual([a.nextIso, a.nextAge], ["2028-02-29", 28]);
a = C.age("2026-10-01", "2026-10-01"); // bugün doğan: sonraki doğum günü seneye
assert.deepStrictEqual([a.years, a.days, a.nextIn, a.nextAge], [0, 0, 365, 1]);
assert.strictEqual(C.age("2027-01-01", "2026-10-01"), null);

// ---------- Birim ----------
near(C.convertUnit("temperature", 100, "C", "F"), 212, 1e-9);
near(C.convertUnit("temperature", 98.6, "F", "C"), 37, 1e-9);
near(C.convertUnit("temperature", -40, "F", "C"), -40, 1e-9);
near(C.convertUnit("temperature", 0, "K", "C"), -273.15, 1e-9);
assert.strictEqual(C.convertUnit("temperature", -300, "C", "K"), null);
assert.strictEqual(C.convertUnit("area", 1, "donum", "m2"), 1000);
assert.strictEqual(C.convertUnit("area", 1, "ha", "donum"), 10);
assert.strictEqual(C.convertUnit("area", 5000, "m2", "donum"), 5);
near(C.convertUnit("length", 1, "mi", "km"), 1.609344, 1e-12);
near(C.convertUnit("length", 1, "in", "cm"), 2.54, 1e-12);
near(C.convertUnit("weight", 1, "lb", "kg"), 0.45359237, 1e-12);
near(C.convertUnit("volume", 1, "gal", "l"), 3.785411784, 1e-12);
assert.strictEqual(C.convertUnit("length", 1, "kg", "m"), null);
for (const cat of Object.values(C.UNITS)) for (const u of cat.pair) assert.ok(u in cat.units);

// ---------- Döviz, bölüşme, yakıt ----------
const rates = { USD: 48.85, EUR: 55.52, XAU: 4200, date: "2026-09-24" };
near(C.convertCurrency(100, "USD", "TRY", rates), 4885, 1e-9);
near(C.convertCurrency(4885, "TRY", "USD", rates), 100, 1e-9);
near(C.convertCurrency(8400, "TRY", "XAU", rates), 2, 1e-9);
near(C.convertCurrency(100, "EUR", "USD", rates), 113.6540, 1e-3);
assert.strictEqual(C.convertCurrency(100, "GBP", "TRY", rates), null);
assert.strictEqual(C.convertCurrency(100, "USD", "TRY", null), null);
const s = C.splitBill(1000, 4, 10);
assert.deepStrictEqual([s.tip, s.total, s.perPerson], [100, 1100, 275]);
assert.strictEqual(C.splitBill(1000, 0, 10), null);
const f = C.fuelCost(450, 6, 45, 3, true);
assert.deepStrictEqual([f.km, f.liters, f.cost, f.perPerson], [900, 54, 2430, 810]);
assert.strictEqual(C.fuelCost(100, 5, null, 1, false).cost, null);
assert.strictEqual(C.fuelCost(100, 5, null, 1, false).liters, 5);

console.log("OK");
