param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonExe = $env:CONNECTED_BUILDING_PYTHON
if (-not $pythonExe) {
    $pythonExe = Join-Path $env:USERPROFILE '.codex\environments\city-connected-building-20261018-v1\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Python environment not found. Follow the README setup steps or set CONNECTED_BUILDING_PYTHON to your Python executable.'
}
if ($Port -lt 1024 -or $Port -gt 55417) { throw 'Choose an HTTP port between 1024 and 55417; the local MQTT port is HTTP port plus 10118.' }
$url = "http://127.0.0.1:$Port/"
$listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($listener) {
    try {
        $health = Invoke-RestMethod -Uri ($url + 'health') -TimeoutSec 3
        if ($health.build -eq '20261018-v1') {
            Write-Host "Connected Building is already running at $url"
            if (-not $NoBrowser) { Start-Process $url }
            exit 0
        }
    } catch { }
    throw "Port $Port is in use by another service. Choose a different port."
}
$stateRoot = Join-Path $env:LOCALAPPDATA 'Generate digital-twin skill gaps PostDoc@city\20261018_v1_connected_building'
$logRoot = Join-Path $stateRoot 'logs'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$env:CONNECTED_BUILDING_DATA_DIR = $stateRoot
$env:TWIN_RUNTIME_DIR = Join-Path $stateRoot 'transport'
$env:TWIN_GEOMETRY_PATH = Join-Path $stateRoot '20261018_v1_geometry.json'
$env:TWIN_ANALYTICS_PATH = Join-Path $stateRoot '20261018_v1_temperature_forecast.json'
$env:TWIN_MQTT_PORT = [string]($Port + 10118)
$launchStamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$outLog = Join-Path $logRoot ($launchStamp + '_v1_server_stdout.log')
$errLog = Join-Path $logRoot ($launchStamp + '_v1_server_stderr.log')
$serverProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m', 'uvicorn', 'twin.main:app', '--host', '127.0.0.1', '--port', [string]$Port) -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
$ready = $false
for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Milliseconds 500
    if ($serverProcess.HasExited) { break }
    try {
        $health = Invoke-RestMethod -Uri ($url + 'health') -TimeoutSec 1
        if ($health.build -eq '20261018-v1') { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    throw "The service did not become ready. Check $errLog"
}
@{ process_id = $serverProcess.Id; url = $url; project_path = $projectRoot; executable = $pythonExe; started_at = (Get-Date).ToUniversalTime().ToString('o'); stdout = $outLog; stderr = $errLog } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $stateRoot '20261018_v1_Server_Record.json') -Encoding utf8
Write-Host "Connected Building is ready at $url"
Write-Host "Persistent data and logs: $stateRoot"
if (-not $NoBrowser) { Start-Process $url }
