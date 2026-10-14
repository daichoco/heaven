(() => {
  const interactiveSelector =
    "a[href], button, input, select, textarea, [contenteditable]:not([contenteditable='false'])";

  function findScrollContainer(element) {
    for (let parent = element.parentElement; parent; parent = parent.parentElement) {
      if (
        /^(auto|scroll|overlay)$/.test(getComputedStyle(parent).overflowY) &&
        parent.scrollHeight > parent.clientHeight
      ) {
        return parent;
      }
    }
    return document.scrollingElement;
  }

  function stickyTop(scrollContainer) {
    let bottom = scrollContainer === document.scrollingElement
      ? 0
      : scrollContainer.getBoundingClientRect().top;
    const candidates = scrollContainer === document.scrollingElement
      ? document.querySelectorAll(".sticky-toolbar")
      : scrollContainer.querySelectorAll("thead th");

    candidates.forEach(element => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      const containerTop = scrollContainer === document.scrollingElement
        ? 0
        : scrollContainer.getBoundingClientRect().top;
      if (
        (style.position === "sticky" || style.position === "fixed") &&
        rect.top <= containerTop + 1 &&
        rect.bottom > containerTop
      ) {
        bottom = Math.max(bottom, rect.bottom);
      }
    });
    return bottom;
  }

  function alignToTop(element) {
    const scrollContainer = findScrollContainer(element);
    const top = element.getBoundingClientRect().top;
    const offset = top - stickyTop(scrollContainer);
    if (Math.abs(offset) < 1) return;

    if (scrollContainer === document.scrollingElement) {
      window.scrollBy(0, offset);
    } else {
      scrollContainer.scrollTop += offset;
    }
  }

  function nextFrame() {
    return new Promise(resolve => requestAnimationFrame(resolve));
  }

  function detailsPeers(details) {
    if (details.matches(".report-entry")) {
      return [...details.parentElement.querySelectorAll(":scope > details.report-entry")];
    }
    const list = details.closest(".report-list");
    if (list && details.matches(".review-body")) {
      return [...list.querySelectorAll("details.review-body")];
    }
    return [...details.parentElement.querySelectorAll(":scope > details")];
  }

  function closePersonRowsExcept(openRow) {
    const tableBody = openRow.closest("tbody");
    syncPersonRows(tableBody, openRow);
  }

  function syncPersonRows(tableBody, openRow = null) {
    if (!tableBody) return;

    tableBody.querySelectorAll("tr.person-row").forEach(row => {
      const detailRow = row.nextElementSibling;
      if (!detailRow?.classList.contains("detail-row")) return;
      const open = row === openRow;
      row.classList.toggle("open", open);
      row.setAttribute("aria-expanded", String(open));
      detailRow.hidden = !open;
    });
  }

  function syncReviewButtons(tableBody) {
    if (!tableBody) return;
    tableBody.querySelectorAll('button[onclick^="toggleReviewRow("]').forEach(button => {
      const match = button.getAttribute("onclick").match(/^toggleReviewRow\(['"]([^'"]+)['"]\)$/);
      const row = match && document.getElementById(match[1]);
      if (!row) return;
      button.setAttribute("aria-controls", row.id);
      button.setAttribute("aria-expanded", String(getComputedStyle(row).display !== "none"));
    });
  }

  function closeReviewRowsExcept(openReviewRow) {
    const tableBody = openReviewRow.closest("tbody");
    if (!tableBody) return;
    tableBody.querySelectorAll("tr.review-row").forEach(row => {
      if (row === openReviewRow) return;
      row.style.display = "none";
    });
  }

  function preparePersonRows() {
    document.querySelectorAll("tr.person-row").forEach((row, index) => {
      const detailRow = row.nextElementSibling;
      if (!detailRow?.classList.contains("detail-row")) return;
      if (!detailRow.id) detailRow.id = `person-detail-${index + 1}`;
      row.tabIndex = 0;
      row.setAttribute("aria-controls", detailRow.id);
      row.setAttribute("aria-expanded", String(row.classList.contains("open")));
    });
    document.querySelectorAll('button[onclick^="toggleReviewRow("]').forEach(button => {
      const match = button.getAttribute("onclick").match(/^toggleReviewRow\(['"]([^'"]+)['"]\)$/);
      if (match) button.setAttribute("aria-controls", match[1]);
    });
  }

  preparePersonRows();

  document.addEventListener("keydown", event => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (!(event.target instanceof Element)) return;
    const row = event.target.closest("tr.person-row");
    if (!row || event.target.closest(interactiveSelector)) return;
    event.preventDefault();
    row.click();
  });

  document.addEventListener("click", event => {
    if (!(event.target instanceof Element)) return;

    const summary = event.target.closest("summary");
    const details = summary?.parentElement;
    if (summary && details?.tagName === "DETAILS" && !event.target.closest(interactiveSelector)) {
      const wasOpen = details.open;
      if (wasOpen) return;
      const peers = detailsPeers(details);
      requestAnimationFrame(async () => {
        if (!details.isConnected || !details.open) return;
        peers.forEach(peer => {
          if (peer !== details) peer.open = false;
        });
        await nextFrame();
        if (details.open) alignToTop(summary);
      });
      return;
    }

    const personRow = event.target.closest("tr.person-row");
    if (personRow && !event.target.closest(interactiveSelector)) {
      const wasOpen = personRow.classList.contains("open");
      if (wasOpen) {
        requestAnimationFrame(() => syncPersonRows(personRow.closest("tbody")));
        return;
      }
      requestAnimationFrame(async () => {
        if (!personRow.isConnected || !personRow.classList.contains("open")) return;
        closePersonRowsExcept(personRow);
        await nextFrame();
        if (personRow.classList.contains("open")) alignToTop(personRow);
      });
      return;
    }

    const reviewButton = event.target.closest('button[onclick^="toggleReviewRow("]');
    if (!reviewButton) return;
    const match = reviewButton.getAttribute("onclick").match(/^toggleReviewRow\(['"]([^'"]+)['"]\)$/);
    const reviewRow = match && document.getElementById(match[1]);
    if (!reviewRow) return;

    const wasVisible = getComputedStyle(reviewRow).display !== "none";
    if (wasVisible) {
      requestAnimationFrame(() => syncReviewButtons(reviewButton.closest("tbody")));
      return;
    }
    const headingRow = reviewButton.closest("tr");
    requestAnimationFrame(async () => {
      if (!reviewRow.isConnected || getComputedStyle(reviewRow).display === "none") return;
      closeReviewRowsExcept(reviewRow);
      syncReviewButtons(reviewButton.closest("tbody"));
      await nextFrame();
      if (getComputedStyle(reviewRow).display !== "none" && headingRow?.isConnected) {
        alignToTop(headingRow);
      }
    });
  }, true);
})();
