# Windows PCでのローカル定期実行

この手順は、Windows 10/11のPCで既存の収集スクリプトを定期実行するためのものです。
特定のPC機種は前提にしません。タスク登録は、このガイドを読んだ人が必要な場合にだけ行ってください。

## 定期更新の運用

定期更新はローカルPCのタスクスケジューラで行います。GitHub Actionsの定期実行は廃止しました。
同じデータを複数のマシンから同時に更新しないでください。競合や古いHTMLによる上書きが
起きる可能性があります。複数のマシンで実行する場合は、同時に動かさないでください。

## 1. Windowsの準備

1. Git for Windowsをインストールし、PowerShellで `git --version` が成功することを確認します。
2. **Python 3.12** をインストールし、Python Launcherが使えることを確認します。
   ```powershell
   py -3.12 --version
   ```
3. 東海レポートを実行する場合はGoogle Chromeをインストールします。収集コードはSeleniumと
   `chromedriver-autoinstaller` を使います。
4. HTMLを生成する日次処理ではNode.jsも必要です。Node.js 22 LTS以上をインストールしてください。
   フル更新モードまたは週間予定の `--collect` を使う場合は、PythonのPlaywright用Chromiumも
   後述のコマンドで導入します。

Windows Update後の自動再起動を許可し、停電復旧後にPCを自動起動する設定がある場合は有効にします。
停電後の自動起動はBIOS/UEFIの機能で、全機種にあるとは限りません。

## 2. OneDrive外にチェックアウトを用意

同期中のファイル競合や意図しない実行を避けるため、OneDrive配下ではなく専用フォルダーを使います。
以下は例です。すでに専用チェックアウトがある場合は、そのフォルダーを使ってください。

```powershell
New-Item -ItemType Directory -Force C:\automation | Out-Null
Set-Location C:\automation
git clone https://github.com/daichoco/heaven.git
Set-Location C:\automation\heaven
git switch main
git pull --ff-only origin main

py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm ci
```

`.venv` はリポジトリのignore対象です。リポジトリ更新後に依存が変わった場合は、
`git pull --ff-only origin main` の後にpip installと `npm ci` を再実行します。
フル更新モードまたは週間予定のブラウザー収集を使う場合は、Playwrightのブラウザーも入れます。

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

レポート生成時の `src\html_format.py` はNode.jsと `npm ci` で導入するHTMLフォーマッターを呼び出します。
そのため、HTML生成を含む日次ラッパーにはNode.js 22 LTS以上と `npm ci` が必要です。
`npm run check:html` や `npm run format:html` も同じ依存を使います。
`format:html` は `docs` 内のHTMLを書き換えるため、差分を確認してください。

## 3. 認証情報を安全に設定

スクリプトは認証情報を環境変数から読み込みます。Windowsの「環境変数を編集」画面で、
タスクを実行するWindowsユーザーの**ユーザー環境変数**として必要な値を設定してください。
設定後は新しいPowerShellを開きます。パスワードをリポジトリ、スクリプト、タスクの引数、
コマンド履歴、ログに書かないでください。

| 環境変数 | 使う機能 |
| --- | --- |
| `NS_SENMON_USER`、`NS_SENMON_PASS` | NS専門の記事収集 |
| `FUZOKU_TOKAI_PASSWORD` | 東海と関東の記事収集。関東も東海と同じ変数名を使います。 |
| `CITYHEAVEN_USER`、`CITYHEAVEN_PASS` | フル更新モードの統合カレンダー（CityHeavenログイン）。標準の日次3サイト更新と週間予定収集には不要です。 |

ユーザー環境変数はそのWindowsアカウントで動くプロセスから参照できます。PCを専用のWindowsアカウントで
運用し、Windowsアカウントのパスワードを保護し、BitLocker等のディスク暗号化を有効にしてください。
タスクスケジューラに認証情報を埋め込まず、専用PowerShellファイルにも値を書きません。
GitHubへのpushはGit Credential Manager等で、タスクと同じWindowsアカウントから事前に認証してください。
一度そのアカウントで `git ls-remote origin main` を実行して認証を完了し、commit作者情報も設定します。
次の値は実際にGitHubで使う名前とメールアドレスに置き換えてください（この設定はリポジトリ内の `.git/config` に保存されます）。

```powershell
git config user.name "GitHubで使う名前"
git config user.email "GitHubで使うメールアドレス"
```

トークンをURLやスクリプトに保存しないでください。

## 4. まず手動で実行

