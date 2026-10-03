// 🔁 Ödünç formu: Ev Envanteri'nden eşya ya da Kişiler'den kişi seçilince ad (ve telefon) alanını doldurur.
// JS yoksa da çalışır: ad boş bırakılırsa sunucu seçilen kaydın adını (ve telefonunu) kullanır.
document.querySelectorAll("form[data-loan-form]").forEach((form) => {
  form.addEventListener("change", (e) => {
    const select = e.target.closest("select[data-loan-pick]");
    const opt = select && select.selectedOptions[0];
    if (!opt || !opt.value) return;
    if (select.dataset.loanPick === "item") {
      form.elements.item_name.value = opt.dataset.name;
    } else {
      form.elements.person_name.value = opt.dataset.name;
      if (opt.dataset.phone) form.elements.phone.value = opt.dataset.phone;
    }
  });
});
