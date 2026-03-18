# Subscription API Server

Proxy server that uses Claude and ChatGPT subscription OAuth tokens to call Anthropic and OpenAI APIs. Supports Claude Sonnet/Opus/Haiku and GPT-5.x models through a single endpoint. Vision (image input) supported.

English | [한국어](README.ko.md)

## Pricing & Limits

This project uses subscription OAuth tokens instead of per-token API billing. Usage is subject to each subscription plan's rate limits.

- [Claude subscription plans](https://claude.com/pricing) / [Claude API pricing](https://platform.claude.com/docs/en/about-claude/pricing)
- [ChatGPT subscription plans](https://chatgpt.com/pricing) / [OpenAI API pricing](https://openai.com/api/pricing/)

## Features

- **Unified endpoint** — Claude and GPT through a single `/v1/chat/completions` API
- **OpenAI-compatible** — drop-in replacement for any OpenAI SDK/client
- **Multi-account** — round-robin load balancing with automatic failover
- **Vision support** — send images (URL or base64) to both Claude and GPT
- **Built-in tools** — web search, web fetch passthrough
- **Auto token refresh** — handles OAuth token expiration automatically

## How It Works

```
Client (curl, SDK, app)
  │
  ▼
This Server (localhost:5010)      ──▶  Anthropic API  (Claude)
  OpenAI /v1/chat/completions     ──▶  ChatGPT API    (GPT)
  │
  └── Auth: OAuth tokens from your Claude / ChatGPT subscriptions
```

Your client sends standard OpenAI Chat Completions requests. The server routes to Claude or GPT based on the model name, authenticating with your subscription OAuth tokens.

## Supported Models

| Model | `model` value | Subscription | Vision |
|---|---|---|---|
| Claude Sonnet 4.6 | `claude-sonnet-4-6` | Claude Pro/Max | O |
| Claude Opus 4.6 | `claude-opus-4-6` | Claude Max | O |
| Claude Haiku 4.5 | `claude-haiku-4-5-20251001` | Claude Pro/Max | O |
| GPT-5.4 | `gpt-5.4` | ChatGPT Plus/Pro | O |
| GPT-5.4 Mini | `gpt-5.4-mini` | ChatGPT Plus/Pro | O |
| GPT-5.3 Codex | `gpt-5.3-codex` | ChatGPT Plus/Pro | O |
| GPT-5.3 Codex Spark | `gpt-5.3-codex-spark` | ChatGPT Pro | O |
| GPT-5.2 | `gpt-5.2` | ChatGPT Plus/Pro | O |
| GPT-5.2 Codex | `gpt-5.2-codex` | ChatGPT Plus/Pro | O |
| GPT-5.1 | `gpt-5.1` | ChatGPT Plus/Pro | O |
| GPT-5.1 Codex | `gpt-5.1-codex` | ChatGPT Plus/Pro | O |
| GPT-5.1 Codex Max | `gpt-5.1-codex-max` | ChatGPT Plus/Pro | O |
| GPT-5 | `gpt-5` | ChatGPT Plus/Pro | O |
| GPT-5 Codex | `gpt-5-codex` | ChatGPT Plus/Pro | O |
| GPT-5 Codex Mini | `gpt-5-codex-mini` | ChatGPT Plus/Pro | O |

## Quick Start

### 1. Login to your subscriptions

```bash
# ChatGPT (for GPT models)
npx codex login

# Claude (for Claude models)
claude login
```

### 2. Install & run

```bash
git clone https://github.com/hwansoo17/claude-gpt-proxy.git
cd claude-gpt-proxy
pip install -r requirements.txt
cp .env.example .env
python3 server.py
```

### 3. Use it

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Or with any OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5010/v1", api_key="unused")
response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### 4. Vision (Image Input)

Send images via URL or base64. Uses standard OpenAI `image_url` format — automatically converted to each backend's native format.

**URL image:**
```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "messages": [{"role": "user", "content": [
      {"type": "text", "text": "What is in this image?"},
      {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}}
    ]}]
  }'
```

**Local file (base64):**
```python
import base64
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5010/v1", api_key="unused")

with open("screenshot.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Describe this image."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
    ]}]
)
print(response.choices[0].message.content)
```

## Docker

```bash
docker compose up --build -d
```

Docker Compose mounts your host credential files directly into the container:

- `~/.claude/.credentials.json` → `/app/claude_credentials.json`
- `~/.codex/auth.json` → `/app/codex_credentials.json`

Just `claude login` and `npx codex login` on the host, then `docker compose up`. No setup endpoint needed — tokens are read from your login sessions and refreshed tokens are written back to the host files.

## Authentication

You must be logged in to each service first:

```bash
claude login          # for Claude models
npx codex login       # for GPT models
```

After login, credentials are loaded automatically. No manual setup required.

**Claude** — token loading priority (highest first):

| Priority | Source | Platform |
|---|---|---|
| 1 | `CLAUDE_OAUTH_TOKENS` env var (multi) | Any |
| 2 | `CLAUDE_CREDENTIALS_FILES` file paths (multi) | Any |
| 3 | `CLAUDE_OAUTH_TOKEN` env var (single) | Any |
| 4 | `CLAUDE_CREDENTIALS_FILE` file (single) | Any |
| 5 | macOS Keychain (single) | macOS |

**Codex (GPT):**

| Priority | Source | Platform |
|---|---|---|
| 1 | `CODEX_AUTH_FILES` env var | Any |
| 2 | `CODEX_AUTH_FILE` file | Any |

## Multi-Account

Distribute requests across multiple subscription accounts via round-robin.

**Option A: Token env vars**
```env
CLAUDE_OAUTH_TOKENS=sk-ant-oat01-aaa...,sk-ant-oat01-bbb...,sk-ant-oat01-ccc...
CLAUDE_OAUTH_REFRESH_TOKENS=sk-ant-ort01-aaa...,sk-ant-ort01-bbb...,sk-ant-ort01-ccc...
```

**Option B: Credentials files** (each from a separate `claude login`)
```env
CLAUDE_CREDENTIALS_FILES=~/.claude/.credentials.json,~/.claude/.credentials2.json,~/.claude/.credentials3.json
```

### Codex Multi-Account
```env
CODEX_AUTH_FILES=~/.codex/auth1.json,~/.codex/auth2.json
```

**Failover:** Accounts are automatically disabled after 3 consecutive failures (120s cooldown), then re-enabled. Configurable via `CLAUDE_MAX_FAILURES`, `CLAUDE_COOLDOWN_SECONDS`.

## API Reference

### `POST /v1/chat/completions`

| Parameter | Type | Required | Description | Claude | GPT |
|---|---|---|---|---|---|
| `model` | string | Yes | Model name | O | O |
| `messages` | array | Yes | Conversation messages | O | O |
| `stream` | boolean | | Enable streaming (default: false) | O | O |
| `stop` | string[] | | Stop sequences | O | X |
| `tools` | array | | Tool definitions | O | O |
| `max_tokens` | integer | | Max output tokens (min 1024) | O | X |

**Vision:** Use `image_url` type in `messages[].content` array. Supports both URL (`https://...`) and base64 data URI (`data:image/...;base64,...`). Auto-converted to each backend's native format.

> `temperature` and `top_p` are not supported (Claude thinking mode constraint / Codex API limitation).

### `GET /v1/models`

Returns list of available models.

### `GET /health`

Returns `{"status": "ok"}`.

## Built-in Tools

### Claude

| Tool | Type | Description |
|---|---|---|
| Web Search | `web_search_20250305` | Real-time web search |
| Web Fetch | `web_fetch_20250910` | Fetch URL content |

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role": "user", "content": "Current Bitcoin price?"}],
    "tools": [{"type": "web_search_20250305", "name": "web_search"}]
  }'
```

### GPT

| Tool | Type | Description |
|---|---|---|
| Web Search | `web_search` | Real-time web search |
| Custom Function | `function` | User-defined function calling |

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "messages": [{"role": "user", "content": "Current Bitcoin price?"}],
    "tools": [{"type": "web_search"}]
  }'
```

## Environment Variables

<details>
<summary>Full list</summary>

### Server

| Variable | Default | Description |
|---|---|---|
| `PORT` | `5010` | Server port |

### Claude

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_OAUTH_TOKENS` | | Multi-account tokens (comma-separated) |
| `CLAUDE_OAUTH_REFRESH_TOKENS` | | Multi-account refresh tokens |
| `CLAUDE_CREDENTIALS_FILES` | | Multi-account credentials file paths (comma-separated) |
| `CLAUDE_OAUTH_TOKEN` | | Single account token |
| `CLAUDE_OAUTH_REFRESH_TOKEN` | | Single account refresh token |
| `CLAUDE_CREDENTIALS_FILE` | `~/.claude/.credentials.json` | Credentials file path |
| `CLAUDE_KEYCHAIN_SERVICE` | `Claude Code-credentials` | macOS Keychain service |
| `CLAUDE_MAX_FAILURES` | `3` | Failures before disable |
| `CLAUDE_COOLDOWN_SECONDS` | `120` | Cooldown duration (s) |

### Codex

| Variable | Default | Description |
|---|---|---|
| `CODEX_AUTH_FILES` | | Multi-account file paths (comma-separated) |
| `CODEX_AUTH_FILE` | `~/.codex/auth.json` | Single account file path |
| `CODEX_MAX_FAILURES` | `3` | Failures before disable |
| `CODEX_COOLDOWN_SECONDS` | `120` | Cooldown duration (s) |

### Advanced

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_API_URL` | `https://api.anthropic.com/v1/messages` | Claude API endpoint |
| `CLAUDE_CODE_VERSION` | `2.1.76` | Claude Code version |
| `CLAUDE_OAUTH_TOKEN_URL` | `https://console.anthropic.com/api/oauth/token` | Token refresh URL |
| `CLAUDE_OAUTH_CLIENT_ID` | `9d1c250a-e61b-44d9-88ed-5944d1962f5e` | OAuth client ID |
| `CODEX_API_URL` | `https://chatgpt.com/backend-api/codex/responses` | Codex API endpoint |
| `CODEX_CLIENT_ID` | `app_EMoamEEZ73f0CkXaXp7hrann` | Codex OAuth client ID |
| `CODEX_TOKEN_URL` | `https://auth.openai.com/oauth/token` | Token refresh URL |

</details>

## Limitations

> **Note:** This project routes requests through subscription OAuth, not the official API. Responses are **not 100% identical** to the official API. Key differences:

- **Claude Sonnet/Opus**: A minimal identification header is required in the system prompt. User instructions are appended after it. `temperature` and `top_p` are restricted by thinking mode.
- **Claude Haiku**: System prompt is fully customizable.
- **GPT**: `temperature`, `top_p`, `max_tokens`, `stop` are not supported through the Codex Responses API.
- **Usage/token counts** may differ from official API responses.
- **Some API features** (e.g., `n`, `frequency_penalty`, `presence_penalty`, `logprobs`) are not available.
- **Rate limits** are determined by your subscription tier, not API quotas.
- **Token refresh**: Automatic. On failure, fails over to the next account.
- **Breaking changes**: Anthropic or OpenAI may update their authentication, API endpoints, or validation at any time, which could break this project without notice.

## Disclaimer

> **This project is for educational and research purposes only.**

- This software is provided **AS-IS** without warranty.
- Using Claude subscription OAuth outside of Claude Code may violate [Anthropic's Terms of Service](https://www.anthropic.com/policies/consumer-terms).
- For ChatGPT/Codex usage, see [OpenAI's Terms of Service](https://openai.com/policies/service-terms/).
- **You assume all responsibility** for any consequences including account suspension, service restrictions, or legal issues.
- The author is not liable for any damages arising from use of this software.

## License

MIT License — See [LICENSE](LICENSE) for details.