PCへ移す前に、タスクと同じWindowsアカウントでPowerShellを開き、環境変数とサイト接続を確認します。
以下は日次ラッパーの処理順です。途中で失敗したら、後続コマンドへ進まずエラーを確認してください。

```powershell
Set-Location C:\automation\heaven
$python = (Resolve-Path .\.venv\Scripts\python.exe).Path

git status --short
git pull --ff-only origin main

& $python src\okinilove\scrape_okinilove.py
if ($LASTEXITCODE -ne 0) { throw "オキニラブの更新に失敗しました" }

& $python src\ns_senmon\collect_ns_senmon.py
if ($LASTEXITCODE -ne 0) { throw "NS専門の記事収集に失敗しました" }
& $python src\ns_senmon\match_cityheaven.py
if ($LASTEXITCODE -ne 0) { throw "NS専門のCityHeaven突合に失敗しました" }
& $python src\ns_senmon\generate_ns_senmon_report.py
if ($LASTEXITCODE -ne 0) { throw "NS専門HTMLの生成に失敗しました" }

& $python src\tokai_fuzoku\collect_fuzoku_tokai.py
if ($LASTEXITCODE -ne 0) { throw "東海の記事収集に失敗しました" }
& $python src\tokai_fuzoku\generate_report_final.py
if ($LASTEXITCODE -ne 0) { throw "東海HTMLの生成に失敗しました" }
```

標準の手動手順はここまでです。追加のCityHeaven横断ページも更新する場合は、上記の3サイト処理が
すべて成功した後、同じPowerShellで次も順番に実行してください。これは後述のラッパーの
`-IncludeCityHeavenCalendarAndWeekly` スイッチを有効にしたときと同じ処理です。

```powershell
$env:CITYHEAVEN_HEADLESS = '1'
& $python src\cityheaven\cityheaven_month_schedule.py
if ($LASTEXITCODE -ne 0) { throw "月間スケジュールの更新に失敗しました" }
& $python src\integrations\report_left_merge.py
if ($LASTEXITCODE -ne 0) { throw "CityHeaven統合カレンダーの生成に失敗しました" }
& $python src\generate_weekly_schedule_report.py --collect --include-a
if ($LASTEXITCODE -ne 0) { throw "週間予定の取得またはHTML生成に失敗しました" }
```

統合カレンダーには `CITYHEAVEN_USER` と `CITYHEAVEN_PASS` が必要です。フル更新モードにはPlaywright
Chromiumが必要で、Node.js 22 LTS以上と `npm ci` もHTML生成に使います。週間予定は認証なしの公開ページを
Playwright Chromiumで取得し、`--include-a` はSに続けてAも予定取得対象にします。
東海HTML生成はオンラインで掲載状況・不足詳細・画像を確認します。いずれもネットワークが必要です。
`--offline` は保存済みデータだけで生成する機能であり、収集や通常のオンラインHTML生成の代わりにはなりません。
成功後、`git status --short` と変更内容を確認してください。

## 5. タスクスケジューラ用のラッパーを作る

1つのタスクから複数スクリプトを逐次実行します。ラッパーはチェックアウト外に保存します
（例：`C:\automation\run-heaven.ps1`）。以下を保存し、チェックアウト場所を変更した場合は
`$Repo` の値も合わせます。スクリプトはログを `%LOCALAPPDATA%\HeavenScheduledReports\logs` に保存します。

既定では3サイト更新だけを実行します。統合カレンダーとS/A週間予定も毎回更新するには、
タスクスケジューラの引数に `-IncludeCityHeavenCalendarAndWeekly` を追加してください。
このスイッチは、3サイト更新の後にCityHeaven統合カレンダーと週間予定・画像取得を順番に実行します。
実行前に `main` がクリーンであることと、リモートからfast-forwardできることを確認します。
選択されたモードの対象ファイルだけをstageし、全処理が成功した場合に変更をcommitして通常の `git push` をします。
自動rebaseやforce pushは行いません。Pushが拒否された場合は失敗ログを残し、ローカルcommitを保持して停止します。

