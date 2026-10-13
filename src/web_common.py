"""docs/*.html で共通に使う head 要素・ナビ・「先頭へ」ボタン。

スタイルは静的ファイル docs/common.css にまとめ、各ページはそれを読み込んだうえで
ページ固有の <style> を後に置いて上書きする。
"""

HEAD_COMMON = (
    '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
    '<meta name="color-scheme" content="light dark">\n'
    '<link rel="stylesheet" href="common.css">'
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
