$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$py = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
& $py -m pip install --disable-pip-version-check -r requirements-github.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install Python dependencies' }
& $py -m scripts.finish_setup
if ($LASTEXITCODE -ne 0) { throw 'Read the safe setup error above' }
