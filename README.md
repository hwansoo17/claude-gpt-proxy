# Codex Proxy Server

Proxy server that uses ChatGPT subscription OAuth tokens to call OpenAI APIs via Codex. Supports GPT-5.x models through a single endpoint. Vision (image input) supported.

English | [한국어](README.ko.md)

## Pricing & Limits

This project uses subscription OAuth tokens instead of per-token API billing. Usage is subject to each subscription plan's rate limits.

- [ChatGPT subscription plans](https://chatgpt.com/pricing) / [OpenAI API pricing](https://openai.com/api/pricing/)

## Features

- **OpenAI-compatible** — drop-in replacement for any OpenAI SDK/client
- **Multi-account** — round-robin load balancing with automatic failover
- **Vision support** — send images (URL or base64)
- **Built-in tools** — web search, custom function calling
- **Auto token refresh** — handles OAuth token expiration automatically
- **401 auto-retry** — refreshes token and retries on authentication failure

## How It Works

```
Client (curl, SDK, app)
  │
  ▼
This Server (localhost:5010)      ──▶  ChatGPT Codex API  (GPT)
  OpenAI /v1/chat/completions
  │
  └── Auth: OAuth tokens from your ChatGPT subscription
```

Your client sends standard OpenAI Chat Completions requests. The server routes to GPT via the Codex Responses API, authenticating with your subscription OAuth tokens.

## Supported Models

| Model | `model` value | Subscription | Vision |
|---|---|---|---|
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

### 1. Login to ChatGPT

```bash
npx codex login
```

### 2. Install & run

```bash
git clone https://github.com/hwansoo17/gpt-proxy.git
cd gpt-proxy
pip install -r requirements.txt
cp .env.example .env
python3 server.py
```

### 3. Use it

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

Or with any OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5010/v1", api_key="unused")
response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)
```

### 4. Vision (Image Input)

Send images via URL or base64. Uses standard OpenAI `image_url` format.

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
    model="gpt-5.4",
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

Docker Compose mounts your host credential file directly into the container:

- `~/.codex/auth.json` → `/app/codex_credentials.json`

Set the container path in your `.env` file:

```env
CODEX_AUTH_FILE=/app/codex_credentials.json
```

Just `npx codex login` on the host, then `docker compose up`. Refreshed tokens are written back to the host file.

## Authentication

You must be logged in first:

```bash
npx codex login
```

After login, credentials are loaded automatically. No manual setup required.

**Token loading priority (highest first):**

| Priority | Source | Platform |
|---|---|---|
| 1 | `CODEX_AUTH_FILES` env var (multi) | Any |
| 2 | `CODEX_AUTH_FILE` file (single) | Any |

## Multi-Account

Distribute requests across multiple subscription accounts via round-robin.

```env
CODEX_AUTH_FILES=~/.codex/auth1.json,~/.codex/auth2.json
```

**Failover:** Accounts are automatically disabled after 3 consecutive failures (120s cooldown), then re-enabled. Configurable via `CODEX_MAX_FAILURES`, `CODEX_COOLDOWN_SECONDS`.

## API Reference

### `POST /v1/chat/completions`

| Parameter | Type | Required | Description |
|---|---|---|---|
| `model` | string | Yes | Model name |
| `messages` | array | Yes | Conversation messages |
| `stream` | boolean | | Enable streaming (default: false) |
| `tools` | array | | Tool definitions |
| `tool_choice` | string/object | | Tool selection preference |

**Vision:** Use `image_url` type in `messages[].content` array. Supports both URL (`https://...`) and base64 data URI (`data:image/...;base64,...`).

> `temperature`, `top_p`, `max_tokens`, `stop` are not supported through the Codex Responses API.

### `GET /v1/models`

Returns list of available models.

### `GET /health`

Returns `{"status": "ok"}`.

## Built-in Tools

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

### Codex

| Variable | Default | Description |
|---|---|---|
| `CODEX_AUTH_FILES` | | Multi-account file paths (comma-separated) |
| `CODEX_AUTH_FILE` | `~/.codex/auth.json` | Single account file path |
| `CODEX_MAX_FAILURES` | `3` | Failures before disable |
| `CODEX_COOLDOWN_SECONDS` | `120` | Cooldown duration (s) |
| `CODEX_REFRESH_MARGIN` | `300` | Seconds before expiry to trigger refresh |

### Advanced

| Variable | Default | Description |
|---|---|---|
| `CODEX_API_URL` | `https://chatgpt.com/backend-api/codex/responses` | Codex API endpoint |
| `CODEX_CLIENT_ID` | `app_EMoamEEZ73f0CkXaXp7hrann` | Codex OAuth client ID |
| `CODEX_TOKEN_URL` | `https://auth.openai.com/oauth/token` | Token refresh URL |

</details>

## Limitations

> **Note:** This project routes requests through subscription OAuth, not the official API. Responses are **not 100% identical** to the official API. Key differences:

- **GPT**: `temperature`, `top_p`, `max_tokens`, `stop` are not supported through the Codex Responses API.
- **Usage/token counts** may differ from official API responses.
- **Some API features** (e.g., `n`, `frequency_penalty`, `presence_penalty`, `logprobs`) are not available.
- **Rate limits** are determined by your subscription tier, not API quotas.
- **Token refresh**: Automatic. 401 errors trigger immediate refresh + retry. On persistent failure, fails over to the next account.
- **Breaking changes**: OpenAI may update their authentication, API endpoints, or validation at any time, which could break this project without notice.

## Disclaimer

> **This project is for educational and research purposes only.**

- This software is provided **AS-IS** without warranty.
- For ChatGPT/Codex usage, see [OpenAI's Terms of Service](https://openai.com/policies/service-terms/).
- **You assume all responsibility** for any consequences including account suspension, service restrictions, or legal issues.
- The author is not liable for any damages arising from use of this software.

## License

MIT License — See [LICENSE](LICENSE) for details.
