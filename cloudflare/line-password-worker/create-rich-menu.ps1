param([Parameter(Mandatory)][string]$LiffId)

# LINE公式アカウントの下部に「パスワード更新」ボタン(リッチメニュー)を作り、全員のデフォルトに設定する。
$ErrorActionPreference = 'Stop'
$token = [Environment]::GetEnvironmentVariable('LINE_CHANNEL_TOKEN', 'User')
if (-not $token) { throw 'LINE_CHANNEL_TOKEN (ユーザー環境変数) がありません' }
$headers = @{ Authorization = "Bearer $token" }

$menu = @{
    size = @{ width = 2500; height = 843 }
    selected = $true
    name = 'heaven-menu'
    chatBarText = 'メニュー'
    areas = @(
        @{
            bounds = @{ x = 0; y = 0; width = 833; height = 843 }
            action = @{ type = 'message'; label = '更新'; text = '更新' }
        },
        @{
            bounds = @{ x = 833; y = 0; width = 834; height = 843 }
            action = @{ type = 'message'; label = '状態'; text = '状態' }
        },
        @{
            bounds = @{ x = 1667; y = 0; width = 833; height = 843 }
            action = @{ type = 'uri'; label = 'パスワード更新'; uri = "https://liff.line.me/$LiffId" }
        }
    )
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
$font = New-Object System.Drawing.Font 'Meiryo UI', 80, ([System.Drawing.FontStyle]::Bold)
$format = New-Object System.Drawing.StringFormat
$format.Alignment = 'Center'; $format.LineAlignment = 'Center'
$line = New-Object System.Drawing.Pen ([System.Drawing.Color]::White), 6
$labels = '更新', '状態', 'パスワード更新'
$xs = 0, 833, 1667
$widths = 833, 834, 833
for ($i = 0; $i -lt 3; $i++) {
    $graphics.DrawString($labels[$i], $font, [System.Drawing.Brushes]::White, (New-Object System.Drawing.RectangleF $xs[$i], 0, $widths[$i], 843), $format)
    if ($i -gt 0) { $graphics.DrawLine($line, $xs[$i], 60, $xs[$i], 783) }
}
$graphics.Dispose()
$bitmap.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()

Invoke-RestMethod -Method Post -Uri "https://api-data.line.me/v2/bot/richmenu/$menuId/content" -Headers $headers `
    -ContentType 'image/png' -InFile $png | Out-Null
Invoke-RestMethod -Method Post -Uri "https://api.line.me/v2/bot/user/all/richmenu/$menuId" -Headers $headers | Out-Null
Remove-Item $png
$old = Invoke-RestMethod -Uri 'https://api.line.me/v2/bot/richmenu/list' -Headers $headers
foreach ($item in $old.richmenus) {
    if ($item.richMenuId -ne $menuId) {
        Invoke-RestMethod -Method Delete -Uri "https://api.line.me/v2/bot/richmenu/$($item.richMenuId)" -Headers $headers | Out-Null
    }
}
"リッチメニューを設定しました: $menuId"
