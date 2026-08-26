[CmdletBinding()]
param(
    [switch]$SkipNapCat
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

    <# 同时核对 PID、进程名和启动时间，防止陈旧 PID 文件误杀后来复用同一 PID 的其他程序。 #>
    if ($Process.ProcessName -ne [string]$Record.processName) {
        return $false
    }
    $expected = [DateTime]::Parse([string]$Record.startedAtUtc).ToUniversalTime()
    $actual = $Process.StartTime.ToUniversalTime()
    return [Math]::Abs(($actual - $expected).TotalSeconds) -lt 2
}

if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "没有找到本脚本管理的运行进程。"
    exit 0
}

[array]$records = @(Get-Content -LiteralPath $pidFile -Raw -Encoding utf8 | ConvertFrom-Json)
# 按启动顺序的逆序停止，让最上游的 Connector 先退出，减少关闭期间的新事件进入。
[array]::Reverse($records)
foreach ($record in $records) {
    $process = Get-Process -Id ([int]$record.processId) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Host "[SKIP] $($record.name) 已退出"
        continue
    }
    if (-not (Test-ProcessIdentity -Process $process -Record $record)) {
        Write-Warning "跳过 PID $($record.processId)：进程身份与记录不一致。"
        continue
    }
    Stop-Process -Id $process.Id -Force
    Write-Host "[STOP] $($record.name)"
}

Remove-Item -LiteralPath $pidFile -Force

if (-not $SkipNapCat) {
    $qqProcesses = @(Get-Process -Name QQ -ErrorAction SilentlyContinue)
    if ($qqProcesses.Count -gt 0) {
        # NapCat 注入 QQ 进程运行；停止 = 结束 QQ。重启后请用 start-local.ps1（带 QQ 号参数快速登录）。
        $qqProcesses | Stop-Process -Force
        Write-Host "[STOP] NapCat (QQ $($qqProcesses.Count) 个进程)"
    } else {
        Write-Host "[SKIP] NapCat 未在运行"
    }
}

Write-Host "本脚本启动的 Memo Echo 服务已停止；Neo4j/MySQL 未受影响。用 scripts/start-local.ps1 一键重启（NapCat 自动快速登录）。"
