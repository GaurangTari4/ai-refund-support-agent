# Deployment

## Generic web service

Use these settings on Render, Railway, Heroku-style platforms, or any host that runs a web process from the repo root:

- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Port: set `PORT` if the platform provides one, otherwise the app defaults to `3000`

## LLM provider

The app uses an LLM by default through CrewAI. Pick one provider for production.

For an Ollama-compatible service:

```text
USE_LLM=true
LLM_PROVIDER=ollama
LOCAL_LLM_MODEL=phi3:mini
LOCAL_LLM_BASE_URL=https://your-ollama-compatible-host/v1
LOCAL_LLM_HEALTH_URL=https://your-ollama-compatible-host/api/tags
```

For OpenAI:

```text
USE_LLM=true
LLM_PROVIDER=openai
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

The deterministic policy engine remains as a fallback when the configured LLM is not reachable.

The included `Procfile` also declares:

```text
web: python app.py
```

## GitHub publish

From this folder:

```powershell
git remote add origin https://github.com/YOUR_USERNAME/ai-refund-support-agent.git
git branch -M main
git push -u origin main
```

Create the empty GitHub repository first, then run the commands above with your real repository URL.
