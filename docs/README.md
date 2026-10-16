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
