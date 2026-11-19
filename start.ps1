# Daily start: refresh Upstox token if needed, refresh the NSE bond list, run the app.
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"

& $py -m app.upstox_auth
if ($LASTEXITCODE -ne 0) { Write-Warning "Upstox login failed - starting without live quotes." }

& $py -m app.sources.nse_universe
if ($LASTEXITCODE -ne 0) { Write-Warning "Universe refresh failed - using the existing data\bonds.csv." }

& $py -m uvicorn app.main:app --port 8000
