"""
Subscription API Server — OpenAI Chat Completions compatible.

Routes to Claude (via Claude Code OAuth) or GPT (via Codex/ChatGPT OAuth)
based on model name. No API keys needed — uses subscription tokens only.

Endpoint: POST /v1/chat/completions
Format:   Standard OpenAI Chat Completions (in & out)
"""

import json
import os
import uuid
import time
import requests as http_requests
from dotenv import load_dotenv
from flask import Flask, request, Response, jsonify

load_dotenv()

from codex_client import CodexClient
from claude_client import ClaudeClient, MODEL_CONFIG, ANTHROPIC_API_URL, CLAUDE_CODE_VERSION

app = Flask(__name__)

# ── Load data ────────────────────────────────────

# Minimal system prompt (CLIProxyAPI cloaking style)
_billing = f"x-anthropic-billing-header: cc_version={CLAUDE_CODE_VERSION}.b57; cc_entrypoint=cli; cch=d283f;"
CLAUDE_MIN_SYSTEM = [
    {"type": "text", "text": _billing},
    {"type": "text", "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK."},
]

CLAUDE_MODELS = set(MODEL_CONFIG.keys())

CODEX_MODELS = {"gpt-5.4", "gpt-5.4-pro", "gpt-5.3-codex", "gpt-5.3-codex-spark", "gpt-5.2", "gpt-5.2-codex"}

ALL_MODELS = CLAUDE_MODELS | CODEX_MODELS

# ── Clients ──────────────────────────────────────

codex = CodexClient()
claude = ClaudeClient()

# ── Route ────────────────────────────────────────

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json()
    model = body.get("model", "claude-sonnet-4-6")
    stream = body.get("stream", False)

    if model not in ALL_MODELS:
        return jsonify({"error": {"message": f"Unknown model: {model}. Available: {', '.join(sorted(ALL_MODELS))}", "type": "invalid_request_error"}}), 400

    # Extract system + chat messages
    system_text = None
    chat_messages = []
    for msg in body.get("messages", []):
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            chat_messages.append(msg)

    if model in CLAUDE_MODELS:
        print(f"[api] -> Claude ({model}) messages={len(chat_messages)} stream={stream}")
        return _handle_claude(model, system_text, chat_messages, body, stream)
    else:
        print(f"[api] -> Codex ({model}) messages={len(chat_messages)} stream={stream}")
        return _handle_codex(model, system_text, chat_messages, body, stream)


# ══════════════════════════════════════════════════
#  Codex (GPT) handler
# ══════════════════════════════════════════════════

def _handle_codex(model, system_text, chat_messages, body, stream):
    codex_body = {
        "model": model,
        "stream": True,
        "store": False,
        "instructions": system_text or "You are a helpful assistant.",
        "input": _codex_convert_messages(chat_messages),
    }

    tools = body.get("tools")
    if tools:
        codex_body["tools"] = _codex_convert_tools(tools)
    if body.get("tool_choice"):
        codex_body["tool_choice"] = body["tool_choice"]

    if stream:
        return _codex_stream(codex_body, model)
    else:
        return _codex_sync(codex_body, model)


def _codex_sync(codex_body, model):
    text_parts = []
    tool_calls = []
    current_tool = None
    usage = {}

    for etype, data in codex.stream(codex_body):
        if etype == "error":
            return jsonify({"error": {"message": data.get("error", ""), "type": "api_error"}}), 500
        if etype != "chunk":
            continue
        t = data.get("type", "")
        if t == "response.output_text.delta":
            text_parts.append(data.get("delta", ""))
        elif t == "response.output_item.added":
            item = data.get("item", {})
            if item.get("type") == "function_call":
                current_tool = {"id": item.get("call_id", f"call_{uuid.uuid4().hex[:24]}"), "type": "function", "function": {"name": item.get("name", ""), "arguments": ""}}
                tool_calls.append(current_tool)
        elif t == "response.function_call_arguments.delta":
            if current_tool:
                current_tool["function"]["arguments"] += data.get("delta", "")
        elif t == "response.completed":
            usage = data.get("response", {}).get("usage", {})

    message = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return jsonify(_build_response(model, message, usage, "tool_calls" if tool_calls else "stop"))


