<#
.SYNOPSIS
    查看 Memo Echo v2 的运行状态(服务 / NapCat / 数据概况)。

.DESCRIPTION
    一次性回答"现在到底什么情况":
      1. v2 进程与 API 是否可用;
      2. NapCat 是否在线(能否调用 OneBot 接口);
      3. NapCat 上报配置是否会推给 v2;
      4. 数据库概况(会话数、消息数、待触发定时唤醒、进行中的目标)。

.EXAMPLE
    .\scripts\status-local.ps1
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"   # 状态脚本要"尽量多报",不该因某一项失败而中断

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$venvPython = Join-Path $projectRoot ".venv/Scripts/python.exe"
$pidFile = Join-Path $projectRoot ".runtime/local-processes.json"

function Write-Section {
    param([string]$Title)
    Write-Host ""
    Write-Host "== $Title =="
}

# ---------------------------------------------------------------------------
# 1. v2 服务
# ---------------------------------------------------------------------------
Write-Section "Memo Echo v2"

if (Test-Path -LiteralPath $pidFile) {
    $records = @(Get-Content -LiteralPath $pidFile -Raw -Encoding utf8 | ConvertFrom-Json)
    foreach ($record in $records) {
        $process = Get-Process -Id ([int]$record.processId) -ErrorAction SilentlyContinue
        if ($process) {
            $uptime = (Get-Date).ToUniversalTime() - $process.StartTime.ToUniversalTime()
            Write-Host ("  进程: PID={0} 已运行 {1:hh\:mm\:ss}" -f $process.Id, $uptime)
        } else {
            Write-Host "  进程: 记录中的 PID $($record.processId) 已不存在(PID 文件可能过期)"
        }
    }
} else {
    Write-Host "  进程: 没有启动记录(.runtime/local-processes.json 不存在)"
}

$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
Write-Host "  端口 8000: $(if ($listening) { '监听中' } else { '未监听' })"

$apiOk = $false
try {
    $convs = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/conversations" -TimeoutSec 3
    Write-Host "  API: 可用(会话数 $($convs.Count))"
    $apiOk = $true
} catch {
    Write-Host "  API: 不可用($($_.Exception.Message))"
}

# ---------------------------------------------------------------------------
# 2. NapCat
# ---------------------------------------------------------------------------
Write-Section "NapCat (QQ 协议端)"

$qqProcesses = @(Get-Process -Name QQ -ErrorAction SilentlyContinue)
Write-Host "  QQ 进程: $($qqProcesses.Count) 个"

try {
    $login = Invoke-RestMethod -Uri "http://127.0.0.1:3011/get_login_info" -Method Post `
        -Body "{}" -ContentType "application/json" -TimeoutSec 3
    if ($login.retcode -eq 0) {
        Write-Host "  OneBot 接口: 可用(QQ $($login.data.user_id) / $($login.data.nickname))"
    } else {
        Write-Host "  OneBot 接口: 返回异常 retcode=$($login.retcode)"
    }
} catch {
    Write-Host "  OneBot 接口: 不可用(端口 3011 无响应)"
}

# 配置检查(确认事件会推给 v2)
$configCheck = Join-Path $scriptRoot "check_napcat_config.py"
if ((Test-Path -LiteralPath $configCheck) -and (Test-Path -LiteralPath $venvPython)) {
    Write-Host "  ---- 上报配置 ----"
    & $venvPython $configCheck 2>&1 | ForEach-Object { "  $_" }
}

# ---------------------------------------------------------------------------
# 3. 数据概况
# ---------------------------------------------------------------------------
if ($apiOk -and (Test-Path -LiteralPath $venvPython)) {
    Write-Section "数据概况"
    # 用 Python 读 SQLite(SQLite 文件锁 + 中文编码在 PowerShell 里都不好用)
    $probe = Join-Path $projectRoot ".runtime/_status_probe.py"
    @'
import io, sqlite3, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
try:
    conn = sqlite3.connect("data/memo-echo.db")
    for table in ("conversations", "messages", "goals", "scheduled_events", "events"):
        n = conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
        print("  %s: %d" % (table, n))
    pending = conn.execute("SELECT COUNT(*) FROM scheduled_events WHERE status='pending'").fetchone()[0]
    print("  待触发定时唤醒: %d" % pending)
    active = conn.execute("SELECT COUNT(*) FROM goals WHERE status='active'").fetchone()[0]
    print("  进行中的目标: %d" % active)
    conn.close()
except Exception as exc:
    print("  (读取数据库失败: %s)" % exc)
'@ | Set-Content -LiteralPath $probe -Encoding utf8
    & $venvPython $probe 2>&1 | ForEach-Object { $_ }
    Remove-Item -LiteralPath $probe -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host "提示: 事件排障可查 http://127.0.0.1:8000/api/events?limit=20"
