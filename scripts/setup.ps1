$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.lock
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& .\.venv\Scripts\python.exe scripts/setup.py
if ($LASTEXITCODE -ne 0) { throw 'Setup failed.' }
# Restrict the secret directory to the current operator and local SYSTEM.
$operatorAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls .secrets /inheritance:r /grant:r "${operatorAccount}:(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Secret directory ACL configuration failed.' }
& .\.venv\Scripts\python.exe scripts/samples.py
