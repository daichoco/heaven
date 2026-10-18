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

## srcスクリプトの機能とオプション

以下のコマンドはリポジトリのルートで実行します。オプションの記載がない
スクリプトはコマンドライン引数を受け取りません。ネットワークを利用する
スクリプトは、生成ページに加えて `data` 内のファイルも更新する場合があります。

### CityHeaven・統合カレンダー

| コマンド | 機能・オプション |
| --- | --- |
| `py -3 src\cityheaven\cityheaven_month_schedule.py` | ChromiumでCityHeavenを開いて土日出勤情報を収集し、`docs\index.html` を生成します。`CITYHEAVEN_USER` と `CITYHEAVEN_PASS` が必要です。 |
| `py -3 src\cityheaven\exists_merge.py` | CityHeavenのプロフィール・出勤情報を収集し、`data\cityheaven_profiles.json` に保存します。上記のCityHeaven認証情報が必要です。 |
| `py -3 src\integrations\report_left_merge.py` | CityHeavenの出勤・プロフィール情報を取得し、保存済みの東海・オキニラブ・NS専門レポートと結合して `docs\calendar_report.html` を生成します。上記のCityHeaven認証情報が必要です。東海レポート履歴も更新し、オキニラブ・NS専門のURL突合結果を更新する場合があります。 |

統合カレンダーのCityHeavenブラウザー動作は、環境変数で変更できます。
`CITYHEAVEN_HEADLESS=0` でブラウザーを表示し、
`CITYHEAVEN_BLOCK_RESOURCES=0` で画像・フォント・動画の読み込みを許可します。
オキニラブの時間がかかる保守処理は既定でスキップされます。
`OKINILOVE_CHECK_UNAVAILABLE=1` で掲載終了確認、
`OKINILOVE_SEARCH_ENRICH=1` で不足しているCityHeavenプロフィールURLの検索補完を行います。

### NS専門レポート

| コマンド | 機能・オプション |
| --- | --- |
| `py -3 src\ns_senmon\collect_ns_senmon.py` | 新しいNS専門記事を収集し、`data\ns_senmon_reports.jsonl` に保存します。`NS_SENMON_USER` と `NS_SENMON_PASS` が必要です。`NS_SENMON_FULL=1` を設定すると、最新保存日以降に限らず全履歴を走査します。`NS_SENMON_MAX_PAGES` で一覧の最大ページ数を指定できます（既定値: 200）。 |
| `py -3 src\ns_senmon\ns_senmon_login.py` | NS専門のログイン認証情報を確認します。ログインに失敗するとエラーを表示します。 |
| `py -3 src\ns_senmon\match_cityheaven.py` | 手動マップ、既存レポート、店舗ページ検索を使って、保存済みNS専門記事とCityHeavenプロフィールを突合します。記事JSONLを更新し、未突合データを `data\ns_senmon_unmatched.json` に出力します。 |
| `py -3 src\ns_senmon\generate_ns_senmon_report.py` | 保存済みNS専門JSONLから `docs\ns_senmon_report.html` を生成し、取得可能なプロフィール画像を取得します。 |

### 東海レポート

| コマンド | 機能・オプション |
| --- | --- |
| `py -3 src\tokai_fuzoku\collect_fuzoku_tokai.py` | 新しい記事を差分収集し、`data\tokai_fuzoku_reports.jsonl` に保存します。最新保存記事から `--overlap-days N` 日さかのぼって確認します（既定値: 3）。`FUZOKU_TOKAI_PASSWORD` が必要です。 |
| `py -3 src\tokai_fuzoku\generate_report_final.py` | `docs\tokai_final_report.html` を生成します。既定ではログインし、プロフィール掲載状況を再確認し、不足しているレポート詳細とプロフィール画像を取得します。O-ren Groupの個人ページURLが個人プロフィールではなく一覧などを返し、対象者の名前を確認できない場合は掲載終了として除外します。`--offline` を指定すると、保存済みの詳細・掲載状況のみを使い、ログインやネットワーク通信を行いません。オンライン実行には `FUZOKU_TOKAI_PASSWORD` が必要です。 |

