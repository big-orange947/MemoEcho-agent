<#
.SYNOPSIS
    停止 Memo Echo v2(以及可选的 NapCat)。

.DESCRIPTION
    只停止本脚本启动的进程:
      - 通过 .runtime/local-processes.json 找到记录;
      - **核对进程身份**(PID + 进程名 + 启动时间)后才结束进程 ——
        避免 PID 被复用后误杀其它程序(这一点在旧版脚本里踩过坑)。

    与旧版不同: v2 只有一个进程,不需要按依赖顺序停止。

    NapCat 的安全默认:
      NapCat 是注入到 QQ 进程里运行的,所以"停止 NapCat" = **结束 QQ**。
      结束 QQ 会打断你正在用的聊天窗口,因此**默认不动 QQ**;
      只有显式指定 -StopNapCat 才会结束 QQ 进程。

.PARAMETER SkipNapCat
    兼容旧参数: 保留 NapCat 运行(等价于默认行为)。

.PARAMETER StopNapCat
    显式要求结束 NapCat(即结束所有 QQ 进程)。请确认没有正在进行的聊天。

.EXAMPLE
    .\scripts\stop-local.ps1              # 只停 v2,保留 QQ
    .\scripts\stop-local.ps1 -StopNapCat  # 连 QQ 一起结束
#>
[CmdletBinding()]
param(
    [switch]$SkipNapCat,
    [switch]$StopNapCat
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$pidFile = Join-Path $projectRoot ".runtime/local-processes.json"

function Test-ProcessIdentity {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]$Record
    )

    <# 同时核对 PID、进程名和启动时间,防止陈旧 PID 文件误杀后来复用同一 PID 的程序。 #>
    if ($Process.ProcessName -ne [string]$Record.processName) {
        return $false
    }
    $expected = [DateTime]::Parse([string]$Record.startedAtUtc).ToUniversalTime()
    $actual = $Process.StartTime.ToUniversalTime()
    return [Math]::Abs(($actual - $expected).TotalSeconds) -lt 2
}

# ---------------------------------------------------------------------------
# 停止 v2(按 PID 文件)
# ---------------------------------------------------------------------------
# 说明: Windows 上 venv 的 python.exe 是转发器,真实服务进程是它的子进程
# (见 start-local.ps1 注释)。因此记录里可能同时有"服务进程"与"启动器进程",
# 这里两个都清理,避免留下占用 8000 端口的孤儿进程。
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "[SKIP] 没有找到本脚本管理的运行记录(PID 文件不存在)。"
} else {
    [array]$records = @(Get-Content -LiteralPath $pidFile -Raw -Encoding utf8 | ConvertFrom-Json)
    foreach ($record in $records) {
        # (1) 服务进程: 先停它(释放端口),再处理启动器
        $serverProcess = Get-Process -Id ([int]$record.processId) -ErrorAction SilentlyContinue
        if ($null -eq $serverProcess) {
            Write-Host "[SKIP] $($record.name) 服务进程已退出"
        } elseif (-not (Test-ProcessIdentity -Process $serverProcess -Record $record)) {
            Write-Warning "跳过 PID $($record.processId): 进程身份与记录不一致(可能已被复用)。"
        } else {
            Stop-Process -Id $serverProcess.Id -Force
            Write-Host "[STOP] $($record.name) 服务进程 (PID=$($serverProcess.Id))"
        }

        # (2) 启动器(venv python 转发器): 若仍在则一并结束
        #     注意: 服务进程被杀后转发器通常也会退出,存在竞态 —— 用 try 兜住
        $launcherPid = 0
        if ($null -ne $record.PSObject.Properties["launcherPid"]) {
            $launcherPid = [int]$record.launcherPid
        }
        if ($launcherPid -gt 0) {
            $launcherProcess = Get-Process -Id $launcherPid -ErrorAction SilentlyContinue
            if ($null -ne $launcherProcess) {
                try {
                    Stop-Process -Id $launcherPid -Force -ErrorAction Stop
                    Write-Host "[STOP] $($record.name) 启动器 (PID=$launcherPid)"
                } catch {
                    # 进程在检查与停止之间自行退出(正常竞态),无需处理
                    Write-Host "[SKIP] $($record.name) 启动器已自行退出"
                }
            }
        }
    }
    Remove-Item -LiteralPath $pidFile -Force
}

# 等待端口释放(给 OS 一点时间回收),再做兜底检查
Start-Sleep -Milliseconds 500

# 兜底: PID 文件丢了但端口仍被占用时,提示用户(不自动杀,避免误伤)
$listening = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($listening) {
    $owner = Get-Process -Id $listening.OwningProcess -ErrorAction SilentlyContinue
    Write-Warning "端口 8000 仍被占用: $($owner.ProcessName) PID=$($listening.OwningProcess)。"
    Write-Warning "若确认是残留的 v2 进程,可手动执行: Stop-Process -Id $($listening.OwningProcess) -Force"
}

# ---------------------------------------------------------------------------
# 停止 NapCat(默认不动!)
# ---------------------------------------------------------------------------
# NapCat 注入在 QQ 进程里,停止它 = 结束 QQ,会打断用户正在用的聊天。
# 因此默认保留 QQ,只有显式 -StopNapCat 才结束。
if ($SkipNapCat -or -not $StopNapCat) {
    $qqCount = @(Get-Process -Name QQ -ErrorAction SilentlyContinue).Count
    Write-Host "[SKIP] 保留 NapCat/QQ 运行(共 $qqCount 个 QQ 进程)。如需结束请加 -StopNapCat。"
    exit 0
}

$qqProcesses = @(Get-Process -Name QQ -ErrorAction SilentlyContinue)
if ($qqProcesses.Count -gt 0) {
    # NapCat 注入 QQ 进程运行;停止 = 结束 QQ。
    # 重启请用 start-local.ps1(带 QQ 号快速登录)。
    $qqProcesses | Stop-Process -Force
    Write-Host "[STOP] NapCat / QQ ($($qqProcesses.Count) 个进程)"
} else {
    Write-Host "[SKIP] NapCat 未在运行"
}
