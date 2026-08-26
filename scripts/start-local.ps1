[CmdletBinding()]
param(
    [switch]$SkipBuild,
    [ValidateRange(10, 300)]
    [int]$StartupTimeoutSeconds = 90,
    [switch]$SkipNapCat,
    [switch]$SkipNeo4j,
    [string]$NapCatQq = "3969785168"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent $scriptRoot
$runtimeRoot = Join-Path $projectRoot ".runtime"
$logRoot = Join-Path $runtimeRoot "logs"
$pidFile = Join-Path $runtimeRoot "local-processes.json"
$localEnvironmentFile = Join-Path $scriptRoot "local-env.ps1"

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Import-LocalEnvironment {
    <# 读取被 Git 忽略的本机配置，让密码和 Token 不必写入源码或启动命令。 #>
    if (Test-Path -LiteralPath $localEnvironmentFile) {
        . $localEnvironmentFile
        Write-Host "已加载 scripts/local-env.ps1"
    }
}

function Assert-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)

    <# 确认启动链路依赖的命令存在，并返回可直接传给 Start-Process 的完整路径。 #>
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        throw "缺少命令 $Name，请先安装并加入 PATH。"
    }
    return $command.Source
}

function Assert-RequiredEnvironment {
    <# 在创建子进程前一次性检查敏感配置，避免多个服务启动一半后才因密码为空失败。 #>
    $requiredNames = @(
        "EVENT_CENTER_DB_PASSWORD",
        "SCHEDULE_DB_PASSWORD",
        "TASK_DB_PASSWORD"
    )
    $missing = @($requiredNames | Where-Object {
        [string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($_))
    })
    if ($missing.Count -gt 0) {
        throw "缺少环境变量：$($missing -join ', ')。请复制 scripts/local-env.example.ps1 为 scripts/local-env.ps1 后填写。"
    }
}

function Test-HttpHealth {
    param([Parameter(Mandatory = $true)][string]$Uri)

    <# 请求服务健康端点；只要返回 status=UP 或 status=ok，就认为服务已经可接收请求。 #>
    try {
        $response = Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 2
        $status = [string]$response.status
        return $status -ieq "UP" -or $status -ieq "ok"
    } catch {
        return $false
    }
}

function Wait-ForHealth {
    param(
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$HealthUri,
        [Parameter(Mandatory = $true)][string]$ErrorLog
    )

    <# 轮询新进程的健康端点；进程提前退出时附带错误日志，缩短启动失败的排查路径。 #>
    $deadline = (Get-Date).AddSeconds($StartupTimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            $tail = if (Test-Path -LiteralPath $ErrorLog) {
                (Get-Content -LiteralPath $ErrorLog -Tail 30 -ErrorAction SilentlyContinue) -join [Environment]::NewLine
            } else {
                "没有生成错误日志"
            }
            throw "$Name 启动进程已退出，exitCode=$($Process.ExitCode)`n$tail"
        }
        if (Test-HttpHealth -Uri $HealthUri) {
            Write-Host "[UP] $Name"
            return
        }
        Start-Sleep -Milliseconds 700
        $Process.Refresh()
    }
    throw "$Name 在 $StartupTimeoutSeconds 秒内未通过健康检查，请查看 $ErrorLog"
}