```powershell
param([switch]$IncludeCityHeavenCalendarAndWeekly)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repo = 'C:\automation\heaven'
$Python = Join-Path $Repo '.venv\Scripts\python.exe'
$LogDir = Join-Path $env:LOCALAPPDATA 'HeavenScheduledReports\logs'
New-Item -ItemType Directory -Force $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("run-{0}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$LockPath = Join-Path $LogDir 'runner.lock'
$Lock = $null
$TranscriptStarted = $false
$LocationPushed = $false
$env:PYTHONUTF8 = '1'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

function Send-Line([string]$Text) {
    if (-not $env:LINE_CHANNEL_TOKEN -or -not $env:LINE_USER_ID) { return }
    try {
        $body = @{ to = $env:LINE_USER_ID; messages = @(@{ type = 'text'; text = $Text }) } | ConvertTo-Json -Depth 5
        Invoke-RestMethod -Method Post -Uri 'https://api.line.me/v2/bot/message/push' `
            -Headers @{ Authorization = "Bearer $env:LINE_CHANNEL_TOKEN" } `
            -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
    } catch {
        Write-Warning "LINE通知に失敗しました: $($_.Exception.Message)"
    }
}

try {
    Start-Transcript -Path $LogPath -Append | Out-Null
    $TranscriptStarted = $true
    $Lock = [System.IO.File]::Open(
        $LockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )

    if (-not (Test-Path $Python)) { throw "Pythonが見つかりません: $Python" }
    $branch = (& git -C $Repo branch --show-current).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Gitのブランチを確認できません' }
    if ($branch -ne 'main') { throw "main以外では実行しません: $branch" }
    $changes = & git -C $Repo status --porcelain
    if ($LASTEXITCODE -ne 0) { throw 'Gitの作業ツリーを確認できません' }
    if ($changes) { throw '作業ツリーに変更があります。内容を確認してから再実行してください' }

    function Invoke-Git {
        param([string[]]$GitArgs)
        & git -C $Repo @GitArgs
        if ($LASTEXITCODE -ne 0) { throw "Gitに失敗しました: $($GitArgs -join ' ')" }
    }

    function Invoke-Python {
        param([string[]]$PythonArgs)
        & $Python @PythonArgs
        if ($LASTEXITCODE -ne 0) { throw "Python処理に失敗しました: $($PythonArgs -join ' ')" }
    }

    Push-Location $Repo
    $LocationPushed = $true
    Invoke-Git -GitArgs @('pull', '--ff-only', 'origin', 'main')

    Invoke-Python -PythonArgs @('src\okinilove\scrape_okinilove.py')
    Invoke-Python -PythonArgs @('src\ns_senmon\collect_ns_senmon.py')
    Invoke-Python -PythonArgs @('src\ns_senmon\match_cityheaven.py')
    Invoke-Python -PythonArgs @('src\ns_senmon\generate_ns_senmon_report.py')
    Invoke-Python -PythonArgs @('src\tokai_fuzoku\collect_fuzoku_tokai.py')
    Invoke-Python -PythonArgs @('src\tokai_fuzoku\generate_report_final.py')

    if ($IncludeCityHeavenCalendarAndWeekly) {
        $env:CITYHEAVEN_HEADLESS = '1'
        Invoke-Python -PythonArgs @('src\cityheaven\cityheaven_month_schedule.py')
        Invoke-Python -PythonArgs @('src\integrations\report_left_merge.py')
        Invoke-Python -PythonArgs @('src\generate_weekly_schedule_report.py', '--collect', '--include-a')
    }

    $Summary = (& $Python src\report_change_summary.py) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw '更新件数の集計に失敗しました' }
    Write-Output $Summary

    $paths = @(
        'data\okinilove_cityheaven_enrich_cache.json',
        'data\okinilove_report_details.json',
        'data\okinilove_reviews.jsonl',
        'data\okinilove_shop_index.json',
        'data\okinilove_skipped_articles.json',
        'docs\okinilove_remove_unknown.html',
        'docs\okinilove_review_bodies.json',
        'data\ns_senmon_reports.jsonl',
        'data\ns_senmon_unmatched.json',
        'data\ns_senmon_shop_search_cache.json',
        'data\ns_senmon_profile_image_cache.json',
        'docs\ns_senmon_report.html',
        'data\tokai_fuzoku_report_history.jsonl',
        'data\tokai_fuzoku_reports.jsonl',
        'docs\tokai_final_report.html',
        'docs\calendar_report.html',
        'docs\index.html',
        'data\cityheaven_weekly_schedules.json',
        'data\cityheaven_weekly_images.json',
        'docs\weekly_schedule.html'
    )
    Invoke-Git -GitArgs (@('add', '-u', '--') + $paths)
    if ($IncludeCityHeavenCalendarAndWeekly) {
        Invoke-Git -GitArgs @('add', '-A', '--', 'docs\weekly_schedule_assets')
    }

    & git -C $Repo diff --cached --quiet
    $diffExitCode = $LASTEXITCODE
    if ($diffExitCode -eq 1) {
        Invoke-Git -GitArgs @('commit', '-m', 'Update scheduled reports')
    } elseif ($diffExitCode -eq 0) {
        Write-Output '今回の更新ではcommitする変更はありません。'
    } else {
        throw 'ステージ済み差分を確認できません'
    }
    Invoke-Git -GitArgs @('push', 'origin', 'main')
    Send-Line "定期更新が完了しました ($env:COMPUTERNAME)`n$Summary"
} catch {
    Send-Line "定期更新に失敗しました: $($_.Exception.Message)`nログ: $LogPath"
    Write-Error $_
    exit 1
} finally {
    if ($LocationPushed) { Pop-Location }
    if ($Lock) { $Lock.Dispose() }
    if ($TranscriptStarted) { Stop-Transcript | Out-Null }
}
```

スイッチなしではオキニラブ、NS専門、東海の3サイト更新だけを実行し、
追加モードではCityHeaven統合カレンダーと週間予定・画像まで実行します。
フル更新で追跡対象となる追加出力は `data\tokai_fuzoku_report_history.jsonl`、
`data\okinilove_reviews.jsonl`、必要なURL突合時に更新される
`data\ns_senmon_shop_search_cache.json`、`docs\calendar_report.html`、
`data\cityheaven_weekly_schedules.json`、`data\cityheaven_weekly_images.json`、
`docs\weekly_schedule.html`、`docs\weekly_schedule_assets\` 内の一覧・レビューJSONです。
`data\cityheaven_profiles.json` は統合カレンダー生成途中で作られ、読み込み後に削除される一時ファイル
（Git ignore対象）なのでstageしません。画像や予定に失敗が残り週間スクリプトが終了コード1を返した場合、
更新済みの一部ファイルが残ることがありますが、ラッパーはstage・commit・pushへ進まず停止します。

LINE通知（任意）：LINE Developersで作成したMessaging APIチャネルのチャネルアクセストークン（長期）と、通知先の
ユーザーIDを、ユーザー環境変数 `LINE_CHANNEL_TOKEN` と `LINE_USER_ID` に設定します。どちらかが未設定なら通知しません。
成功時と失敗時にPush通知を送ります。成功時は `src\report_change_summary.py` が、commit前のデータとHEADを比較した
東海・NS専門・オキニラブの追加・削除件数と、高得点で追加されたレポート（最大10件）を本文に含めます。
高得点の基準は、東海が総合点80以上かつ「返す（リピートする）」、NS専門が総合満足度90点以上、オキニラブが星4以上です。
基準を変える場合は同スクリプト先頭の定数を編集します。通知自体の失敗は更新処理を止めません。Pushは無料プランで月200通までです。
トークンはリポジトリにcommitしないでください。ラッパーは文字化け防止のため `PYTHONUTF8=1` と `[Console]::OutputEncoding`（UTF-8）も設定します。
後者がないと、Pythonの出力をPowerShellで受け取った集計文がLINEで文字化けします。

認証情報やローカルの収集再開状態ファイルをcommitしません。別のマシンが先にpushすると
このpushは拒否されます。作業ツリーが不潔、ブランチがmainでない、pullがfast-forwardできない場合も
何も自動修復せず停止します。いずれの場合もログ・`git status`・`git log --oneline --decorate -5` を確認し、
競合を人が解決してから次の実行に進んでください。`reset --hard`、force push、未確認のrebaseで
データを捨てないでください。

## 6. タスクスケジューラに登録

1. **タスクの作成**（基本タスクではなく通常のタスク）を選び、日次のトリガーを設定します。
2. **操作**は次のとおりです。実際にラッパーを保存した場所へパスを合わせます。
   - プログラム/スクリプト：`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
   - 引数（標準の3サイト更新）：`-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "C:\automation\run-heaven.ps1"`
   - 引数（CityHeaven統合カレンダーと週間予定も更新）：`-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "C:\automation\run-heaven.ps1" -IncludeCityHeavenCalendarAndWeekly`
   - 開始（オプション）：`C:\automation`
