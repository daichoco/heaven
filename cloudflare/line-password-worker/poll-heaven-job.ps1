$ErrorActionPreference = 'Stop'
$env:Path = ([Environment]::GetEnvironmentVariable('Path', 'Machine'), [Environment]::GetEnvironmentVariable('Path', 'User'), $env:Path) -join ';'
foreach ($name in 'LINE_CHANNEL_TOKEN', 'LINE_USER_ID', 'PASSWORD_WORKER_URL', 'PASSWORD_WORKER_TOKEN') {
    $value = [Environment]::GetEnvironmentVariable($name, 'User')
    if ($value) { Set-Item -Path "Env:$name" -Value $value }
}
if (-not $env:PASSWORD_WORKER_URL -or -not $env:PASSWORD_WORKER_TOKEN) { exit 0 }

$LogDir = Join-Path $env:LOCALAPPDATA 'HeavenScheduledReports\logs'
$Runner = 'C:\automation\run-heaven.ps1'

function Send-Line([string]$Text) {
    if (-not $env:LINE_CHANNEL_TOKEN -or -not $env:LINE_USER_ID) { return }
    $body = @{ to = $env:LINE_USER_ID; messages = @(@{ type = 'text'; text = $Text }) } | ConvertTo-Json -Depth 5
    Invoke-RestMethod -Method Post -Uri 'https://api.line.me/v2/bot/message/push' `
        -Headers @{ Authorization = "Bearer $env:LINE_CHANNEL_TOKEN" } `
        -ContentType 'application/json; charset=utf-8' `
        -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
}

function Test-RunnerBusy {
    try {
        $stream = [IO.File]::Open((Join-Path $LogDir 'runner.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $stream.Dispose()
        return $false
    } catch {
        return $true
    }
}

$response = Invoke-WebRequest -UseBasicParsing -Method Post -Uri ($env:PASSWORD_WORKER_URL.TrimEnd('/') + '/job') `
    -Headers @{ Authorization = "Bearer $env:PASSWORD_WORKER_TOKEN" } -TimeoutSec 30
if ($response.StatusCode -ne 200) { exit 0 }
$job = ([Text.Encoding]::UTF8.GetString($response.RawContentStream.ToArray()) | ConvertFrom-Json).job

if ($job -eq 'status') {
    $last = Get-ChildItem $LogDir -Filter 'run-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $busy = Test-RunnerBusy
    if ($busy) {
        Send-Line '状態: 実行中です。'
    } elseif ($last) {
        $failed = [bool](Select-String -Path $last.FullName -Pattern '終了エラー' -Quiet)
        $result = if ($failed) { '失敗' } else { '成功' }
        Send-Line ("状態: 待機中。最終実行 {0:MM/dd HH:mm} ({1})" -f $last.LastWriteTime, $result)
    } else {
        Send-Line '状態: 待機中。実行履歴はありません。'
    }
} elseif ($job -eq 'run') {
    if (Test-RunnerBusy) {
        Send-Line '更新はすでに実行中のため、新しくは開始しません。'
        exit 0
    }
    Send-Line '更新を開始しました。'
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File $Runner -IncludeCityHeavenCalendarAndWeekly
}