収集スクリプトでは、次のモードは同時に指定できません。

| オプション | 機能 |
| --- | --- |
| `--backfill` | 過去の一覧ページを走査し、未保存の記事を取り込みます。中断した補完を再開するには `--resume` を併用します。 |
| `--fill-details` | 保存済み記事のうち未保存の点数・コメント・時間・費用を補完します。 |
| `--recheck-profiles` | ログインせず、保存済みのプロフィール掲載状況を人物単位で再確認します。既定では、前回掲載終了と判定された人物が対象です。 |

その他の収集オプション: `--recheck-profiles` と併用する `--status published`、
`--status unavailable`、`--status unknown` は複数指定でき、再確認する保存状態を選べます。
`--max-pages N` は一覧の最大ページ数です（既定値: `FUZOKU_TOKAI_MAX_PAGES`
または75）。`--limit N` は処理する記事数の上限です（`--recheck-profiles` の場合は人物数）。
`--resume` は `--backfill` と併用してください。

### オキニラブレポート

| コマンド | 機能・オプション |
| --- | --- |
| `py -3 src\okinilove\scrape_okinilove.py` | オキニラブの記事・口コミデータを更新し、`docs\okinilove_remove_unknown.html` を再生成します。 |
| `py -3 src\okinilove\scrape_okinilove.py --recover-numbered-profile-article URL` | 指定したオキニラブ記事から、番号付きの `www` プロフィールURLを探して取り込みます。 |
| `py -3 src\okinilove\maintain_okinilove.py --check-unavailable` | 保存済みプロフィールのHTTP 404/410を確認し、掲載終了プロフィールを削除します。保存済みCityHeaven出勤カレンダーに掲載中のプロフィールは対象外です。 |
| `py -3 src\okinilove\maintain_okinilove.py --enrich` | 店舗ページを検索して、不足しているCityHeavenプロフィールURLを補完します。 |
| `py -3 src\okinilove\render_people_summary.py` | 既存ページから人物別オキニラブHTMLと口コミ本文JSONを再生成します。不足しているプロフィール画像や業種情報を取得する場合があります。 |

保守オプションは併用できます。`--time-budget N` は検索補完に使う時間の上限を秒数で指定します（既定値: 20）。`--enrich` に適用されます。`--check-unavailable` または `--enrich` のいずれかを指定してください。

### オフラインでのレポート生成

| コマンド | 機能 |
| --- | --- |
| `py -3 src\generate_booking_difficulty_report.py` | 保存済みのNS専門・オキニラブ・東海レポートから `docs\reservation_difficulty.html` を生成します。オンラインデータの収集は行いません。 |
| `py -3 src\generate_weekly_schedule_report.py` | 3レポート全体の横断順位と、保存済みの本日から7日間の予定から `docs\weekly_schedule.html` を生成します。既定ではネットワーク通信しません。 |

### 3レポート横断の7日間出勤カレンダー

```powershell
py -3 src\generate_weekly_schedule_report.py
py -3 src\generate_weekly_schedule_report.py --collect
py -3 src\generate_weekly_schedule_report.py --collect --include-a
```

最初のコマンドはオフライン生成、2番目はSを取得、3番目はSに続いてAも取得します。
取得にはPlaywright Chromium（未導入なら `py -3 -m playwright install chromium`）が必要です。
公開プロフィールの月間予定を読みます。ログイン・年齢確認・アクセス制限などで
予定が読めない場合は取得エラーとして表示し、認証や制限を回避しません。
`CITYHEAVEN_HEADLESS=0`、`CITYHEAVEN_BLOCK_RESOURCES=0` は既存収集と同様に使用できます。
1人ずつ取得し、対象者のエラーは保存・表示して残りを処理します。
対象者の取得エラーが1件以上あれば、HTMLとJSONを保存したうえで終了コード1を返します。
ブラウザー起動失敗など実行基盤のエラーは例外で停止します。