def _codex_stream(codex_body, model):
    def generate():
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        tool_calls_by_item = {}
        tool_index = 0

        for etype, data in codex.stream(codex_body):
            if etype == "error":
                yield _sse(chat_id, model, {"content": f"[error: {data.get('error', '')}]"})
                break
            if etype != "chunk":
                continue
            t = data.get("type", "")

            if t == "response.output_text.delta":
                text = data.get("delta", "")
                if text:
                    yield _sse(chat_id, model, {"content": text})

            elif t == "response.output_item.added":
                item = data.get("item", {})
                if item.get("type") == "function_call":
                    item_id = item.get("id", "")
                    tool_calls_by_item[item_id] = tool_index
                    yield _sse(chat_id, model, {"tool_calls": [{"index": tool_index, "id": item.get("call_id", ""), "type": "function", "function": {"name": item.get("name", ""), "arguments": ""}}]})
                    tool_index += 1

            elif t == "response.function_call_arguments.delta":
                idx = tool_calls_by_item.get(data.get("item_id", ""))
                if idx is not None:
                    delta = data.get("delta", "")
                    if delta:
                        yield _sse(chat_id, model, {"tool_calls": [{"index": idx, "function": {"arguments": delta}}]})

            elif t == "response.completed":
                finish = "tool_calls" if tool_calls_by_item else "stop"
                yield _sse(chat_id, model, {}, finish, data.get("response", {}).get("usage", {}))

        yield "data: [DONE]\n\n"

    return Response(generate(), content_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _codex_convert_messages(messages):
    items = []
    for msg in messages:
        role, content = msg["role"], msg.get("content", "")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": msg.get("tool_call_id", ""), "output": content if isinstance(content, str) else json.dumps(content)})
        elif role == "assistant" and msg.get("tool_calls"):
            if content:
                items.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": content}]})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                cid = tc.get("id", f"fc_{uuid.uuid4().hex[:24]}")
                items.append({"type": "function_call", "id": cid, "call_id": cid, "name": fn.get("name", ""), "arguments": fn.get("arguments", "{}")})
        else:
            text_type = "output_text" if role == "assistant" else "input_text"
            if isinstance(content, str) and content:
                items.append({"type": "message", "role": role, "content": [{"type": text_type, "text": content}]})
            elif isinstance(content, list):
                parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                if parts:
                    items.append({"type": "message", "role": role, "content": [{"type": text_type, "text": "\n".join(parts)}]})
    return items


def _codex_convert_tools(tools):
    result = []
    for t in tools:
        if t.get("type") == "function":
            fn = t["function"]
            result.append({"type": "function", "name": fn["name"], "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
        else:
            # Built-in tools (web_search, code_interpreter, etc.) — pass through as-is
            result.append(t)
    return result


# ══════════════════════════════════════════════════
#  Claude handler
# ══════════════════════════════════════════════════

def _handle_claude(model, system_text, chat_messages, body, stream):
    config = MODEL_CONFIG[model]

    # Haiku: user instruction only / Sonnet,Opus: minimal Claude Code header required
    if "haiku" in model:
        system = [{"type": "text", "text": system_text or "You are a helpful assistant."}]
    else:
        system = CLAUDE_MIN_SYSTEM[:]
        if system_text:
            system.append({"type": "text", "text": system_text})

    # max_tokens: user 값이 작으면 thinking이 토큰 다 먹으므로 최소 보장
    user_max = body.get("max_tokens")
    if user_max:
        max_tokens = max(user_max, 1024)  # thinking 여유분 확보
    else:
        max_tokens = config["max_tokens"]

    claude_body = {
        "model": model,
        "max_tokens": max_tokens,
        "stream": stream,
        "messages": _claude_convert_messages(chat_messages),
        "system": system,
        "tools": body.get("tools", []),
        "metadata": claude.build_metadata(),
        "thinking": config["thinking"],
        "context_management": {"edits": [{"type": "clear_thinking_20251015", "keep": "all"}]},
    }

    # Optional parameters
    # thinking mode: temperature must be 1, top_p must be >= 0.95
    has_thinking = config["thinking"].get("type") in ("enabled", "adaptive")
    if "temperature" in body:
        if not has_thinking:
            claude_body["temperature"] = body["temperature"]
    if "top_p" in body:
        if not has_thinking or body["top_p"] >= 0.95:
            claude_body["top_p"] = body["top_p"]
    if "stop" in body:
        claude_body["stop_sequences"] = body["stop"]

    if "output_config" in config:
        claude_body["output_config"] = config["output_config"]

    # Select account for this request
    account = claude.get_account()
    if not account:
        return jsonify({"error": {"message": "No Claude accounts configured", "type": "auth_error"}}), 401

    if stream:
        return _claude_stream(claude_body, model, account)
    else:
        return _claude_sync(claude_body, model, account)


def _claude_sync(claude_body, model, account):
    headers = claude.build_headers(model, account)
    claude_body["stream"] = False

    resp = http_requests.post(f"{ANTHROPIC_API_URL}?beta=true", headers=headers, json=claude_body, timeout=300)

    if not resp.ok:
        print(f"[claude] error {resp.status_code} (account '{account.label}'): {resp.text[:300]}")
        account.mark_failure()
        return jsonify({"error": {"message": resp.text[:500], "type": "api_error"}}), resp.status_code

    account.mark_success()

    data = resp.json()
    content_text = ""
    tool_calls = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            content_text += block["text"]
        elif block.get("type") == "tool_use":
            tool_calls.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)}})

    message = {"role": "assistant", "content": content_text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    usage = data.get("usage", {})
    return jsonify(_build_response(model, message, {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}, "tool_calls" if tool_calls else "stop"))


def _claude_stream(claude_body, model, account):
    headers = claude.build_headers(model, account)
    claude_body["stream"] = True

    def generate():
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        resp = http_requests.post(f"{ANTHROPIC_API_URL}?beta=true", headers=headers, json=claude_body, stream=True, timeout=300)

        if not resp.ok:
            account.mark_failure()
            print(f"[claude] stream error {resp.status_code}: {resp.text[:300]}")
            yield _sse(chat_id, model, {"content": f"[error {resp.status_code}]"})
            yield "data: [DONE]\n\n"
            return

        account.mark_success()
        tool_index = 0
        current_tool_id = None

        for line in resp.iter_lines():
            if not line:
                continue
            line = line.decode("utf-8")
            if not line.startswith("data: "):
                continue
            raw = line[6:]
            if raw == "[DONE]":
                break
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue

            etype = event.get("type", "")

            if etype == "content_block_start":
                block = event.get("content_block", {})
                if block.get("type") == "tool_use":
                    current_tool_id = block.get("id", "")
                    yield _sse(chat_id, model, {"tool_calls": [{"index": tool_index, "id": current_tool_id, "type": "function", "function": {"name": block.get("name", ""), "arguments": ""}}]})
                    tool_index += 1

            elif etype == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        yield _sse(chat_id, model, {"content": text})
                elif delta.get("type") == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if partial and current_tool_id is not None:
                        yield _sse(chat_id, model, {"tool_calls": [{"index": tool_index - 1, "function": {"arguments": partial}}]})

            elif etype == "message_delta":
                stop_reason = event.get("delta", {}).get("stop_reason", "end_turn")
                finish = "tool_calls" if stop_reason == "tool_use" else "stop"
                usage = event.get("usage", {})
                yield _sse(chat_id, model, {}, finish, {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)})

        yield "data: [DONE]\n\n"

    return Response(generate(), content_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _claude_convert_messages(messages):
    result = []
    for msg in messages:
        role, content = msg["role"], msg.get("content", "")
        if role == "tool":
            result.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": msg.get("tool_call_id", ""), "content": content if isinstance(content, str) else json.dumps(content)}]})
        elif role == "assistant" and msg.get("tool_calls"):
            blocks = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in msg["tool_calls"]:
                fn = tc.get("function", {})
                try:
                    parsed = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                blocks.append({"type": "tool_use", "id": tc.get("id", f"toolu_{uuid.uuid4().hex[:24]}"), "name": fn.get("name", ""), "input": parsed})
            result.append({"role": "assistant", "content": blocks})
        elif role in ("user", "assistant"):
            if isinstance(content, str) and content:
                result.append({"role": role, "content": content})
            elif isinstance(content, list):
                result.append({"role": role, "content": content})
    return result


# ══════════════════════════════════════════════════
#  Shared helpers
# ══════════════════════════════════════════════════

def _build_response(model, message, usage, finish_reason):
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:29]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
            "total_tokens": usage.get("total_tokens", usage.get("input_tokens", 0) + usage.get("output_tokens", 0)),
        },
    }


