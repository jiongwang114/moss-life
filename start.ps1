$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python environment creation failed' }
}
& '.\.venv\Scripts\python.exe' -m pip install -r backend\requirements.txt --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
if (-not $env:MOSS_SEARCH_ENABLED) { $env:MOSS_SEARCH_ENABLED = '1' }
if ($env:MOSS_SEARCH_ENABLED -eq '1') {
    if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Smart search requires Node.js 22+. Set MOSS_SEARCH_ENABLED=0 to run recording only.' }
    if (-not (Test-Path -LiteralPath 'smart-search/node_modules/@huggingface/transformers/package.json')) {
        $env:ONNXRUNTIME_NODE_INSTALL_CUDA = 'skip'
        Push-Location smart-search
        try { npm ci --no-audit --no-fund; if ($LASTEXITCODE -ne 0) { throw 'Search dependencies failed to install' } }
        finally { Pop-Location }
    }
}
Write-Host 'UI: http://127.0.0.1:8000/  |  Mock: http://127.0.0.1:8000/?api=mock'
& '.\.venv\Scripts\python.exe' -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
