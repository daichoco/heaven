(() => {
  const bands = {
    "0-10000": [0, 10000],
    "10000-15000": [10000, 15000],
    "15000-20000": [15000, 20000],
    "20000-25000": [20000, 25000],
    "25000-30000": [25000, 30000],
    "30000": [30000, Infinity],
  };
  window.shopPriceMatches = row => {
    const band = document.getElementById("shopPriceBand")?.value || "";
    const raw = row.dataset.shopPrice;
    const amount = raw ? Number(raw) : NaN;
    const known = row.dataset.shopPriceStatus === "ok" && Number.isFinite(amount) && amount > 0;
    if (band === "unknown") return !known;
    if (!band) return true;
    const bounds = bands[band];
    return known && !!bounds && amount >= bounds[0] && amount < bounds[1];
  };
  window.resetShopPrices = () => {
    const control = document.getElementById("shopPriceBand");
    if (control) control.value = "";
  };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("shopPriceFilters");
    if (!root) return;
    const update = () => {
      if (typeof window.applyCombinedFilter === "function") window.applyCombinedFilter();
      else if (typeof window.applyFilters === "function") window.applyFilters();
      else if (typeof window.filterPeople === "function") window.filterPeople();
      else document.dispatchEvent(new Event("shop-prices-change"));
    };
    root.querySelectorAll("select").forEach(control => {
      control.addEventListener("change", update);
    });
  });
})();
