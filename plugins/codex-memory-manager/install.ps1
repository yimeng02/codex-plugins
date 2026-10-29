$ErrorActionPreference = 'Stop'
$installerPath = Join-Path $PSScriptRoot 'install.py'
if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $installerPath @args
    exit $LASTEXITCODE
}
if (Get-Command python3 -ErrorAction SilentlyContinue) {
    & python3 $installerPath @args
    exit $LASTEXITCODE
}
if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $installerPath @args
    exit $LASTEXITCODE
}
Write-Error 'Python 3.10+ is required. Install Python, then run this script again.'
exit 1
