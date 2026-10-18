$ErrorActionPreference = 'Stop'
$recordPath = Join-Path $env:LOCALAPPDATA 'Generate digital-twin skill gaps PostDoc@city\20261018_v1_connected_building\20261018_v1_Server_Record.json'
if (-not (Test-Path -LiteralPath $recordPath)) { Write-Host 'No launcher record was found.'; exit 0 }
$record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
$candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.process_id)"
if (-not $candidate) { Write-Host 'The recorded service has already stopped.'; exit 0 }
if ($candidate.CommandLine -notmatch 'uvicorn twin.main:app' -or $candidate.CommandLine -notmatch '--host 127.0.0.1') {
    throw 'The recorded process no longer matches Connected Building. It has not been stopped.'
}
$health = Invoke-RestMethod -Uri ($record.url + 'health') -TimeoutSec 3
if ($health.build -ne '20261018-v1') { throw 'The listener is not the expected application.' }
Stop-Process -Id $record.process_id
Write-Host 'Connected Building has stopped. Published source files and stored data remain available.'
