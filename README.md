# BajiMart Customer Support Agent

A self-contained refund support demo with a mock CRM, strict policy checks, live reasoning logs, and optional LLM orchestration through CrewAI.

## Overview

This project simulates a customer support workflow for refund requests. It combines:

- a mock CRM with 15 customer profiles and order histories
- a strict refund policy document used by the decision engine
- a deterministic fallback path so the app still works when an LLM is unavailable
- optional CrewAI integration with either Ollama-compatible local models or OpenAI
- a live admin dashboard that streams tool calls and decisions in real time
- browser voice input by default, with optional OpenAI-backed audio transcription and speech generation

The app runs as a single Python HTTP server and serves the UI from `public/` with policy and CRM data from `data/`.

## Project Status

The repository is set up for local development and GitHub publishing:

- `.gitignore` excludes virtual environments, secrets, and generated files
- `.env.example` documents supported runtime settings
- `dev.ps1` creates a local `.venv`, installs dependencies, and starts the app
- `.github/workflows/ci.yml` checks Python and browser script syntax on push and pull requests
- `Procfile` supports Heroku-style web deploys
- `DEVELOPMENT.md` and `DEPLOYMENT.md` cover setup and publishing

## Quick Start

### 1. Install Python

Use Python 3.11 or newer.

### 2. Optional: install a local LLM

The default configuration prefers Ollama:

```powershell
winget install -e --id Ollama.Ollama
ollama pull phi3:mini
```

### 3. Install dependencies

```powershell
python -m pip install -r requirements.txt
```

### 4. Start the app

```powershell
python app.py
```

On Windows, you can also use the development launcher:

```powershell
.\dev.ps1
```

If you already have an environment you want to reuse:

```powershell
.\start.ps1
```

### 5. Open the app

Go to:

- http://localhost:3000

## How It Works

Refund requests follow this flow:

1. Extract key details from the customer message
2. Match the customer and order in the CRM
3. Verify identity and order ownership
4. Evaluate strict policy rules
5. Approve, deny, or route to manual review
6. Record a refund case for audit history when appropriate

The customer-facing chat stays concise, while the admin panel shows the underlying reasoning trail.

## Voice Modes

There are two voice paths:

- Browser voice mode: uses browser speech recognition and speech synthesis, no API key required
- OpenAI voice mode: when `OPENAI_API_KEY` is set, the mic records audio, transcribes it with OpenAI, and plays the reply using OpenAI text-to-speech

Supported audio-related environment variables:

- `OPENAI_API_KEY`
- `OPENAI_TRANSCRIBE_MODEL`
- `OPENAI_TTS_MODEL`
- `OPENAI_VOICE`

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PORT` | `3000` | HTTP port for the server |
| `USE_LLM` | `true` | Enables the LLM orchestration path when available |
| `LLM_PROVIDER` | `ollama` | Chooses `ollama`, `openai`, or `auto` |
| `LOCAL_LLM_MODEL` | `phi3:mini` | Local Ollama-compatible model name |
| `LOCAL_LLM_BASE_URL` | `http://127.0.0.1:11434/v1` | Base URL for local LLM requests |
| `LOCAL_LLM_HEALTH_URL` | `http://127.0.0.1:11434/api/tags` | Health check for local LLM availability |
| `OPENAI_API_KEY` | empty | Enables OpenAI LLM and voice features |
| `OPENAI_MODEL` | `gpt-4o-mini` | OpenAI text model used by CrewAI |
| `OPENAI_TRANSCRIBE_MODEL` | `gpt-4o-transcribe` | OpenAI speech-to-text model |
| `OPENAI_TTS_MODEL` | `gpt-4o-mini-tts` | OpenAI text-to-speech model |
| `OPENAI_VOICE` | `marin` | Voice used by the OpenAI audio response |

Copy `.env.example` to `.env` if you want local overrides.

## Example Prompts

- Approve: `I want a refund for ORD-7002 because it arrived damaged.`
- Deny: `I want a refund for ORD-7005 because I changed my mind.`
- Manual review: `I want a refund for ORD-7008 because the charge looks wrong.`
- Clarification: `ORD-7002`

The policy engine also accepts email-based requests when the CRM can match the account and order.

## Development Checks

If you want to verify the project locally:

```powershell
python -B -c "import ast, pathlib; ast.parse(pathlib.Path('app.py').read_text(encoding='utf-8')); print('ok')"
node --check public\app.js
```

These checks are the same kind of syntax validation used in CI.

## Deployment

This app is a single-process HTTP server, so most web hosts can run it directly from the repo root.

Suggested settings:

- Build command: `pip install -r requirements.txt`
- Start command: `python app.py`
- Port: set `PORT` if your platform provides one
- Bundle `data/customers.json` and `data/refund-policy.md` with the deployment
- Configure either OpenAI credentials or a local Ollama-compatible endpoint if you want the LLM path

A `Procfile` is included for hosts that support it:

```text
web: python app.py
```

## Project Structure

- `app.py`: HTTP server, policy engine, optional CrewAI orchestration, and voice endpoints
- `public/index.html`: customer chat UI and admin dashboard shell
- `public/app.js`: client-side chat, voice, runtime config, and trace rendering
- `public/styles.css`: UI styling
- `data/customers.json`: mock CRM records
- `data/refund-policy.md`: strict refund policy source

## License

MIT License. See [LICENSE](LICENSE).
