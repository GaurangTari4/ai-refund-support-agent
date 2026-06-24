# Development

## First run on Windows

Install and start the local LLM:

```powershell
winget install -e --id Ollama.Ollama
ollama pull phi3:mini
```

Ollama usually runs as a background service on Windows. If it is not running, start it with:

```powershell
ollama serve
```

```powershell
.\dev.ps1
```

The script creates `.venv`, installs `requirements.txt`, loads `.env` when present, and starts the app in LLM mode by default.

## Manual setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

Open http://localhost:3000.

## Environment

Copy `.env.example` to `.env` when you want local overrides.

Default local LLM settings:

```powershell
USE_LLM=true
LLM_PROVIDER=ollama
LOCAL_LLM_MODEL=phi3:mini
LOCAL_LLM_BASE_URL=http://127.0.0.1:11434/v1
LOCAL_LLM_HEALTH_URL=http://127.0.0.1:11434/api/tags
```

Use OpenAI instead:

```powershell
USE_LLM=true
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

The app prefers the configured LLM. If the LLM is unavailable, it falls back to the deterministic refund-policy engine so development is not blocked.

## Useful checks

```powershell
.\.venv\Scripts\python.exe -B -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8')); print('ok')"
node --check public\app.js
```

To verify the local LLM is reachable:

```powershell
ollama list
Invoke-WebRequest http://127.0.0.1:11434/api/tags
```
