<#
.SYNOPSIS
    启动 Memo Echo v2(v2-python-rewrite 分支: Python + LangGraph 对话助手)。

.DESCRIPTION
    与旧版(start-local.ps1 启动 5 个 Java 服务)不同, v2 是**单进程**:
      - memo-echo: FastAPI + LangGraph + 调度器,一个进程全部搞定。

    可选一并启动 NapCat(QQ 协议端):
      - 用 -SkipNapCat 跳过;或直接不带该开关,由脚本尝试拉起。

    过程:
      1. 检查依赖(.venv / .env / 端口占用)
      2. 后台启动 v2 进程,并把 stdout/stderr 写入 .runtime/logs/
      3. 轮询健康端点,确认真的起来了(而不是"进程在但服务不可用")
      4. (可选)启动 NapCat,并做一次配置检查

.PARAMETER SkipNapCat
    不启动 NapCat(只跑 v2,适合纯本机/桌面端开发)。

.PARAMETER NapCatQq
    NapCat 快速登录使用的 QQ 号(默认 3969785168,与已保存的 NapCat 配置对应)。

.PARAMETER StartupTimeoutSeconds
    健康检查等待上限(默认 60 秒;首次启动会装依赖,可适当加大)。

.EXAMPLE
    .\scripts\start-local.ps1
    .\scripts\start-local.ps1 -SkipNapCat
#>
[CmdletBinding()]
param(
    [switch]$SkipNapCat,
    [string]$NapCatQq = "3969785168",
    [ValidateRange(10, 600)]
    [int]$StartupTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$runtimeRoot = Join-Path $projectRoot ".runtime"
$logRoot = Join-Path $runtimeRoot "logs"
$pidFile = Join-Path $runtimeRoot "local-processes.json"
$venvPython = Join-Path $projectRoot ".venv/Scripts/python.exe"
$envFile = Join-Path $projectRoot ".env"
$napCatDir = "D:\napcat"

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Write-Step {
    param([string]$Message)
    Write-Host "[memo-echo] $Message"
}

# ---------------------------------------------------------------------------
# 预检查
# ---------------------------------------------------------------------------
function Assert-Prerequisites {
    <# 启动前把"缺啥"一次性说清楚,避免起一半才失败。 #>
    if (-not (Test-Path -LiteralPath $venvPython)) {
        throw "找不到虚拟环境: $venvPython`n请先运行: uv venv; uv pip install -e ."
    }
    if (-not (Test-Path -LiteralPath $envFile)) {
        throw "找不到配置文件: $envFile`n请复制 .env 模板并填入 OPENAI_API_KEY / OPENAI_BASE_URL。"
    }

    # 端口占用检查: 8000 是 v2 的 API 端口
    $listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
    if ($listening) {
        $owner = Get-Process -Id $listening.OwningProcess -ErrorAction SilentlyContinue
        throw "端口 8000 已被占用(进程 $($owner.ProcessName) PID=$($listening.OwningProcess))。`n若确认是残留的 v2 进程,请运行: .\scripts\stop-local.ps1"
    }
}

function Test-V2Health {
    <# 健康检查: /api/conversations 返回 200 即认为服务可用。 #>
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/conversations" `
            -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -eq 200
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# 启动 v2
# ---------------------------------------------------------------------------
Assert-Prerequisites

$stdoutLog = Join-Path $logRoot "memo-echo.out.log"
$stderrLog = Join-Path $logRoot "memo-echo.err.log"

Write-Step "启动 v2 服务..."
# 为什么直接用 venv 的 python 而不是 `uv run`:
#   - uv run 会再套一层进程,停止时容易留下孤儿 python。
# 注意: Windows 上 venv 的 python.exe 其实是个"转发器",真正的解释器是
#   它启动的子进程(如 D:\anna\python.exe)。因此记录 PID 时必须取
#   **实际监听 8000 的进程**,否则停止脚本会杀错对象、留下占端口的孩子。
# 工作目录必须是项目根 —— config.py 从当前目录读 .env 与 data/。
$launcher = Start-Process -FilePath $venvPython `
    -ArgumentList "-m", "app.main", "serve" `
    -WorkingDirectory $projectRoot `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru -WindowStyle Hidden

# ---------------------------------------------------------------------------
# 等待健康(并识别真实服务进程)
# ---------------------------------------------------------------------------
Write-Step "等待服务就绪(最多 $StartupTimeoutSeconds 秒)..."
$deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
$healthy = $false
$serverPid = $null
while ((Get-Date) -lt $deadline) {
    if ($launcher.HasExited) {
        $tail = if (Test-Path -LiteralPath $stderrLog) {
            (Get-Content -LiteralPath $stderrLog -Tail 30 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
        } else { "(无错误日志)" }
        throw "v2 进程已退出(exitCode=$($launcher.ExitCode)):`n$tail"
    }
    if (Test-V2Health) {
        $healthy = $true
        # 谁是真正在监听 8000 的进程(即真正的服务进程)
        $listener = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($listener) { $serverPid = [int]$listener.OwningProcess }
        break
    }
    Start-Sleep -Milliseconds 700
    $launcher.Refresh()
}

