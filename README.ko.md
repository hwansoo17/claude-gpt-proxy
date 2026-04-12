# Codex Proxy Server

ChatGPT 구독의 OAuth 토큰으로 Codex를 통해 OpenAI API를 호출하는 프록시 서버입니다. GPT-5.x 모델을 하나의 엔드포인트로 지원합니다. Vision(이미지 입력) 지원.

한국어 | [English](README.md)

## 가격 및 한도

토큰당 과금되는 API 대신 구독 OAuth 토큰을 사용합니다. 사용량은 각 구독 플랜의 rate limit을 따릅니다.

- [ChatGPT 구독 플랜](https://chatgpt.com/pricing) / [OpenAI API 가격](https://openai.com/api/pricing/)

## 주요 기능

- **OpenAI 호환** — 모든 OpenAI SDK/클라이언트에서 바로 사용 가능
- **멀티 계정** — 라운드로빈 로드밸런싱 + 자동 페일오버
- **Vision 지원** — 이미지(URL 또는 base64) 전송 가능
- **내장 도구** — 웹 검색, 커스텀 함수 호출
- **자동 토큰 갱신** — OAuth 토큰 만료 자동 처리
- **401 자동 재시도** — 인증 실패 시 토큰 갱신 후 재시도

## 동작 방식

```
Client (curl, SDK, app)
  │
  ▼
This Server (localhost:5010)      ──▶  ChatGPT Codex API  (GPT)
  OpenAI /v1/chat/completions
  │
  └── Auth: ChatGPT 구독의 OAuth 토큰
```

클라이언트가 표준 OpenAI Chat Completions 요청을 보내면, 서버가 Codex Responses API를 통해 GPT로 라우팅하고 구독 OAuth 토큰으로 인증합니다.

## 지원 모델

| 모델 | `model` 값 | 구독 | Vision |
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

## 빠른 시작

### 1. ChatGPT 로그인

```bash
npx codex login
```

### 2. 설치 및 실행

```bash
git clone https://github.com/hwansoo17/claude-gpt-proxy.git
cd claude-gpt-proxy
pip install -r requirements.txt
cp .env.example .env
python3 server.py
```

### 3. 사용

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.3-codex",
    "messages": [{"role": "user", "content": "안녕하세요!"}]
  }'
```

OpenAI SDK로도 사용 가능:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5010/v1", api_key="unused")
response = client.chat.completions.create(
    model="gpt-5.3-codex",
    messages=[{"role": "user", "content": "안녕하세요!"}]
)
print(response.choices[0].message.content)
```

### 4. Vision (이미지 입력)

URL 또는 base64로 이미지를 전송할 수 있습니다. OpenAI 표준 `image_url` 형식을 사용합니다.

**URL 이미지:**
```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "messages": [{"role": "user", "content": [
      {"type": "text", "text": "이 이미지에 뭐가 있어?"},
      {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}}
    ]}]
  }'
```

**로컬 파일 (base64):**
```python
import base64
from openai import OpenAI

client = OpenAI(base_url="http://localhost:5010/v1", api_key="unused")

with open("screenshot.png", "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

response = client.chat.completions.create(
    model="gpt-5.4",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "이 이미지를 설명해줘."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}
    ]}]
)
print(response.choices[0].message.content)
```

## Docker

```bash
docker compose up --build -d
```

Docker Compose가 호스트의 credential 파일을 컨테이너에 직접 마운트합니다:

- `~/.codex/auth.json` → `/app/codex_credentials.json`

`.env` 파일에 컨테이너 내부 경로를 설정해야 합니다:

```env
CODEX_AUTH_FILE=/app/codex_credentials.json
```

호스트에서 `npx codex login`만 해두면 `docker compose up`으로 바로 실행됩니다. 갱신된 토큰은 호스트 파일에 다시 저장됩니다.

## 인증

사전에 로그인되어 있어야 합니다:

```bash
npx codex login
```

로그인 후에는 credential이 자동으로 로드됩니다. 별도 설정이 필요 없습니다.

**토큰 로딩 우선순위 (높은 순):**

| 순위 | 소스 | 플랫폼 |
|---|---|---|
| 1 | `CODEX_AUTH_FILES` 환경변수 (멀티) | 모든 |
| 2 | `CODEX_AUTH_FILE` 파일 (단일) | 모든 |

## 멀티 계정

여러 구독 계정을 등록하면 요청을 라운드로빈으로 분산합니다.

```env
CODEX_AUTH_FILES=~/.codex/auth1.json,~/.codex/auth2.json
```

