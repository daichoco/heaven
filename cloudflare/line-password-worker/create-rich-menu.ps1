param([Parameter(Mandatory)][string]$LiffId)

# LINE公式アカウントの下部に「パスワード更新」ボタン(リッチメニュー)を作り、全員のデフォルトに設定する。
$ErrorActionPreference = 'Stop'
$token = [Environment]::GetEnvironmentVariable('LINE_CHANNEL_TOKEN', 'User')
if (-not $token) { throw 'LINE_CHANNEL_TOKEN (ユーザー環境変数) がありません' }
$headers = @{ Authorization = "Bearer $token" }

$menu = @{
    size = @{ width = 2500; height = 843 }
    selected = $true
    name = 'password-update'
    chatBarText = 'メニュー'
    areas = @(@{
        bounds = @{ x = 0; y = 0; width = 2500; height = 843 }
        action = @{ type = 'uri'; label = 'パスワード更新'; uri = "https://liff.line.me/$LiffId" }
    })
} | ConvertTo-Json -Depth 6
$created = Invoke-RestMethod -Method Post -Uri 'https://api.line.me/v2/bot/richmenu' -Headers $headers `
    -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($menu))
$menuId = $created.richMenuId

Add-Type -AssemblyName System.Drawing
$png = Join-Path $env:TEMP 'richmenu.png'
$bitmap = New-Object System.Drawing.Bitmap 2500, 843
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#06c755'))
$graphics.TextRenderingHint = 'AntiAlias'
$font = New-Object System.Drawing.Font 'Meiryo UI', 120, ([System.Drawing.FontStyle]::Bold)
$format = New-Object System.Drawing.StringFormat
$format.Alignment = 'Center'; $format.LineAlignment = 'Center'
$graphics.DrawString('パスワード更新', $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF 0, 0, 2500, 843), $format)
$graphics.Dispose()
$bitmap.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

Invoke-RestMethod -Method Post -Uri "https://api-data.line.me/v2/bot/richmenu/$menuId/content" -Headers $headers `
    -ContentType 'image/png' -InFile $png | Out-Null
Invoke-RestMethod -Method Post -Uri "https://api.line.me/v2/bot/user/all/richmenu/$menuId" -Headers $headers | Out-Null
Remove-Item $png
"リッチメニューを設定しました: $menuId"