3. **全般**では、環境変数を設定したWindowsユーザーを指定します。ログイン中でなくても動かす場合は
   「ユーザーがログオンしているかどうかにかかわらず実行する」を選び、タスクスケジューラの案内に従って
   そのユーザーのWindowsパスワードを登録します。初回は「ユーザーがログオンしているときのみ実行する」で
   手動テストしてから切り替える方法もあります。
4. **設定**では「スケジュールされた時刻に開始できなかった場合、すぐにタスクを実行する」を有効にし、
   「タスクが既に実行中の場合」は「新しいインスタンスを開始しない」にします。アプリケーションの再起動を
   自動設定せず、失敗時はログを確認します。
5. **条件**では必要に応じて「タスクを実行するためにスリープを解除する」を有効にします。
   PCがスリープ・休止・電源断中は実行できません。PCを常時稼働させる場合はWindowsの電源設定で
   スリープを無効にし、ミニPCの電源・ネットワーク接続を維持します。
6. 「タスクの実行」を押してテストし、タスク履歴の結果とログを確認します。

ログオンしていない状態の実行では、タスクに指定したWindowsアカウントのユーザー環境変数・Git認証が
利用できることを先に確認してください。対話式ログイン画面やChromeウィンドウに依存する処理は、
非ログイン状態で動作しないことがあります。上記の日次workflowは基本的にバックグラウンド実行ですが、
実サイトへのアクセスと認証が成功する保証ではありません。

