$ErrorActionPreference = "Stop"

if (-not (Test-Path ".venv")) {
    python -m venv .venv
}

$python = Join-Path $PWD ".venv\Scripts\python.exe"
$devui = Join-Path $PWD ".venv\Scripts\devui.exe"

& $python -m pip install --upgrade pip
& $python -m pip install --pre -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env. Add your OPENROUTER_API_KEY, then run .\run.ps1 again."
    exit 0
}

& $devui .\entities --reload --instrumentation
