# Comprehensive one-shot bootstrap for X-Automation on Windows.
#
# Does, in order:
#   1. Verify repo root (this script lives at <repo>/scripts/boot.ps1)
#   2. Create .venv if missing
#   3. Upgrade pip + install requirements
#   4. Ensure .env exists (copy from .env.example if not)
#   5. Run scripts/verify_setup.py (Phase 0 X API Skills)
#   6. Run scripts/auth_setup.py if no OAuth tokens are saved yet
#      (interactive: opens browser, captures the redirect)
#   7. Start Streamlit (foreground, server.port 8501)
#
# Re-running this script is safe. It only does work for the steps that
# aren't already done.

$ErrorActionPreference = 'Stop'

# 1. Repo root
$ScriptDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot    = Resolve-Path (Join-Path $ScriptDir '..')
Set-Location $RepoRoot
Write-Host "==> Repo: $RepoRoot"

# 2. venv
$Py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host '==> Creating .venv'
    python -m venv .venv
}
else {
    Write-Host '==> .venv present'
}

# 3. Install deps
Write-Host '==> Installing requirements'
& $Py -m pip install --upgrade pip --quiet
& $Py -m pip install -r requirements.txt --quiet

# 4. .env
$EnvFile = Join-Path $RepoRoot '.env'
if (-not (Test-Path $EnvFile)) {
    Write-Host '==> Creating .env from .env.example'
    Copy-Item (Join-Path $RepoRoot '.env.example') $EnvFile
    Write-Host '    Edit .env with your X_BEARER_TOKEN, X_CLIENT_ID,'
    Write-Host '    X_CLIENT_SECRET and MINIMAX_API_KEY, then re-run this script.'
    exit 0
}
else {
    Write-Host '==> .env present'
}

# 5. Phase 0 verification
Write-Host '==> Running scripts/verify_setup.py'
& $Py scripts/verify_setup.py
if ($LASTEXITCODE -ne 0) {
    Write-Error 'verify_setup.py failed. Run it manually to see what is missing.'
    exit $LASTEXITCODE
}

# 6. OAuth setup, if needed
$Tokens = Join-Path $RepoRoot 'data\oauth_tokens.json'
if (-not (Test-Path $Tokens)) {
    Write-Host '==> Running scripts/auth_setup.py (opens browser)'
    & $Py scripts/auth_setup.py
    if ($LASTEXITCODE -ne 0) {
        Write-Error 'auth_setup.py failed. Re-run it manually after fixing .env.'
        exit $LASTEXITCODE
    }
}
else {
    Write-Host '==> OAuth tokens present (skip auth_setup)'
}

# 7. Launch Streamlit
Write-Host ''
Write-Host '==> Launching Streamlit on http://localhost:8501'
Write-Host '    Press Ctrl-C to stop.'
Write-Host ''
& $Py -m streamlit run src\x_auto\app.py `
    --server.headless true `
    --server.port 8501 `
    --client.toolbarMode minimal `
    --browser.gatherUsageStats false