## 7. CityHeavenの予定、画像、関東レポート

### 週間予定と人物画像

週間カレンダーの取得・生成は次の既存コマンドで行います。

```powershell
# Sランクの公開プロフィールから本日〜6日後の予定を取得し、HTMLも生成
.\.venv\Scripts\python.exe src\generate_weekly_schedule_report.py --collect

# Sに加えてAランクも予定取得対象にする
.\.venv\Scripts\python.exe src\generate_weekly_schedule_report.py --collect --include-a

# 通信せず、保存済み予定・画像からHTMLを再生成
.\.venv\Scripts\python.exe src\generate_weekly_schedule_report.py --offline

# 東海風俗・NS専門のみで順位・表示し、保存済み予定を再利用
.\.venv\Scripts\python.exe src\generate_weekly_schedule_report.py --offline --display-sources 東海風俗 NS専門
```

`--display-sources` は表示対象・順位・評価の対象ソース自体を制限します。
省略時は3レポート全体です。予定収集オプションと併用時は表示対象の中から取得し、
`--collect-sources` は表示対象に含まれるソースだけ指定できます。
`--output weekly_schedule_tokai_ns.html` を付けると、`docs` 配下の別ファイルに出力します。
分割アセットは `weekly_schedule_tokai_ns_assets/` に分かれるため、3レポート版を上書き・削除しません。
東海・NS専門ページは `--display-sources 東海風俗 NS専門 --output weekly_schedule_tokai_ns.html` で作成します。
`--output` を省略した場合の生成先は `docs\weekly_schedule.html` です。
`--collect` はS対象者の予定を取得し、`--collect --include-a` はSの次にAも取得します。
`--collect --collect-sources 東海風俗 NS専門` は、指定レポートに評価がある人（CityHeaven URLあり）を
点数上位から `--collect-limit`（既定50）人だけ取得します。全体順位のティアには依存しません。
`--collect --collect-high-rated` は、東海風俗・NS専門で高評価の基準（`src/report_change_summary.py` の
`TOKAI_MIN_TOTAL` / `NS_MIN_TOTAL`）を満たす2023年以降の口コミがある人（CityHeaven URLあり）を
全員取得します。`--collect-sources` とは併用できません。
通常のオンラインHTML生成は不足画像も取得し、`--collect` は同じプロフィール応答から予定と画像を保存します。
予定キャッシュは `data\cityheaven_weekly_schedules.json`、画像キャッシュは
`data\cityheaven_weekly_images.json`、HTMLは `docs\weekly_schedule.html` に保存されます。
Playwright Chromiumが必要です。ログインや年齢確認・アクセス制限を回避する機能ではありません。
プロフィール単位の取得失敗や画像取得失敗が残ると、HTMLとキャッシュを保存した後でも終了コード1になることがあります。

週間予定は標準の日次3サイト更新には含まれません。ラッパーに
`-IncludeCityHeavenCalendarAndWeekly` を付けたフル更新モードでは、統合カレンダーの後に
`--collect --include-a` で取得し、キャッシュ・HTML・分割アセットを同じ実行でcommit/pushします。
週間ページの横断対象はNS専門・オキニラブ・東海の3データセットで、関東レポートは含まれません。
別タスクで同じデータを同時更新しないでください。

