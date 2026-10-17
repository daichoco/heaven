# HTML formatting

The published HTML in this directory is generated. From the repository root,
install Node.js (22 LTS or newer) and the Python requirements, then install the
locked formatter dependency:

```powershell
py -3 -m pip install -r requirements.txt
npm ci
npm run format:html
npm run check:html
```

On systems without the Windows Python launcher, use `python` instead of `py -3`.
`format:html` recursively formats every `.html` file under `docs`; `check:html`
does not write files and exits nonzero if any file needs formatting.
Neither command collects data or makes network requests.

## Generated output

`src/html_format.py` calls the same `scripts/format-html.cjs` formatter used by
the npm commands. All current HTML writers use `write_html(path, source)`:

| Output | Generator / writer |
| --- | --- |
| `index.html` | `src/cityheaven/cityheaven_month_schedule.py` |
| `calendar_report.html` | `src/integrations/report_left_merge.py` |
| `ns_senmon_report.html` | `src/ns_senmon/generate_ns_senmon_report.py` |
| `tokai_final_report.html` | `src/tokai_fuzoku/generate_report_final.py` |
| `okinilove_remove_unknown.html` | `src/okinilove/render_people_summary.py`, including intermediate updates in `src/okinilove/scrape_okinilove.py` |
| `reservation_difficulty.html` | `src/generate_booking_difficulty_report.py` |

`reservation_difficulty.html` is generated offline from the NS専門,
オキニラブ, and 東海風俗 JSONL reports. It lists people whose saved report
text explicitly describes booking difficulty and includes the matching excerpt
and source article link. Regenerate it with:

```powershell
py -3 src\generate_booking_difficulty_report.py
```

The extraction is a keyword-based snapshot, not a check of current schedules.
Only identical normalized profile URLs are merged; records without profile
URLs remain separate so namesakes are not combined by guesswork.

Run generators as before, with Node.js on `PATH` and `npm ci` already completed;
formatting is automatic at the write boundary. No extra formatting step is
required after generation. New HTML writers should also use `write_html`.
The string/soup builders remain unchanged. Formatting failures raise an error
before the HTML file is opened for writing; there is no unformatted fallback.
Generators themselves may collect data; do not run them merely to format HTML.

The pinned `js-beautify` version and `.jsbeautifyrc` are shared by both paths.
HTML uses two-space indentation and LF line endings (also enforced for HTML by
`.gitattributes`, including Windows checkouts). Text is not wrapped, and
attribute values are not rewritten. Contents of `script`, `style`, `pre`,
`textarea`, and `p` are left untouched: paragraphs include review text displayed
with CSS `white-space: pre-wrap`, whose newlines and spaces are meaningful.
Consequently, embedded JS/CSS and some paragraphs intentionally retain long
lines. Edit templates/renderers for persistent content changes, not only the
generated pages. JSON review bodies and shared CSS/JS are outside the formatting
scope.

Offline regression checks (no report collection):

```powershell
py -3 -m unittest discover -s tests
npm run check:html
```

## Tokai report sorting and person aggregation

`tokai_final_report.html` groups reports by normalized profile URL. It does not
guess that different URLs (or reports with no profile URL) represent the same
person. Each person's card shows the latest available name/shop/profile link
and image, report count, latest date, and averages for total, play, face, style,
and panel difference. Each average shows its own `n`; missing/invalid values
are excluded, zero is valid, and a score with no values is shown as 未評価.
Opening a person shows every original report, newest first, with its original
date, link, scores, and comments. The JSONL remains one row per article.

おすすめ順 compares each person's total, play, face, then style average in
descending order, without weighting or statistical correction. Each score can
also be selected individually, as can the person's newest report date. Missing
averages come after valid averages (including zero). Ties use newest report
date, then original input order; missing dates come last. Repeat status and
panel difference never affect rank.

Search and all existing filters match individual reports: a person is shown
when at least one single report satisfies every active condition, and only
matching reports remain visible inside that person's card. Thus conditions
cannot be combined across different reports. Averages and sort order always
use the person's full report history, including while filtered. The count
distinguishes visible people from matching reports and shows both page totals.
Sorting remains available when the mobile filter panel is collapsed; changing
the order preserves expanded people and filter conditions, and resetting
conditions preserves the selected order.

The Tokai page starts with its detailed filter panel expanded on mobile as well
as desktop. The mobile 絞り込み button still closes and reopens the panel, with
`aria-expanded` reflecting its state. This initial state is specific to Tokai;
other pages retain their existing mobile collapse behavior. The initial people,
counts, and recommended order do not require an empty search.

Offline regression tests execute the actual JavaScript from both the template
and published HTML using Node.js, including initial sorting, changing modes,
missing/zero averages, ties, same-report filter matching, reset, and expanded
person state:

```powershell
py -3 -m unittest discover -s tests -p test_tokai_sorting.py
```

Real-browser checks cover both generated and published pages in Chromium and
WebKit, including mobile initial visibility, closing/reopening, empty searches,
shop filtering, no results, reset, desktop sorting and expanded-state retention,
scroll/reload/history, and blocked or delayed shared JavaScript. Requests are
intercepted locally; profile images and remote pages are never downloaded.
Playwright WebKit is not an actual iPhone running Brave, so it cannot establish
device-specific rendering behavior.

```powershell
py -3 -m playwright install chromium webkit
py -3 -m unittest discover -s tests -p test_tokai_browser.py
```

### Applying renderer changes alongside newer data

Keep the latest `data\tokai_fuzoku_reports.jsonl`; do not replace it with data
from an older feature branch. Apply renderer/test/documentation changes first,
then regenerate the HTML from that latest JSONL instead of overwriting a newer
HTML with the feature branch's generated file:

```powershell
py -3 src\tokai_fuzoku\generate_report_final.py --offline
```

`--offline` uses stored details and the latest recorded profile availability,
without login, collection, profile checks, or image requests. Age/deletion
filters alone do not reproduce the published set, so use the existing generator
rather than filtering the JSONL manually. The command does not modify JSONL.
Without `--offline`, the generator may log in and fetch reports.

Review the HTML diff against the latest page: report count, URLs, text, images,
and existing filter attributes should remain unchanged for a matching enriched
JSONL/HTML pair. Only the sort UI/script and sort metadata should change.
Then run the regression command above and `npm run check:html`.
