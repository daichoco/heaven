(() => {
  const key = "weekendNotifyTargets";
  const stars = new Set((() => {
    try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch (e) { return []; }
  })());
  const box = document.createElement("div");
  box.className = "notify-box";
  box.innerHTML = '<span id="notifyCount"></span> <button type="button" id="notifyCopy">通知対象をコピー</button> <span id="notifyMsg" role="status"></span>';
  const anchor = document.getElementById("toolbar") || document.querySelector("h1");
  anchor.insertAdjacentElement(anchor.id === "toolbar" ? "beforebegin" : "afterend", box);
  const count = box.querySelector("#notifyCount");
  function paint() {
    count.textContent = `★通知対象 ${stars.size}名`;
    document.querySelectorAll(".notify-star").forEach(button => {
      const on = stars.has(button.dataset.url);
      button.textContent = on ? "★" : "☆";
      button.setAttribute("aria-pressed", String(on));
    });
  }
  function decorate() {
    document.querySelectorAll("td.shop-cell > a[href^='http']").forEach(link => {
      if (link.previousElementSibling?.classList.contains("notify-star")) return;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "notify-star";
      button.dataset.url = link.href;
      button.title = "土日出勤の通知対象にする";
      link.before(button);
    });
    paint();
  }
  document.addEventListener("click", event => {
    const button = event.target.closest(".notify-star");
    if (!button) return;
    if (!stars.delete(button.dataset.url)) stars.add(button.dataset.url);
    try { localStorage.setItem(key, JSON.stringify([...stars])); } catch (e) {}
    paint();
  });
  box.querySelector("#notifyCopy").addEventListener("click", async () => {
    const text = [...stars].join("\n");
    try {
      await navigator.clipboard.writeText(text);
      box.querySelector("#notifyMsg").textContent = "コピーしました";
    } catch (e) {
      window.prompt("通知対象のURL", text);
    }
  });
  decorate();
})();