`src\cityheaven\cityheaven_month_schedule.py`、`src\cityheaven\exists_merge.py`、
`src\integrations\report_left_merge.py` はCityHeavenログイン処理です。`CITYHEAVEN_USER` と
`CITYHEAVEN_PASS` が必要です。`cityheaven_month_schedule.py` と `report_left_merge.py` はフル更新モードで実行します。
`cityheaven_month_schedule.py` はこのモードで統合カレンダーの前に実行し、`docs\index.html` を更新します。`exists_merge.py` はこのモードには含めません。
とくに月間スケジュールスクリプトは
ブラウザー画面を表示する実装のため、ラッパーは `CITYHEAVEN_HEADLESS=1` を設定してヘッドレスで実行します（未設定時は従来どおり画面表示）。

### 掲載終了確認の範囲

各処理の確認対象は**人物プロフィールの掲載状態**です。記事URLそのものが削除されたかどうかを
全記事について確認するものではありません。HTTPエラーや応答を判定できない場合も、掲載終了と決めつけず
保持する仕組みがあります。

| 対象 | 定期更新で行う確認 |
| --- | --- |
| オキニラブ | 通常の `scrape_okinilove.py` は人物プロフィールURLの応答を確認し、HTTP 404/410と判定した人物を既存一覧HTML・レビューJSONLから除いてから更新します。確認結果は7日間のキャッシュを利用し、通信失敗は削除せず保持します。記事URLの掲載確認ではありません。 |
| 東海 | オンラインの `generate_report_final.py` が人物プロフィールを確認し、明確な掲載終了シグナルがある人物を生成HTMLから除外します。収集時に掲載終了と保存済みの人物は先に除外されるため、この通常生成では再確認されません。`--offline` では新しい確認を行いません。 |
| 関東 | オンラインの `generate_report_final.py` が東海と共通の判定処理で人物プロフィールを確認し、明確な掲載終了シグナルをHTMLから除外します。関東データは週間予定・統合カレンダーには結合されません。 |
| NS専門 | CityHeaven突合時に店舗ページの名前検索で掲載を確認できない場合、`not_listed` として「掲載終了?」を表示します。これは照合上の可能性表示で、各プロフィールURLの確定的な掲載終了確認ではありません。 |

掲載終了済みとして保存・除外された人物の復帰確認は、この定期運用では行いません。

### 関東レポート

関東記事の収集とHTML生成コードはありますが、**現在の定期実行に関東は含まれていません**。
ローカルで実行する場合は、先に初回全件収集の範囲を確認してください。

```powershell
# 本番JSONL・再開状態を書かずに事前確認
.\.venv\Scripts\python.exe src\kanto_fuzoku\collect_fuzoku_kanto.py --preflight

# 問題がなければ差分収集し、関東HTMLを生成
.\.venv\Scripts\python.exe src\kanto_fuzoku\collect_fuzoku_kanto.py
.\.venv\Scripts\python.exe src\kanto_fuzoku\generate_report_final.py
```

関東収集には `FUZOKU_TOKAI_PASSWORD` 環境変数を使います。収集結果は
`data\kanto_fuzoku_reports.jsonl`、HTMLは `docs\kanto_final_report.html` です。
公開HTMLの掲載確認・画像取得はオンライン通信を伴います。関東レポートは既存の東海・NS専門・
オキニラブの定期workflowに自動統合されていません。日次ラッパーに組み込む場合は、preflightを
手動で済ませてから、関東コマンドと対象ファイルだけを明示的に追加してください。

## 8. 失敗時の確認と復旧

- タスクスケジューラの「前回の実行結果」と `%LOCALAPPDATA%\HeavenScheduledReports\logs` の最新ログを確認します。
- 401/403、年齢確認、ログイン画面、HTML構造変更、タイムアウトはPCの性能だけでは解消しません。
  サイト側の制限や変更を回避せず、時間をおいて再確認してください。
- 失敗後にファイルが更新されていても、内容を見ずに消去・commitしないでください。ラッパーは次回、
  未コミット変更を検出して停止します。`git status --short` と差分を確認し、保存済みデータを保持したまま
  手動で復旧してください。
- pushが拒否された場合はローカルcommitが残ります。まず `git status --short` と
  `git log --oneline --decorate -5` でローカル・リモートの状態を調べ、競合を確認してから人が解決します。
  ラッパーは自動rebase・force pushをしません。
- PCの再起動後にタスクが動かない場合、トリガー、Windowsアカウント、ユーザー環境変数、
  タスク履歴、電源・スリープ設定、Git Credential Managerの認証状態を確認します。
- スクリプトの引数や出力先は変更されることがあります。更新後にこのガイドと各コマンドの現行実装を確認し、
  定期実行を再開してください。