母集団は掲載終了扱いを既存ルールで除外した3レポートの全評価人物です。
予約困難・人気の文言、年齢、3件未満では除外しません。NS専門・東海の総合満足度と
オキニラブの星を既存の相対スコア方式（同点平均順位のパーセンタイル、3件分の50点への平均回帰）
で補正し、評価のある元のみを等重み平均します。欠けた元は0点にしません。
無評価は順位対象外。1評価元は「暫定」とし、排除しません。
評価元数と、各元で同一人物・記事URLを重複除去した評価件数を表示します。
記事URLのHTTP/HTTPS違いは同一、口コミフラグメント違いは別件です。

正規化したCityHeavenプロフィールURL（`girlid-N`まで、クエリ・フラグメント除去、
www有無を統一）だけで同一人物を統合します。外部サイトURL・URLなしは記事単位で残し、
同姓同名や移籍を推測で統合しません。未突合者も順位の分母に含め、取得対象の繰り上げはしません。
区分は人数切り上げの上位10%がS、その次から上位25%がA。
丸め前スコアの同点は競技順位（1, 1, 3）とし、境界同点は全員上位区分へ入れます。
このため割合を超える場合があり、少人数ではAが存在しない場合があります。

期間は実行時の日本時間を基準に**本日から7日間（本日〜6日後）**です。
平日を含む7日間をISO日付で保存し、年末年始も対象週の日付と照合します。
ページにない日は「未公開」、空欄も「未公開」、明示された「休み」は休み、
「未定」は未定、不明な文言は原文付きの「要確認」として扱います。
HTTPエラー・別ページ転送・DOM変更は「取得エラー」であり、休みとは扱いません。
月間予定が対象週の一部しか公開していなくても推測や別日の転用はしません。
翌月への追加ナビゲーションは行わないため、対象日がページにない場合は未公開のままです。

取得結果は `data\cityheaven_weekly_schedules.json` に期間開始日・プロフィールURL別に
取得日時、7日間の状態と原文、エラーを保存します。再実行は同じ対象者の結果を更新し、
今回取得しなかったAや過去期間は保持します。形式v2の `windows[開始日][プロフィールURL]` を使用します。
旧v1の `weeks` は読み込み時に同じ開始日・7日分のまま移行し、収集時にv2で保存します。
オフライン読み込みでは元ファイルを書き換えません。開始日が一致する保存期間だけを利用し、
前日開始の期間や旧翌週データを本日へずらしたり、欠けた日を補完したりしません。
オフライン生成では対象期間だけを表示し、
取得日時を併記します（自動的な鮮度保証はしません）。
データがなければ「未取得」、URLがなければ「CityHeaven URL未突合」です。
一覧と予約困難ページから移動でき、既存の土日カレンダーは変更しません。

外観は `calendar_report.html` に合わせ、PCでは人物欄＋日別列、
スマホ・タブレットでは人物カード＋横スクロール日別ボックスを表示します。
固定ツールバーのセレクタは「予定ありのみ（7日間）」が既定です。
「本日出勤」は閲覧時のJST本日、「土日出勤」は保存期間内の土日のいずれかに
明示的な出勤予定がある人物に絞り、対応する日付列・カードだけを表示します。
未取得・未公開・未定・休み・要確認・取得エラーは出勤ありに含めません。
「全表示」でこれらの状態も確認できます。表示0名では空状態と確認方法を案内します。
本日が保存期間外なら再生成・再取得を案内し、保存初日を本日と偽って表示しません。
表示フィルタは取得対象・ランキング母集団・スコア・順位・S/A区分を変更しません。
JavaScript無効時も生成時の予定ありのみを表示します（セレクタ操作はJavaScriptが必要）。

「日付指定」は対象7日間の指定日に出勤予定がある人物を表示します。
日付セレクタを操作すると出勤条件も「日付指定」に切り替わり、本日・土日条件は残りません。
年齢は予約困難ページと同じ「絞り込みなし（既定）／30歳未満／30歳以上／不明」で併用できます。
NS専門の保存済み推定年齢、東海の推定年齢、オキニラブの保存年齢を使用し、
**現在の年齢とは断定しません**。複数年齢がある人物は該当するどちらの区分にも一致します。
年齢欠損・不正値は不明であり0歳とはしません。

