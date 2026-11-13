# LINEから東海パスワードを更新する

LINEボットに `パスワード 新しいパスワード` と送ると、Cloudflare Workers が受け取って KV に一時保存します。
ローカルの定時実行ラッパー(`run-heaven.ps1`)が起動時に `/pull` で取り出し、
ユーザー環境変数 `FUZOKU_TOKAI_PASSWORD` を更新します。取り出した値は KV から削除されます。

- 署名検証(`X-Line-Signature`)と送信者の LINE ユーザーID を確認し、自分以外は無視します。
- 保存期間は最大7日です。

## 初回セットアップ

```powershell
cd cloudflare\line-password-worker
npx wrangler login
npx wrangler kv namespace create PASSWORDS   # 出力のidを wrangler.toml に入れる
npx wrangler secret put LINE_CHANNEL_SECRET  # LINE Developers のチャネルシークレット
npx wrangler secret put LINE_CHANNEL_TOKEN   # 既存のチャネルアクセストークン
npx wrangler secret put ALLOWED_USER_ID      # 自分の LINE ユーザーID(LINE_USER_ID と同じ)
npx wrangler secret put PULL_TOKEN           # 長いランダム文字列を作って入れる
npx wrangler deploy
```

1. 表示された `https://...workers.dev` の URL に `/webhook` を付けて、LINE Developers の Webhook URL に設定し、Webhook の利用をオンにします。
2. 応答メッセージ(自動応答)はオフにします。
3. ローカルのユーザー環境変数を設定します。

```powershell
[Environment]::SetEnvironmentVariable('PASSWORD_WORKER_URL','https://...workers.dev','User')
[Environment]::SetEnvironmentVariable('PASSWORD_WORKER_TOKEN','(PULL_TOKENと同じ値)','User')
```

## メニューのボタンから入力する(LIFF フォーム)

トーク下部のリッチメニュー「パスワード更新」を押すと、LINE内で入力フォーム(`/form`)が開きます。
パスワードはトーク履歴に残りません。LINEのアクセストークンを検証し、自分のユーザーID以外は拒否します。

1. LINE Developers で、同じプロバイダーに「LINEログイン」チャネルを作り、LIFFアプリを追加します。
   - エンドポイントURL: `https://...workers.dev/form`
   - スコープ: `profile`
   - 表示されるLIFF ID(`1234567890-xxxxxxxx`)と、LINEログインチャネルのチャネルID(数字)を控えます。
2. Worker に設定してデプロイします。

```powershell
cd cloudflare\line-password-worker
npx wrangler secret put LIFF_ID           # LIFF ID
npx wrangler secret put LIFF_CHANNEL_ID   # LINEログインチャネルのチャネルID
npx wrangler deploy
```

3. リッチメニューを作成します(画像は自動生成)。

```powershell
.\create-rich-menu.ps1 -LiffId <LIFF ID>
```

## LINEから手動実行する

LINEで `更新` と送る(リッチメニューの「更新」「状態」ボタンでも可)と日次処理(`run-heaven.ps1 -IncludeCityHeavenCalendarAndWeekly`)を実行し、`状態` と送ると実行中・最終実行の結果を返します。
Worker が要求を KV に保存し、PC のタスク `HeavenJobPoller`(5分ごと、`poll-heaven-job.ps1`)が `/job` を確認して実行します。
完了・失敗は従来どおりLINEに通知されます。実行中に `更新` を送っても二重起動はしません。

```powershell
$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy RemoteSigned -File "C:\automation\heaven\cloudflare\line-password-worker\poll-heaven-job.ps1"'
$t = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName HeavenJobPoller -Action $a -Trigger $t -Force
```

## 注意

- パスワードは LINE のトーク履歴に残ります。反映後に削除してください。
- Webhook の URL と `PULL_TOKEN` は公開しないでください。
