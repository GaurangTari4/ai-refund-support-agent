$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction SilentlyContinue).Path
if (-not $python) {
  $python = (Get-Command py -ErrorAction SilentlyContinue).Path
}

if (-not $python) {
  throw "Python was not found on PATH. Install Python 3 or set up a virtual environment, then run: python app.py"
}

& $python app.py