**페일오버:** 연속 3회 실패 시 해당 계정 120초 비활성화 후 자동 복구. `CODEX_MAX_FAILURES`, `CODEX_COOLDOWN_SECONDS`로 설정 가능.

## API 레퍼런스

### `POST /v1/chat/completions`

| 파라미터 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `model` | string | O | 모델명 |
| `messages` | array | O | 대화 메시지 배열 |
| `stream` | boolean | | 스트리밍 여부 (기본: false) |
| `tools` | array | | 도구 정의 |
| `tool_choice` | string/object | | 도구 선택 설정 |

**Vision:** `messages[].content` 배열에 `image_url` 타입을 사용합니다. URL(`https://...`)과 base64 data URI(`data:image/...;base64,...`) 모두 지원합니다.

> `temperature`, `top_p`, `max_tokens`, `stop`은 Codex Responses API 특성상 지원되지 않습니다.

### `GET /v1/models`

사용 가능한 모델 목록을 반환합니다.

### `GET /health`

`{"status": "ok"}`를 반환합니다.

## 내장 도구 (Tools)

| 도구 | `type` 값 | 설명 |
|---|---|---|
| 웹 검색 | `web_search` | 실시간 웹 검색 |
| 커스텀 함수 | `function` | 사용자 정의 함수 호출 |

```bash
curl -X POST http://localhost:5010/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-5.4",
    "messages": [{"role": "user", "content": "오늘 비트코인 가격은?"}],
    "tools": [{"type": "web_search"}]
  }'
```

## 환경변수 (.env)

<details>
<summary>전체 목록</summary>

### 서버

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PORT` | `5010` | 서버 포트 |

### Codex

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CODEX_AUTH_FILES` | | 멀티 계정 파일 경로 (쉼표 구분) |
| `CODEX_AUTH_FILE` | `~/.codex/auth.json` | 단일 계정 파일 경로 |
| `CODEX_MAX_FAILURES` | `3` | 비활성화까지 실패 횟수 |
| `CODEX_COOLDOWN_SECONDS` | `120` | 쿨다운 시간 (초) |
| `CODEX_REFRESH_MARGIN` | `300` | 만료 전 갱신 시작 시간 (초) |

### 고급

| 변수 | 기본값 | 설명 |
|---|---|---|
| `CODEX_API_URL` | `https://chatgpt.com/backend-api/codex/responses` | Codex API 엔드포인트 |
| `CODEX_CLIENT_ID` | `app_EMoamEEZ73f0CkXaXp7hrann` | Codex OAuth 클라이언트 ID |
| `CODEX_TOKEN_URL` | `https://auth.openai.com/oauth/token` | 토큰 리프레시 URL |

</details>

## 제한사항

> **참고:** 이 프로젝트는 구독 OAuth를 경유하며, 공식 API와 **100% 동일한 응답을 보장하지 않습니다.** 주요 차이점:

- **GPT**: Codex Responses API 경유 특성상 `temperature`, `top_p`, `max_tokens`, `stop` 파라미터가 지원되지 않습니다.
- **사용량/토큰 수**가 공식 API 응답과 다를 수 있습니다.
- **일부 API 기능** (`n`, `frequency_penalty`, `presence_penalty`, `logprobs` 등)은 사용할 수 없습니다.
- **Rate limit**은 API 할당량이 아닌 구독 티어에 따라 결정됩니다.
- **OAuth 토큰 만료**: 자동 리프레시됩니다. 401 에러 발생 시 즉시 갱신 + 재시도합니다. 지속 실패 시 다음 계정으로 페일오버됩니다.
- **호환성**: OpenAI가 인증, API 엔드포인트, 검증 방식을 변경하면 예고 없이 동작하지 않을 수 있습니다.

## Disclaimer

> **이 프로젝트는 교육 및 연구 목적으로 제작되었습니다.**

- 이 소프트웨어는 **있는 그대로(AS-IS)** 제공되며, 어떠한 보증도 하지 않습니다.
- Codex/ChatGPT 구독 OAuth 사용에 대해서는 [OpenAI 이용약관](https://openai.com/policies/service-terms/)을 확인하세요.
- 본 프로젝트의 사용으로 인해 발생하는 **계정 정지, 서비스 제한, 요금 청구, 법적 문제 등 모든 결과에 대한 책임은 전적으로 사용자에게 있습니다.**
- 제작자는 이 소프트웨어의 사용으로 인한 직접적, 간접적 손해에 대해 어떠한 책임도 지지 않습니다.
- 각 서비스의 이용약관을 반드시 확인하고, 본인의 판단 하에 사용하시기 바랍니다.

## License

MIT License — 자세한 내용은 [LICENSE](LICENSE) 파일을 참조하세요.
