(() => {
  document.querySelectorAll(".profile-photo img").forEach(image => {
    const showError = () => {
      image.hidden = true;
      image.nextElementSibling.hidden = false;
    };
    image.addEventListener("error", showError);
    if (image.complete && image.naturalWidth === 0) showError();
  });
  const table = document.querySelector("table[data-dates]");
  const select = document.getElementById("scheduleFilter");
  const ageSelect = document.getElementById("ageFilter");
  const daySelect = document.getElementById("daySelect");
  const dates = table.dataset.dates.split(",");
  const rows = [...table.querySelectorAll(".person-row")];
  const reviewRows = [...table.querySelectorAll(".review-row")];
  const reviewButtons = [...table.querySelectorAll(".review-toggle")];
  function closeReviews() {
    reviewRows.forEach(row => row.hidden = true);
    reviewButtons.forEach(button => button.setAttribute("aria-expanded", "false"));
  }
  reviewButtons.forEach(button => button.addEventListener("click", () => {
    const row = document.getElementById(button.getAttribute("aria-controls"));
    const opening = row.hidden;
    const top = button.getBoundingClientRect().top;
    closeReviews();
    row.hidden = !opening;
    button.setAttribute("aria-expanded", String(opening));
    // Closing a review above the clicked button must not move that button.
    window.scrollBy(0, button.getBoundingClientRect().top - top);
  }));
  const todayJst = () => {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    const value = type => parts.find(part => part.type === type).value;
    return `${value("year")}-${value("month")}-${value("day")}`;
  };
  function applyFilter() {
    const mode = select.value;
    const today = todayJst();
    const targets = dates.filter(day => {
      if (mode === "today") return day === today;
      if (mode === "date") return day === daySelect.value;
      if (mode === "weekend") return [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay());
      return true;
    });
    let shown = 0;
    rows.forEach(row => {
      const working = row.dataset.working.split(",");
      const ages = row.dataset.ages.split(",").filter(Boolean).map(Number);
      const ageMatches = !ageSelect.value || (ageSelect.value === "unknown"
        ? ages.length === 0
        : ages.some(age => ageSelect.value === "under30" ? age < 30 : age >= 30));
      row.hidden = !ageMatches || (mode !== "all" && !targets.some(day => working.includes(day)));
      if (!row.hidden) shown += 1;
      if (row.hidden) row.querySelectorAll(".review-toggle").forEach(button => {
        document.getElementById(button.getAttribute("aria-controls")).hidden = true;
        button.setAttribute("aria-expanded", "false");
      });
    });
    table.querySelectorAll("[data-day]").forEach(cell => {
      cell.hidden = !targets.includes(dates[Number(cell.dataset.day)]);
    });
    document.getElementById("dayCount").textContent =
      `${select.selectedOptions[0].textContent}${mode === "date" ? ` ${daySelect.value}` : ""}: 表示 ${shown}名 / 母集団 ${rows.length}名（順位・S/Aは固定）`;
    daySelect.classList.toggle("inactive", mode !== "date");
    document.getElementById("noResults").hidden = shown > 0;
    document.getElementById("staleNotice").hidden = dates.includes(today);
  }
  function changeFilter() {
    closeReviews();
    applyFilter();
  }
  select.addEventListener("change", changeFilter);
  ageSelect.addEventListener("change", changeFilter);
  daySelect.addEventListener("change", () => {
    select.value = "date";
    changeFilter();
  });
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) applyFilter();
  });
  setInterval(applyFilter, 60000);
  applyFilter();
})();
