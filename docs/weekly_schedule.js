(() => {
  const table = document.querySelector("table[data-dates]");
  const tbody = table.tBodies[0];
  const index = JSON.parse(document.getElementById("weeklyIndex").textContent);
  const select = document.getElementById("scheduleFilter");
  const ageSelect = document.getElementById("ageFilter");
  const daySelect = document.getElementById("daySelect");
  const search = document.getElementById("weeklySearch");
  const status = document.getElementById("weeklyLoadStatus");
  const retry = document.getElementById("weeklyRetry");
  const previous = document.getElementById("weeklyPrevious");
  const next = document.getElementById("weeklyNext");
  const dates = table.dataset.dates.split(",");
  const rows = new Map([...tbody.rows].map(row => [row.dataset.personId, row.outerHTML]));
  const requests = new Map();
  const fileMode = location.protocol === "file:";
  let pageNumber = 0;
  let revision = 0;
  let openReview = null;
  let matched = [];
  document.getElementById("fileNotice").hidden = !fileMode;

  function load(url) {
    if (fileMode) return Promise.reject(new Error("ファイル直開きでは取得できません。HTTPまたはGitHub Pagesで閲覧してください。"));
    if (!requests.has(url)) {
      const promise = fetch(url).then(response => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      }).catch(error => {
        requests.delete(url);
        throw error;
      });
      requests.set(url, promise);
    }
    return requests.get(url);
  }
  function closeReviews() {
    openReview = null;
    tbody.querySelectorAll(".review-row").forEach(row => row.remove());
    tbody.querySelectorAll(".review-toggle").forEach(button => button.setAttribute("aria-expanded", "false"));
  }
  function stableButton(button, action) {
    const top = button.getBoundingClientRect().top;
    action();
    window.scrollBy(0, button.getBoundingClientRect().top - top);
  }
  async function showReview(button) {
    const person = button.closest(".person-row");
    const source = button.dataset.source;
    const id = person.dataset.personId;
    const closing = openReview?.id === id && openReview?.source === source;
    const token = {id, source};
    let row;
    stableButton(button, () => {
      closeReviews();
      if (closing) return;
      openReview = token;
      button.setAttribute("aria-expanded", "true");
      row = document.createElement("tr");
      row.id = button.getAttribute("aria-controls");
      row.className = `review-row review-source-${source}`;
      const cell = row.insertCell();
      cell.colSpan = dates.length + 1;
      cell.setAttribute("aria-busy", "true");
      cell.innerHTML = '<p role="status">本文を読み込んでいます…</p>';
      person.insertAdjacentElement("afterend", row);
    });
    if (closing) return;
    try {
      const payload = await load(person.dataset.bodyUrl);
      if (openReview !== token || !row.isConnected) return;
      if (payload.id !== id || typeof payload.sources?.[source] !== "string") {
        throw new Error("人物・評価元と本文データが一致しません。ページを再読み込みしてください。");
      }
      row.cells[0].innerHTML = payload.sources[source] || '<p role="status">保存本文は空です。</p>';
    } catch (error) {
      requests.delete(person.dataset.bodyUrl);
      if (openReview !== token || !row.isConnected) return;
      const message = document.createElement("p");
      message.setAttribute("role", "alert");
      message.textContent = `本文を取得できませんでした: ${error.message}`;
      const again = document.createElement("button");
      again.type = "button";
      again.textContent = "本文を再試行";
      again.addEventListener("click", () => {
        closeReviews();
        showReview(button);
      });
      row.cells[0].replaceChildren(message, again);
    } finally {
      if (row.isConnected) row.cells[0].setAttribute("aria-busy", "false");
    }
  }
  tbody.addEventListener("click", event => {
    const button = event.target.closest(".review-toggle");
    if (button) showReview(button);
  });
  tbody.addEventListener("error", event => {
    const image = event.target;
    if (image.matches(".profile-photo img")) {
      image.hidden = true;
      image.nextElementSibling.hidden = false;
    }
  }, true);
  const todayJst = () => {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(new Date());
    const value = type => parts.find(part => part.type === type).value;
    return `${value("year")}-${value("month")}-${value("day")}`;
  };
  function targets() {
    return dates.filter(day => {
      if (select.value === "today") return day === todayJst();
      if (select.value === "date") return day === daySelect.value;
      if (select.value === "weekend") return [0, 6].includes(new Date(`${day}T00:00:00Z`).getUTCDay());
      return true;
    });
  }
  function matches(person, days) {
    return (!ageSelect.value || (ageSelect.value === "unknown" ? person.ages.length === 0 :
      person.ages.some(age => ageSelect.value === "under30" ? age < 30 : age >= 30))) &&
      (typeof shopPriceMatches !== "function" || shopPriceMatches({dataset: {
        shopPrice: String(person.price), shopPriceStatus: person.priceStatus,
      }})) && (!search.value.trim() || person.search.includes(search.value.trim().toLocaleLowerCase())) &&
      (select.value === "all" || days.some(day => person.working.includes(day)));
  }
  async function render(scroll = false) {
    const token = ++revision;
    closeReviews();
    const days = targets();
    matched = index.people.filter(person => matches(person, days));
    const pages = Math.max(1, Math.ceil(matched.length / index.pageSize));
    pageNumber = Math.min(pageNumber, pages - 1);
    const selection = matched.slice(pageNumber * index.pageSize, (pageNumber + 1) * index.pageSize);
    document.getElementById("dayCount").textContent =
      `${select.selectedOptions[0].textContent}${select.value === "date" ? ` ${daySelect.value}` : ""}: 表示 ${matched.length}名 / 母集団 ${index.people.length}名（順位・S/Aは固定）`;
    document.getElementById("weeklyPage").textContent = `${pageNumber + 1} / ${pages}ページ（このページ ${selection.length}名 / 該当 ${matched.length}名）`;
    previous.disabled = pageNumber === 0;
    next.disabled = (pageNumber + 1) * index.pageSize >= matched.length;
    daySelect.classList.toggle("inactive", select.value !== "date");
    document.getElementById("noResults").hidden = matched.length > 0;
    document.getElementById("staleNotice").hidden = dates.includes(todayJst());
    retry.hidden = true;
    table.setAttribute("aria-busy", "true");
    // Remove the previous page immediately; it must never masquerade as the new filter result.
    tbody.replaceChildren();
    status.textContent = selection.length ? "一覧を読み込んでいます…" : "";
    try {
      const urls = [...new Set(selection.filter(person => !rows.has(person.id)).map(person => person.url))];
      await Promise.all(urls.map(async url => {
        const payload = await load(url);
        if (!payload.rows || typeof payload.rows !== "object") throw new Error("一覧データの形式が不正です");
        Object.entries(payload.rows).forEach(([id, html]) => {
          if (typeof html === "string") rows.set(id, html);
        });
      }));
      if (token !== revision) return;
      const container = document.createElement("tbody");
      for (const person of selection) {
        const html = rows.get(person.id);
        if (!html) throw new Error("該当する人物の一覧データがありません");
        container.insertAdjacentHTML("beforeend", html);
        if (container.lastElementChild.dataset.personId !== person.id) throw new Error("一覧の人物IDが一致しません");
      }
      tbody.replaceChildren(...container.children);
      table.querySelectorAll("[data-day]").forEach(cell => {
        cell.hidden = !days.includes(dates[Number(cell.dataset.day)]);
      });
      status.textContent = "";
      if (scroll) table.closest(".table-scroll").scrollIntoView({block: "start"});
    } catch (error) {
      if (token !== revision) return;
      if (!fileMode) {
        selection.forEach(person => {
          rows.delete(person.id);
          requests.delete(person.url);
        });
      }
      // file:// can still show matching rows already embedded in the initial HTML.
      if (fileMode) {
        selection.filter(person => rows.has(person.id)).forEach(person =>
          tbody.insertAdjacentHTML("beforeend", rows.get(person.id)));
      }
      status.textContent = `一覧を取得できませんでした: ${error.message}`;
      retry.hidden = fileMode;
    } finally {
      if (token === revision) table.setAttribute("aria-busy", "false");
    }
  }
  function changeFilter() {
    pageNumber = 0;
    render();
  }
  select.addEventListener("change", changeFilter);
  ageSelect.addEventListener("change", changeFilter);
  search.addEventListener("input", changeFilter);
  document.addEventListener("shop-prices-change", changeFilter);
  daySelect.addEventListener("change", () => { select.value = "date"; changeFilter(); });
  previous.addEventListener("click", () => { pageNumber -= 1; render(true); });
  next.addEventListener("click", () => { pageNumber += 1; render(true); });
  retry.addEventListener("click", () => render());
  document.getElementById("weeklyReset").addEventListener("click", () => {
    select.value = "working";
    ageSelect.value = "";
    daySelect.selectedIndex = 0;
    search.value = "";
    resetShopPrices();
    changeFilter();
  });
  let lastToday = todayJst();
  function updateDay() {
    if (todayJst() !== lastToday) {
      lastToday = todayJst();
      changeFilter();
    }
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) updateDay(); });
  setInterval(updateDay, 60000);
  render();
})();
