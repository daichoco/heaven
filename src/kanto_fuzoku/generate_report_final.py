"""関東専用HTMLを生成する。30歳以下のみ表示。--offline は保存済みデータのみを使う。"""
import os
import sys
from pathlib import Path

import requests

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from html_format import write_html
from shop_prices import refresh_saved_prices
from tokai_fuzoku import generate_report_final as shared
from tokai_fuzoku.html_render import generate_html, report_template
from kanto_fuzoku.collect_fuzoku_kanto import login, validate_article
from kanto_fuzoku.report_store import REPORTS_JSONL, parse_report_details

INPUT_JSONL = REPORTS_JSONL
OUTPUT_HTML = ROOT_DIR / "docs" / "kanto_final_report.html"
TITLE = "関東風俗レポート"


def main(argv=None):
    args = shared.parse_args(
        sys.argv[1:] if argv is None else argv, description="関東風俗レポートHTMLを生成する（30歳以下のみ表示）",
    )
    if args.refresh_prices:
        write_html(OUTPUT_HTML, refresh_saved_prices(
            OUTPUT_HTML.read_text(encoding="utf-8"), report_template(TITLE), "function applySort()",
        ))
        print(f"関東の保存HTMLの料金を更新しました: {OUTPUT_HTML}")
        return
    frame = shared.load_input_data(INPUT_JSONL)
    if args.offline:
        frame = shared.prepare_offline_data(
            frame, output_path=OUTPUT_HTML, fill_command="collect_fuzoku_kanto.py --fill-details",
        )
    else:
        password = os.environ.get("FUZOKU_TOKAI_PASSWORD")
        if not password:
            raise RuntimeError("関東の認証にも FUZOKU_TOKAI_PASSWORD が必要です（ネットなしは --offline）")
        with requests.Session() as session:
            login(session, password)
            frame = shared.filter_unavailable_girl_urls(frame, session)
            frame = shared.enrich_report_data(
                frame, session, parse_details=parse_report_details, validate_article=validate_article,
            )
    OUTPUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    write_html(OUTPUT_HTML, generate_html(frame, title=TITLE))
    print(f"生成しました: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
