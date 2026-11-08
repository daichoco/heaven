"""docs/*.html で共通に使う head 要素・ナビ・「先頭へ」ボタン。

共通スタイルは docs/common.css、開閉時の位置補正は docs/common.js に置き、
各ページはこれらを読み込んだうえでページ固有の <style> を適用する。
"""

HEAD_COMMON = (
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    '<meta name="color-scheme" content="light dark">\n'
    '<link rel="stylesheet" href="common.css">\n'
    '<script src="common.js" defer></script>'
)

BACK_NAV = '<nav class="page-nav" aria-label="ページ移動"><a href="index.html">← 一覧</a></nav>'

# スマホでは .sticky-toolbar 内の .filter-bar を折りたたみ、#filterToggle で開閉する。
# ページ側の applyFilters から updateFilterToggle() を呼ぶと、指定中の条件数をボタンに表示する。
FILTER_TOGGLE_SCRIPT = (
    "<script>\n"
    "function updateFilterToggle() {\n"
    "  const toggle = document.getElementById('filterToggle');\n"
    "  if (!toggle) return;\n"
    "  const active = [...document.querySelectorAll('#filterBar select:not([data-sort])')]\n"
    "    .filter(select => select.selectedIndex > 0).length;\n"
    "  toggle.textContent = active ? `絞り込み (${active})` : '絞り込み';\n"
    "}\n"
    "document.getElementById('filterToggle').addEventListener('click', event => {\n"
    "  const open = document.getElementById('toolbar').classList.toggle('filters-open');\n"
    "  event.currentTarget.setAttribute('aria-expanded', String(open));\n"
    "});\n"
    "</script>"
)

BACK_TO_TOP = (
    '<button type="button" class="close-open" id="closeOpen" hidden>▲ 開いた本文を閉じる</button>\n'
    "<style>.close-open { position: fixed; right: 12px; bottom: 64px; z-index: 30; min-height: 44px; "
    "padding: 8px 14px; border-radius: 22px; border: 1px solid var(--border, #888); "
    "background: var(--chip, #eee); color: var(--fg, #000); box-shadow: 0 2px 6px rgba(0,0,0,.3); cursor: pointer; }</style>\n"
    "<script>\n"
    "(() => {\n"
    "  const button = document.getElementById('closeOpen');\n"
    "  const selector = 'details.report-entry[open], .review-toggle[aria-expanded=\"true\"]';\n"
    "  const open = () => [...document.querySelectorAll(selector)];\n"
    "  const update = () => { button.hidden = open().length === 0; };\n"
    "  button.addEventListener('click', () => {\n"
    "    const items = open();\n"
    "    const anchor = i => i.tagName === 'DETAILS' ? i.querySelector('summary') : i;\n"
    "    const middle = innerHeight / 2;\n"
    "    const current = items.find(i => { const r = i.getBoundingClientRect(); return r.top <= middle && r.bottom >= middle; });\n"
    "    const target = anchor(current || items[0]);\n"
    "    items.forEach(i => i.tagName === 'DETAILS' ? (i.open = false) : i.click());\n"
    "    target?.scrollIntoView({ block: 'center' });\n"
    "    update();\n"
    "  });\n"
    "  new MutationObserver(update).observe(document.body, {\n"
    "    subtree: true, childList: true, attributes: true, attributeFilter: ['open', 'aria-expanded'],\n"
    "  });\n"
    "  update();\n"
    "})();\n"
    "</script>\n"
    '<button type="button" class="back-to-top" id="backToTop" hidden aria-label="ページの先頭へ">↑ 先頭へ</button>\n'
    "<script>\n"
    "(() => {\n"
    "  const button = document.getElementById('backToTop');\n"
    "  const update = () => { button.hidden = window.scrollY < 600; };\n"
    "  window.addEventListener('scroll', update, { passive: true });\n"
    "  button.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));\n"
    "  update();\n"
    "})();\n"
    "</script>"
)
