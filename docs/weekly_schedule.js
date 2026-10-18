(() => {
  const table = document.querySelector("table[data-dates]");
  const select = document.getElementById("scheduleFilter");
  const dates = table.dataset.dates.split(",");
  const rows = [...table.querySelectorAll(".person-row")];
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
      if (mode === "weekend") return [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay());
      return true;
    });
    let shown = 0;
    rows.forEach(row => {
      const working = row.dataset.working.split(",");
      row.hidden = mode !== "all" && !targets.some(day => working.includes(day));
      if (!row.hidden) shown += 1;
    });
    table.querySelectorAll("[data-day]").forEach(cell => {
      cell.hidden = !targets.includes(dates[Number(cell.dataset.day)]);
    });
    document.getElementById("dayCount").textContent =
      `${select.selectedOptions[0].textContent}: 表示 ${shown}名 / 母集団 ${rows.length}名（順位・S/Aは固定）`;
    document.getElementById("noResults").hidden = shown > 0;
    document.getElementById("staleNotice").hidden = dates.includes(today);
  }
  select.addEventListener("change", applyFilter);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) applyFilter();
  });
  setInterval(applyFilter, 60000);
  applyFilter();
})();
