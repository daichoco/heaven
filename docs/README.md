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

## Tokai report sorting

`tokai_final_report.html` sorts individual articles, not people. The default
おすすめ順 compares total, play, face, then style scores in descending
lexicographic order, without weighting or statistical correction. Each score
can also be selected individually, as can newest article date. Missing,
invalid, or non-finite scores come after valid scores (including zero) at each
comparison level. Ties use newest article date, then original input order;
missing dates come last. Repeat status and panel difference never affect rank.

Sorting remains available when the mobile filter panel is collapsed and works
with search and every existing filter. Changing the order preserves expanded
articles and filter conditions; resetting conditions preserves the selected
order. The condition count excludes sorting.

Offline regression tests execute the actual JavaScript from both the template
and published HTML using Node.js, including initial sorting, changing modes,
missing/zero scores, ties, filters, reset, and expanded article state:

```powershell
py -3 -m unittest discover -s tests -p test_tokai_sorting.py
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
