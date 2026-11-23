param([string]$Target = "")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$py = "py"
if ($Target) {
  & $py ".\APPLY_V21_5_TO_EXISTING_V21_4.py" --target $Target
} else {
  & $py ".\APPLY_V21_5_TO_EXISTING_V21_4.py"
}
exit $LASTEXITCODE
