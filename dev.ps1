$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
  $pythonCommand = Get-Command py -ErrorAction SilentlyContinue
}

if (-not $pythonCommand) {
  throw "Python was not found on PATH. Install Python 3, then run .\dev.ps1 again."
}

$venvPath = Join-Path $root ".venv"
if (-not (Test-Path $venvPath)) {
  & $pythonCommand.Path -m venv $venvPath
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

$envPath = Join-Path $root ".env"
if (Test-Path $envPath) {
  Get-Content $envPath | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
      return
    }

    $name, $value = $line.Split("=", 2)
    [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), "Process")
  }
}

& $venvPython app.py
