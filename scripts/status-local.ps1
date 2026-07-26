[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-HttpHealth {
    param([Parameter(Mandatory = $true)][string]$Uri)

    <# 返回健康端点是否可访问，不把单个服务离线提升为脚本异常。 #>
    try {
        $response = Invoke-RestMethod -Method Get -Uri $Uri -TimeoutSec 2
        $status = [string]$response.status
        return $status -ieq "UP" -or $status -ieq "ok"
    } catch {
        return $false
    }
}

function Test-TcpPort {
    param(
        [Parameter(Mandatory = $true)][string]$HostName,
        [Parameter(Mandatory = $true)][int]$Port
    )

    <# 轻量探测没有 HTTP 健康端点的 MySQL 和 NapCat，不发送业务请求。 #>
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $task = $client.ConnectAsync($HostName, $Port)
        return $task.Wait(1200) -and $client.Connected
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

$services = @(
    [pscustomobject]@{ name = "MySQL"; endpoint = "127.0.0.1:3306"; online = (Test-TcpPort -HostName "127.0.0.1" -Port 3306) },
    [pscustomobject]@{ name = "NapCat HTTP"; endpoint = "127.0.0.1:3011"; online = (Test-TcpPort -HostName "127.0.0.1" -Port 3011) },
    [pscustomobject]@{ name = "Event Center"; endpoint = "127.0.0.1:8093"; online = (Test-HttpHealth -Uri "http://127.0.0.1:8093/actuator-like/health") },
    [pscustomobject]@{ name = "Schedule Service"; endpoint = "127.0.0.1:8092"; online = (Test-HttpHealth -Uri "http://127.0.0.1:8092/actuator-like/health") },
    [pscustomobject]@{ name = "Task Service"; endpoint = "127.0.0.1:8094"; online = (Test-HttpHealth -Uri "http://127.0.0.1:8094/actuator-like/health") },
    [pscustomobject]@{ name = "Agent Runtime"; endpoint = "127.0.0.1:8000"; online = (Test-HttpHealth -Uri "http://127.0.0.1:8000/health") },
    [pscustomobject]@{ name = "QQ Connector"; endpoint = "127.0.0.1:8091"; online = (Test-HttpHealth -Uri "http://127.0.0.1:8091/actuator-like/health") }
)

$services |
    Select-Object Name, Endpoint, @{Name = "Status"; Expression = { if ($_.online) { "UP" } else { "DOWN" } } } |
    Format-Table -AutoSize