人物欄には横断相対スコアとは別に、各元の**元評価平均・件数**を表示します
（NS専門・東海は `/100点`、オキニラブは `★平均/5`）。
`calendar_report` と同様、人物欄のレポート元別「レビュー（元名・件数）」ボタンで、
人物行の直下に全日付列をまたぐ本文行を開閉します。PCはテーブル内の全幅行、
モバイルは人物カード直下の全幅ブロックです。クリック・Enter・Spaceで操作でき、
`aria-controls` と `aria-expanded` で開閉状態を示します。
開いた元の全記事の日付・元評価・出典リンク・保存済み本文をまとめて表示し、
記事ごとの追加クリックは不要です。別のレビューボタンを開くと前の本文行を閉じ、
フィルタを変更したときも閉じます。定期的な表示更新だけでは開いている本文を閉じません。
本文欠損は明示し、新たに外部から本文を取得しません。
HTMLはエスケープして表示します。週間ページのアコーディオン・本文ボタンは共通の自動上端スクロールを
使用せず、標準の開閉・スクロールアンカリングを維持します。他のページの操作は変更しません。
別本文を閉じたことでクリック位置より上の高さが変化した場合も、操作したボタンの画面内位置を維持します。

画像は既存NSプロフィール画像キャッシュ、統合カレンダー、週間予定内の画像を優先再利用し、
オフライン生成だけでも既存画像を表示できます。今後の `--collect` は同じプロフィール応答から
画像URLと状態も取得・予定内に保存します。旧キャッシュに画像キーがなくても表示できます。
不足画像だけ取得する場合（保存済み予定は変更しません）:

```powershell
py -3 src\generate_weekly_schedule_report.py --collect-images --image-limit 20
```

Sの画像未取得者を順位順に最大20人、既存プロフィール画像取得ヘルパーで収集し、
`data\cityheaven_weekly_images.json` に保存します。Aを含める場合は `--include-a` を追加します。
件数は `--image-limit` で指定し、取得処理の時間上限は既存ヘルパーの120秒です。
画像なし・エラーの再試行待ちは既存規則の7日です。画像なし／未取得／取得失敗／
表示時の読込失敗を明示します。画像URLを保存し、画像本体は表示時に読み込みます。
画像・代替表示は97×97pxを予約し、読込時に後続の位置が移動しないようにしています。

上記に記載していない `src` 内のファイルは、共通ヘルパー、レンダラー、
データアクセス用モジュールです。単独で実行するのではなく、各エントリーポイントから読み込まれます。

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
| `weekly_schedule.html` | `src/generate_weekly_schedule_report.py` |

Calendar review buttons retain the collapsed-by-default behavior. Open rows use
CSS to switch between cards (up to 1100 CSS pixels, or coarse-pointer touch
devices) and desktop table rows, including after rotation/resizing. Tablet
reviews no longer inherit a seven-day table's oversized minimum width.
Each review spans every schedule column. Long review text wraps
at all widths. Buttons expose `aria-controls` / `aria-expanded`, including when the
shared script fails to load. Offline Chromium/WebKit touch and desktop checks are
in `tests/test_calendar_browser.py` (390, 820, 1024, 1366, and 1440 CSS-pixel viewports).
These are emulations, not physical iPhone/iPad tests.