if (-not $healthy) {
    throw "v2 未在 $StartupTimeoutSeconds 秒内就绪。查看日志: $stderrLog"
}
if (-not $serverPid) {
    # 理论上不该发生(健康检查已通过);退化为记录启动器 PID
    Write-Warning "未能识别监听 8000 的进程,将记录启动器 PID。"
    $serverPid = $launcher.Id
}

# 记录"真实服务进程"的身份,停止脚本据此核对后结束进程。
# 同时保留 launcherPid: 若转发器进程还在,一并清理,避免残留。
$serverProcess = Get-Process -Id $serverPid -ErrorAction SilentlyContinue
$record = [pscustomobject]@{
    name          = "memo-echo"
    processId     = $serverPid
    processName   = if ($serverProcess) { $serverProcess.ProcessName } else { "python" }
    startedAtUtc  = if ($serverProcess) { $serverProcess.StartTime.ToUniversalTime().ToString("o") } else { "" }
    launcherPid   = $launcher.Id
    launcherName  = $launcher.ProcessName
    workingDir    = $projectRoot
}
@($record) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding utf8

Write-Step "[UP] v2 API: http://127.0.0.1:8000  (服务进程 PID=$serverPid, 启动器 PID=$($launcher.Id))"
Write-Step "     日志: $stdoutLog / $stderrLog"

# ---------------------------------------------------------------------------
# NapCat(可选)
# ---------------------------------------------------------------------------
if ($SkipNapCat) {
    Write-Step "已跳过 NapCat(-SkipNapCat)。"
    exit 0
}

$launcher = Join-Path $napCatDir "launcher-user.bat"
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Warning "找不到 NapCat 启动器: $launcher —— 跳过 NapCat 启动。v2 仍在运行(桌面端可用)。"
    exit 0
}

# 判断 NapCat 是否真的在工作: 不能只看"QQ 进程存不存在" ——
# QQ 可能是普通方式启动的(没有注入 NapCat),那样 3011 不会响应,
# 此时必须提示用户重启 QQ 加载 NapCat,否则消息根本进不来。
function Test-NapCatReady {
    try {
        $resp = Invoke-RestMethod -Uri "http://127.0.0.1:3011/get_login_info" -Method Post `
            -Body "{}" -ContentType "application/json" -TimeoutSec 3
        return ($resp.retcode -eq 0)
    } catch {
        return $false
    }
}

if (Test-NapCatReady) {
    Write-Step "NapCat 已就绪(OneBot 接口可用),无需重启。"
} else {
    $qqProcesses = @(Get-Process -Name QQ -ErrorAction SilentlyContinue)
    if ($qqProcesses.Count -gt 0) {
        # QQ 在运行但 NapCat 接口不可用 → QQ 是普通模式启动的,需要重启才能注入 NapCat
        Write-Warning "检测到 QQ 正在运行,但 NapCat 未注入(3011 无响应)。"
        Write-Warning "NapCat 必须由注入方式启动。请手动执行(会重启 QQ):"
        Write-Warning "  1) .\scripts\stop-local.ps1 -StopNapCat"
        Write-Warning "  2) 重新运行本脚本,或直接执行 $launcher $NapCatQq"
        Write-Step "v2 已就绪;桌面端与 API 可用,QQ 通道待 NapCat 就绪。"
        exit 0
    }

    Write-Step "启动 NapCat(QQ $NapCatQq,快速登录)..."
    Start-Process -FilePath $launcher -ArgumentList $NapCatQq `
        -WorkingDirectory $napCatDir -WindowStyle Hidden

    # 轮询等待 NapCat 注入完成(QQ 启动 + 登录 + 注入需要时间)
    Write-Step "     已拉起,等待 OneBot 接口就绪(最多 45 秒)..."
    $napDeadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $napDeadline) {
        if (Test-NapCatReady) {
            Write-Step "     [UP] NapCat 就绪"
            break
        }
        Start-Sleep -Milliseconds 1500
    }
    if (-not (Test-NapCatReady)) {
        Write-Warning "NapCat 在 45 秒内未就绪(可能需要扫码登录,或 QQ 版本不兼容)。"
        Write-Warning "可稍后运行 .\scripts\status-local.ps1 复查。"
    }
}

# 配置检查: 确认 NapCat 事件确实会推给 v2
$configCheck = Join-Path $scriptRoot "check_napcat_config.py"
if (Test-Path -LiteralPath $configCheck) {
    Write-Step "检查 NapCat 配置..."
    & $venvPython $configCheck
}

Write-Step "完成。验证方式: 用另一个 QQ 给机器人发消息,应收到回复。"
