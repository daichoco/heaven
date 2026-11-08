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

## 注意

- パスワードは LINE のトーク履歴に残ります。反映後に削除してください。
- Webhook の URL と `PULL_TOKEN` は公開しないでください。