`reservation_difficulty.html` is generated offline from the NS専門,
オキニラブ, and 東海風俗 JSONL reports. It lists candidates whose saved report
text describes booking difficulty, being sold out (`完売`), or popularity
(`人気`). These are separate, nonexclusive categories with a classification
filter; the default shows booking-difficulty and sold-out candidates in the
ordinary-candidate group, excluding people supported only by uncertain evidence.
Popularity candidates include people described as popular but easy to book.
Negated popularity/sellout statements and future predictions are excluded.
Substring matches in `恋人気分` (feeling like lovers) and `不人気`
(unpopular) are not counted as popularity evidence or highlighted.
Inflected negations (`予約が埋まっていない`, `完売もなく`,
`人気嬢ではない`), explicit predictions, shop-only popularity (`人気店`),
and clear fallback choices referring to another person are excluded.
Checks use individual fields and sentence-local context rather than suppressing
an entire review because a different sentence contains a negation.
Ambiguous comparisons, subject references, equipment-description fields, and
speculation carry visible review reasons. The confirmation filter separates
ordinary candidates, all candidates, and review-only people. A person with
independent ordinary evidence remains an ordinary candidate; uncertain mentions
do not qualify them for an additional category in the default confirmation mode.
Names that also occur as saved shop names, or missing/placeholder names, require
identity review and are kept separate by article instead of merged by a
potentially misidentified profile URL. Source records are not corrected or guessed.
Keywords may still refer to another person or a temporary situation: the labels
describe saved wording, not verified popularity or current availability.
Person table cells, photos,
ratings, and detail rows are embedded from the existing docs reports, so
reviews can be read without leaving the page. Okinilove's separate review
bodies are inlined at generation time; no additional fetch is needed to open
them. Matching reviews are marked and their bodies open when the person's
details are expanded. Person-level ratings retain all reviews in the source
docs, including those without booking-difficulty wording.
Every matching entry includes a separate **抽出に使用した保存本文** block
containing the complete, exact saved fields that supplied accepted or uncertain
matches, with their field labels, when those fields are not already visible.
Tokai comment text already displayed in its report is highlighted in place
instead of being copied a second time. Highlights use the extraction's exact
character ranges, not a second search against a possibly different docs body.
Text is escaped; the source docs remain unchanged. A notice identifies fields
whose complete text cannot be found in the existing docs display (ignoring
whitespace), including omitted comments and different saved versions.
HTTP/HTTPS-equivalent article links share a rendered entry, but all distinct
saved evidence fields and raw records remain available.
The age filter uses Okinilove's categories: no restriction, under 30, 30 or
older, and unknown. It reads the displayed person ages across all three
reports, not ages mentioned in review text. A person with multiple displayed
ages matches either applicable age category; unknown means no displayed age
is available. Age and text filters apply together.

Cross-database sorting defaults to a relative satisfaction score, independent
of popularity/sellout mentions. Scores use all saved records in each DB,
including people outside the candidate list: NS/Tokai overall points and
Okinilove stars. Records sharing a person key and article URL are counted once
(HTTP/HTTPS variants are equivalent; review fragments remain distinct).
Each person's mean rating is converted to a 0–100 percentile within that DB,
using average ranks for ties and 50 for a single rated person. The adjusted
score is `(count * percentile + 3 * 50) / (count + 3)`. The three-review prior
pulls small samples toward the DB's mean percentile (50).

Available DB scores are averaged with equal weight per DB, not by review
count. Missing DB ratings do not count as zero; people without any ratings
sort last. The page shows the original DB averages, rated review counts,
per-DB adjusted scores, and combined score. Saved-data person grouping and
coverage may differ from the embedded source report summaries. These scores
are relative positions, not an absolute quality comparison across sources.
Separate sort options use distinct matching article counts or the original
extraction order. Sorting preserves the age, category, confirmation, and text filters. Record
deep links reveal review-only candidates as well as ordinary candidates.

Okinilove records missing from its existing docs are rendered offline with
its existing person-summary renderer and saved review bodies.
Tokai records excluded from its normal report are rendered from the matching
JSONL records using the same person/report HTML renderer, without applying
that report's age filter. Records marked unavailable are excluded using the
latest Tokai person status, fresh Okinilove 404/410 status-cache entries, and
NS「掲載終了?」or cached profile 404/410 results. Unknown or stale Okinilove
status results are retained. Their profile images are reused from the generated
Tokai report when the profile URL matches. The full saved JSON and source links
remain available as secondary information. The databases do not retain original
external-page HTML. Generation requires the three existing docs
reports and `docs/okinilove_review_bodies.json`; it never collects online data.
Regenerate it with:

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