def _sse(chat_id, model, delta, finish_reason=None, usage=None):
    chunk = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage:
        chunk["usage"] = {
            "prompt_tokens": usage.get("input_tokens", usage.get("prompt_tokens", 0)),
            "completion_tokens": usage.get("output_tokens", usage.get("completion_tokens", 0)),
            "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        }
    return f"data: {json.dumps(chunk)}\n\n"


@app.route("/v1/models", methods=["GET"])
def list_models():
    models = [{"id": m, "object": "model", "owned_by": "anthropic"} for m in sorted(CLAUDE_MODELS)]
    models += [{"id": m, "object": "model", "owned_by": "openai"} for m in sorted(CODEX_MODELS)]
    return jsonify({"object": "list", "data": models})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


PORT = int(os.environ.get("PORT", 5010))

if __name__ == "__main__":
    print("=" * 50)
    print("  Subscription API Server")
    print("  OpenAI Chat Completions compatible")
    print("=" * 50)
    print()
    print("  POST /v1/chat/completions")
    print()
    print(f"  Claude ({claude.account_count} account(s)):")
    for m in sorted(CLAUDE_MODELS):
        print(f"    {m}")
    print()
    print(f"  GPT ({codex.account_count} account(s)):")
    for m in sorted(CODEX_MODELS):
        print(f"    {m}")
    print()
    print(f"  base_url = http://localhost:{PORT}/v1")
    print("=" * 50)

    app.run(host="0.0.0.0", port=PORT, debug=False)
