(() => {
  function values() {
    const min = document.getElementById("shopPriceMin");
    const max = document.getElementById("shopPriceMax");
    const mode = document.getElementById("shopPriceMode");
    const lower = min?.value ? Number(min.value) : null;
    const upper = max?.value ? Number(max.value) : null;
    const unknown = mode?.value === "unknown";
    const invalid = !unknown && ((min && !min.validity.valid) || (max && !max.validity.valid)
      || (lower !== null && upper !== null && lower > upper));
    return {lower, upper, unknown, invalid};
  }
  window.shopPriceMatches = row => {
    const {lower, upper, unknown, invalid} = values();
    if (invalid) return false;
    const raw = row.dataset.shopPrice;
    const amount = raw ? Number(raw) : NaN;
    const known = row.dataset.shopPriceStatus === "ok" && Number.isFinite(amount) && amount > 0;
    if (unknown) return !known;
    if (lower === null && upper === null) return true;
    return known && (lower === null || amount >= lower) && (upper === null || amount <= upper);
  };
  window.resetShopPrices = () => {
    ["shopPriceMin", "shopPriceMax", "shopPriceMode"].forEach(id => {
      const control = document.getElementById(id);
      if (control) { control.value = ""; control.disabled = false; }
    });
    const error = document.getElementById("shopPriceError");
    if (error) error.hidden = true;
  };
  document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("shopPriceFilters");
    if (!root) return;
    const update = () => {
      const {unknown, invalid} = values();
      const error = document.getElementById("shopPriceError");
      error.hidden = !invalid;
      error.textContent = invalid ? "料金は0以上の数値で、下限が上限以下となるよう指定してください。" : "";
      root.querySelectorAll("input").forEach(input => input.disabled = unknown);
      if (typeof window.applyCombinedFilter === "function") window.applyCombinedFilter();
      else if (typeof window.applyFilters === "function") window.applyFilters();
      else if (typeof window.filterPeople === "function") window.filterPeople();
      else document.dispatchEvent(new Event("shop-prices-change"));
    };
    root.querySelectorAll("input, select").forEach(control => {
      control.addEventListener("input", update);
      control.addEventListener("change", update);
    });
  });
})();
