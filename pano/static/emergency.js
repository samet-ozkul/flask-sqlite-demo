// 🆘 Acil Durum Kartı: acil kişi satırını Kişiler'den doldurma ve cüzdan kartını yazdırma.
// JS kapalıyken de form çalışır (seçim kutusu ve Yazdır düğmesi gizli kalır; tarayıcının yazdır menüsü kullanılır).
(() => {
  const data = document.getElementById("em-picks");
  const picks = data ? JSON.parse(data.textContent) : [];
  if (picks.length) {
    document.querySelectorAll(".em-pick").forEach((sel) => {
      picks.forEach((p, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = (p.relation ? `${p.name} (${p.relation})` : p.name) + ` · ${p.phone}`;
        sel.append(opt);
      });
      sel.classList.remove("hidden");
      sel.addEventListener("change", () => {
        const p = picks[Number(sel.value)];
        if (!sel.value || !p) return;
        const row = sel.closest("li");
        row.querySelector('[name="contact_name"]').value = p.name;
        row.querySelector('[name="contact_phone"]').value = p.phone;
        const rel = row.querySelector('[name="contact_relation"]');
        if (!rel.value && p.relation) rel.value = p.relation;   // "Eşi" gibi yazılmış yakınlık korunur
        sel.value = "";
      });
    });
  }

  const print = document.getElementById("em-print");
  if (print) {
    print.classList.remove("hidden");
    print.addEventListener("click", () => window.print());
  }
})();
