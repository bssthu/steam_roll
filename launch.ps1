param([switch]$NoBrowser, [switch]$Stop, [int]$Port = 17863)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$baseUrl = "http://127.0.0.1:$Port"
$runtimePath = Join-Path $projectPath '.runtime'

function Get-RollHealth {
    try {
        return Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2 -Proxy $null
    } catch { return $null }
}

function Confirm-OurService($health) {
    return $health -and $health.app -eq 'steam-roll' -and $health.version -eq 1 -and $health.project.TrimEnd('\', '/') -ieq $projectPath.TrimEnd('\', '/')
}

$launchMutex = New-Object System.Threading.Mutex($false, "Local\SteamRoll.Launch.$Port")
$hasMutex = $false
try {
    try { $hasMutex = $launchMutex.WaitOne(20000) } catch [System.Threading.AbandonedMutexException] { $hasMutex = $true }
    if (-not $hasMutex) { throw 'Another Steam Roll launcher is still starting. Try again in a moment.' }
    $health = Get-RollHealth
    if ($health -and -not (Confirm-OurService $health)) { throw "Port $Port belongs to another application or another checkout. No process was stopped." }
    if ($Stop) {
        if ($health) {
            Invoke-RestMethod -Uri "$baseUrl/api/shutdown" -Method Post -Headers @{'X-Steam-Roll-Token' = $health.token} -TimeoutSec 5 -Proxy $null | Out-Null
            Write-Host 'Steam Roll stopped.'
        } else { Write-Host 'Steam Roll is not running.' }
        exit 0
    }
    if (-not $health) {
        # Probe before starting; never kill another application using this port.
        $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $Port)
        $probe.Server.ExclusiveAddressUse = $true
        try { $probe.Start() } catch { throw "Port $Port is occupied. Steam Roll did not start or stop any other application." } finally { $probe.Stop() }
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        $pythonLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        $pythonPath = $null
        if ($pythonCommand) {
            $candidate = & $pythonCommand.Source -B -c 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) { $pythonPath = [string]$candidate }
        }
        if (-not $pythonPath -and $pythonLauncher) {
            $candidate = & $pythonLauncher.Source -3 -B -c 'import sys; assert sys.version_info >= (3, 10); print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $candidate) { $pythonPath = [string]$candidate }
        }
        if (-not $pythonPath) { throw 'Python 3.10+ is required. Install Python and enable Add Python to PATH, then run this script again.' }
        $hiddenPython = Join-Path (Split-Path $pythonPath) 'pythonw.exe'
        if (Test-Path -LiteralPath $hiddenPython) { $pythonPath = $hiddenPython }
        New-Item -ItemType Directory -Path $runtimePath -Force | Out-Null
        $errorLog = Join-Path $runtimePath "server-$Port-error.log"
        $outputLog = Join-Path $runtimePath "server-$Port.log"
        $serverScript = Join-Path $projectPath 'app.py'
        $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-B', ('"' + $serverScript + '"'), '--port', "$Port") -WorkingDirectory $projectPath -WindowStyle Hidden -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -PassThru
        for ($attempt = 0; $attempt -lt 60; $attempt++) {
            Start-Sleep -Milliseconds 250
            $health = Get-RollHealth
            if (Confirm-OurService $health) { break }
            if ($serverProcess.HasExited) {
                $details = Get-Content -LiteralPath $errorLog -Raw -ErrorAction SilentlyContinue
                throw "Steam Roll could not start. $details"
            }
        }
        if (-not (Confirm-OurService $health)) { throw "Service did not become ready. See $errorLog" }
        Write-Host "Steam Roll started. PID: $($health.pid)"
    } else { Write-Host "Reusing Steam Roll. PID: $($health.pid)" }
    if (-not $NoBrowser) { Start-Process $baseUrl }
    Write-Host $baseUrl
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
} finally {
    if ($hasMutex) { $launchMutex.ReleaseMutex() }
    $launchMutex.Dispose()
}