function Build-JavaService {
    param(
        [Parameter(Mandatory = $true)][string]$MavenCommand,
        [Parameter(Mandatory = $true)][string]$ServiceDirectory,
        [Parameter(Mandatory = $true)][string]$Name
    )

    <# 使用当前 Java 21 环境构建单个服务；构建失败时立即停止，不启动旧 JAR。 #>
    Write-Host "[BUILD] $Name"
    Push-Location $ServiceDirectory
    try {
        & $MavenCommand -q -DskipTests package
        if ($LASTEXITCODE -ne 0) {
            throw "$Name Maven 构建失败，exitCode=$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$ArgumentList,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$HealthUri
    )

    <# 后台启动一个项目进程，保存日志并返回包含 PID 和启动时间的安全停止凭据。 #>
    if (Test-HttpHealth -Uri $HealthUri) {
        Write-Host "[SKIP] $Name 已经在运行"
        return $null
    }

    $stdoutLog = Join-Path $logRoot "$Name.out.log"
    $stderrLog = Join-Path $logRoot "$Name.err.log"
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $ArgumentList `
        -WorkingDirectory $WorkingDirectory `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog `
        -WindowStyle Hidden `
        -PassThru

    Wait-ForHealth -Process $process -Name $Name -HealthUri $HealthUri -ErrorLog $stderrLog
    return [pscustomobject]@{
        name = $Name
        processId = $process.Id
        processName = $process.ProcessName
        startedAtUtc = $process.StartTime.ToUniversalTime().ToString("O")
        healthUri = $HealthUri
    }
}

function Save-ManagedProcesses {
    param([Parameter(Mandatory = $true)][System.Collections.IEnumerable]$Processes)

    <# 每成功启动一个进程就刷新 PID 文件，即使后续服务失败也能可靠回收已启动部分。 #>
    @($Processes) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding utf8
}

function Stop-StartedProcesses {
    param([Parameter(Mandatory = $true)][System.Collections.IEnumerable]$Processes)

    <# 启动链路失败时只回收本次脚本创建的进程，不触碰启动前已经存在的服务。 #>
    foreach ($entry in @($Processes)) {
        $process = Get-Process -Id $entry.processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

Import-LocalEnvironment
Assert-RequiredEnvironment

if (Test-Path -LiteralPath $pidFile) {
    throw "检测到已有 .runtime/local-processes.json。请先运行 scripts/status-local.ps1；需要重启时先运行 scripts/stop-local.ps1。"
}

$javaCommand = Assert-CommandAvailable -Name "java"
$mavenCommand = Assert-CommandAvailable -Name "mvn"
$pythonCommand = Assert-CommandAvailable -Name "python"

# 本机 Java 服务实际用 JDK 24 运行（D:\Java\jdk，已验证 event-center/connector 兼容）；
# 存在时优先使用，否则回退 PATH 中的 java（JDK 21 亦可）。
$preferredJdk = "D:\Java\jdk\bin\java.exe"
if (Test-Path -LiteralPath $preferredJdk) {
    $javaCommand = $preferredJdk
    Write-Host "使用本机 JDK：$preferredJdk"
}

$javaVersionOutput = (& $env:ComSpec /c "`"$javaCommand`" -version 2>&1") -join " "
if ($javaVersionOutput -notmatch 'version "(?:21|24)(?:\.|\")') {
    throw "当前 java 不是 JDK 21/24：$javaVersionOutput"
}

$javaServices = @(
    [pscustomobject]@{ name = "event-center"; directory = "event-center-service"; jar = "event-center-service-0.1.0-SNAPSHOT.jar"; health = "http://127.0.0.1:8093/actuator-like/health" },
    [pscustomobject]@{ name = "schedule-service"; directory = "schedule-service"; jar = "schedule-service-0.1.0-SNAPSHOT.jar"; health = "http://127.0.0.1:8092/actuator-like/health" },
    [pscustomobject]@{ name = "task-service"; directory = "task-service"; jar = "task-service-0.1.0-SNAPSHOT.jar"; health = "http://127.0.0.1:8094/actuator-like/health" },
    [pscustomobject]@{ name = "qq-connector"; directory = "qq-napcat-connector"; jar = "qq-napcat-connector-0.1.0-SNAPSHOT.jar"; health = "http://127.0.0.1:8091/actuator-like/health" }
)

if (-not $SkipBuild) {
    foreach ($service in $javaServices) {
        $serviceDirectory = Join-Path $projectRoot "services-java/$($service.directory)"
        Build-JavaService -MavenCommand $mavenCommand -ServiceDirectory $serviceDirectory -Name $service.name
    }
}

$managedProcesses = [System.Collections.ArrayList]::new()
try {
    # ---------------------------------------------------------------- Neo4j（记忆图谱）
    if (-not $SkipNeo4j) {
        $neo4jListen = Get-NetTCPConnection -LocalPort 7687 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $neo4jListen) {
            Write-Host "[SKIP] Neo4j 已经在运行"
        } else {
            $dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
            if ($null -eq $dockerCommand) {
                Write-Warning "Neo4j 未运行且未找到 docker，请手动：docker compose -f scripts/docker-compose.neo4j.yml up -d"
            } else {
                Write-Host "[START] Neo4j (docker compose)"
                & $dockerCommand.Source compose -f (Join-Path $scriptRoot "docker-compose.neo4j.yml") up -d 2>&1 | Out-Null
                $neo4jReady = $false
                $deadline = (Get-Date).AddSeconds(120)
                while ((Get-Date) -lt $deadline) {
                    Start-Sleep -Seconds 3
                    $port = Get-NetTCPConnection -LocalPort 7687 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($null -ne $port) { $neo4jReady = $true; break }
                }
                if ($neo4jReady) { Write-Host "[UP] Neo4j" } else { Write-Warning "Neo4j 容器启动超时，请检查 Docker Desktop" }
            }
        }
    }

    foreach ($service in $javaServices | Where-Object { $_.name -ne "qq-connector" }) {
        $serviceDirectory = Join-Path $projectRoot "services-java/$($service.directory)"
        $jarPath = Join-Path $serviceDirectory "target/$($service.jar)"
        if (-not (Test-Path -LiteralPath $jarPath)) {
            throw "找不到 $jarPath，请移除 -SkipBuild 后重试。"
        }
        $entry = Start-ManagedProcess -Name $service.name -FilePath $javaCommand -ArgumentList @("-jar", $jarPath) -WorkingDirectory $serviceDirectory -HealthUri $service.health
        if ($null -ne $entry) {
            [void]$managedProcesses.Add($entry)
            Save-ManagedProcesses -Processes $managedProcesses
        }
    }

    $pythonDirectory = Join-Path $projectRoot "agent-runtime-python"
    # 优先使用 uv 管理的虚拟环境（项目实际依赖安装在其中）；没有 uv 时回退到系统 python。
    $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uvCommand) {
        $pythonEntry = Start-ManagedProcess -Name "agent-runtime" -FilePath $uvCommand.Source -ArgumentList @("run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $pythonDirectory -HealthUri "http://127.0.0.1:8000/health"
    } else {
        $pythonEntry = Start-ManagedProcess -Name "agent-runtime" -FilePath $pythonCommand -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000") -WorkingDirectory $pythonDirectory -HealthUri "http://127.0.0.1:8000/health"
    }
    if ($null -ne $pythonEntry) {
        [void]$managedProcesses.Add($pythonEntry)
        Save-ManagedProcesses -Processes $managedProcesses
    }

    $connector = $javaServices | Where-Object { $_.name -eq "qq-connector" }
    $connectorDirectory = Join-Path $projectRoot "services-java/$($connector.directory)"
    $connectorJar = Join-Path $connectorDirectory "target/$($connector.jar)"
    if (-not (Test-Path -LiteralPath $connectorJar)) {
        throw "找不到 $connectorJar，请移除 -SkipBuild 后重试。"
    }
    $connectorEntry = Start-ManagedProcess -Name $connector.name -FilePath $javaCommand -ArgumentList @("-jar", $connectorJar) -WorkingDirectory $connectorDirectory -HealthUri $connector.health
    if ($null -ne $connectorEntry) {
        [void]$managedProcesses.Add($connectorEntry)
        Save-ManagedProcesses -Processes $managedProcesses
    }

    # ---------------------------------------------------------------- NapCat（QQ 机器人，快速登录）
    if (-not $SkipNapCat) {
        $napcatListen = Get-NetTCPConnection -LocalPort 3011 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -ne $napcatListen) {
            Write-Host "[SKIP] NapCat 已经在运行"
        } else {
            $launcher = "D:\napcat\launcher-user.bat"
            if (-not (Test-Path -LiteralPath $launcher)) {
                Write-Warning "未找到 $launcher，请手动启动 NapCat（带 QQ 号参数可快速登录）"
            } else {
                Write-Host "[START] NapCat (快速登录 QQ=$NapCatQq)"
                $napcatLog = Join-Path $env:TEMP "napcat-console.log"
                # 传 QQ 号参数 = 快速登录（免扫码）；不传参则每次需要重新扫码。
                Start-Process -FilePath $env:ComSpec -ArgumentList @("/c", "chcp 65001 >nul && cd /d D:\napcat && launcher-user.bat $NapCatQq > `"$napcatLog`" 2>&1") -WindowStyle Hidden
                $napcatReady = $false
                $deadline = (Get-Date).AddSeconds(90)
                while ((Get-Date) -lt $deadline) {
                    Start-Sleep -Seconds 5
                    $port = Get-NetTCPConnection -LocalPort 3011 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($null -ne $port) {
                        try {
                            $login = Invoke-RestMethod -Uri "http://127.0.0.1:3011/get_login_info" -TimeoutSec 3
                            if ($null -ne $login.data.user_id) {
                                Write-Host "[UP] NapCat 已快速登录：$($login.data.user_id) / $($login.data.nickname)"
                                $napcatReady = $true
                                break
                            }
                        } catch { }
                    }
                }
                if (-not $napcatReady) {
                    Write-Warning "NapCat 3011 已监听但快速登录未确认，可能仍需扫码（查看 $napcatLog）；本脚本会继续。"
                }
            }
        }
    }

    Write-Host "Memo Echo 本地服务已就绪。日志目录：$logRoot"
    & (Join-Path $scriptRoot "status-local.ps1")
} catch {
    Stop-StartedProcesses -Processes $managedProcesses
    Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
    throw
}
