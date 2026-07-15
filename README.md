<div align="center">

# ⚡ g4f-bridge

**G4F + EAON → OpenCode · Claude Code · Codex CLI · Cursor · Antigravity**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat&logo=python&logoColor=white)](https://python.org)
[![Node](https://img.shields.io/badge/Node-22%2B-339933?style=flat&logo=nodedotjs&logoColor=white)](https://nodejs.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688?style=flat&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Ink](https://img.shields.io/badge/Ink-7-000000?style=flat&logo=react&logoColor=white)](https://inkjs.dev)
[![License](https://img.shields.io/badge/License-GPL%203.0-181717?style=flat)](LICENSE)

```
  ┌─────────────────────────────────────────────────┐
  │  ▸ deepseek/r1      POST /v1/chat/completions   │
  │  ▸ gemini/pro         ← 200 OK (1.2s)           │
  │  ▸ gpt-4o            POST /v1/messages          │
  │  ▸ claude-3.5          ← 200 OK (3.4s)          │
  │                                                 │
  │  [q] stop  [r] restart  [c] config  [m] models  │
  └─────────────────────────────────────────────────┘
```

</div>

---

## Quick Start

```bash
git clone https://github.com/Yatin-Code/opencode-g4f-bridge.git
cd opencode-g4f-bridge
pip install -r requirements.txt
npm install && npm run build
pip install -e .
g4f-bridge
```

First run opens the setup wizard. After that, the dashboard starts.

---

## Commands

**TUI**
```
g4f-bridge                Auto: first run → wizard, else → dashboard
g4f-bridge setup          Setup wizard
g4f-bridge config         IDEs, settings, API keys
g4f-bridge models         Model picker
g4f-bridge dashboard      Live logs
```

**CLI**
```
g4f-bridge -b                 Auto-select top 15 models
g4f-bridge -b 5               Top 5 models
g4f-bridge -m gpt             Search by name
g4f-bridge -b -t              Auto-select + test before saving
g4f-bridge -b --target all    Generate for all tools
g4f-bridge -s                 Update API keys
```

---

## How It Works

```
Your Tool ──► g4f-bridge:1337 ──► G4F / EAON
               │
               ├── Translates Anthropic ⇄ OpenAI
               ├── Sanitizes SSE streams
               └── Returns clean response
```

| Endpoint | Format | Tools |
|----------|--------|-------|
| `POST /v1/chat/completions` | OpenAI Chat | OpenCode, Codex, Cursor, Antigravity |
| `POST /v1/messages` | Anthropic Messages | Claude Code |
| `GET /v1/models` | Model Discovery | All |

---

## Supported Tools

| Tool | Config | Format |
|------|--------|--------|
| OpenCode | `~/.config/opencode/opencode.json` | `@ai-sdk/openai-compatible` |
| Claude Code | `~/.claude/settings.json` | Anthropic env vars |
| Codex CLI | `~/.codex/config.toml` | Custom provider |
| Cursor | `~/.cursor/settings.json` | OpenAI API |
| Antigravity | `~/.gemini/settings.json` | Base URL |

---

## Configuration

```
~/.g4f-bridge/
├── keys.json         API keys (G4F, EAON, custom)
└── settings.json     IDEs, models, preferences
```

**Custom config directory**
```bash
export G4F_BRIDGE_CONFIG_DIR=/path/to/custom/dir
g4f-bridge
```

**Custom providers** — add via TUI settings or `keys.json`:
```json
{
  "G4F": "your-key",
  "EAON": "your-key",
  "MY_PROVIDER": "your-key"
}
```

---

## Features

- One command for everything — dynamic API providers
- 5 AI tools, 1 bridge — Anthropic ⇄ OpenAI translation
- Live model stress testing — SSE stream sanitization
- Model discovery endpoint — real-time TUI dashboard
- Cross-platform (Linux/macOS/Windows)

---

## Project Structure

```
g4f-bridge/
├── smart_bridge.py          Python shim
├── src/bridge/              FastAPI server
│   ├── main.py              CLI entry + TUI launcher
│   ├── models.py            Model discovery, testing
│   ├── translate.py         Anthropic ⇄ OpenAI translation
│   └── utils.py             Config, preflight checks
├── src/configs/             Per-IDE config generators
├── tui/                     Node.js TUI (Ink + React)
│   ├── index.tsx            TUI entry
│   ├── app.tsx              Root component
│   ├── screens/             5 screens
│   ├── components/          Reusable UI
│   └── lib/                 API, config, process
├── requirements.txt
├── pyproject.toml
└── package.json
```

---

<div align="center">GPL-3.0</div>
