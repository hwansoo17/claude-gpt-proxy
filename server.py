"""
Codex Proxy Server — OpenAI Chat Completions compatible.

Routes GPT requests through Codex (ChatGPT OAuth). No API keys needed.

Endpoint: POST /v1/chat/completions
Format:   Standard OpenAI Chat Completions (in & out)
"""

import json
import os
import uuid
import time
from dotenv import load_dotenv
from flask import Flask, request, Response, jsonify

load_dotenv()

from codex_client import CodexClient

app = Flask(__name__)

# ── Models ──────────────────────────────────────

CODEX_MODELS = {
    "gpt-5.4", "gpt-5.4-mini",
    "gpt-5.3-codex", "gpt-5.3-codex-spark",
    "gpt-5.2", "gpt-5.2-codex",
    "gpt-5.1", "gpt-5.1-codex", "gpt-5.1-codex-max",
    "gpt-5", "gpt-5-codex", "gpt-5-codex-mini",
}

DEFAULT_MODEL = "gpt-5.3-codex"

# ── Client ──────────────────────────────────────

codex = CodexClient()

# ── Route ────────────────────────────────────────

@app.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    body = request.get_json()
    model = body.get("model", DEFAULT_MODEL)
    stream = body.get("stream", False)

    if model not in CODEX_MODELS:
        return jsonify({"error": {"message": f"Unknown model: {model}. Available: {', '.join(sorted(CODEX_MODELS))}", "type": "invalid_request_error"}}), 400

    # Extract system + chat messages
    system_text = None
    chat_messages = []
    for msg in body.get("messages", []):
        if msg["role"] == "system":
            system_text = msg["content"]
        else:
            chat_messages.append(msg)

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
    has_function_calls = False
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
                has_function_calls = True
                current_tool = {"id": item.get("call_id", f"call_{uuid.uuid4().hex[:24]}"), "type": "function", "function": {"name": item.get("name", ""), "arguments": ""}}
                tool_calls.append(current_tool)
        elif t == "response.function_call_arguments.delta":
            if current_tool:
                current_tool["function"]["arguments"] += data.get("delta", "")
        elif t == "response.output_item.done":
            item = data.get("item", {})
            if item.get("type") == "web_search_call":
                action = item.get("action", {})
                query = action.get("query", "")
                queries = action.get("queries", [])
                tool_calls.append({"id": item.get("id", f"ws_{uuid.uuid4().hex[:24]}"), "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": query, "queries": queries}, ensure_ascii=False)}})
        elif t == "response.completed":
            usage = data.get("response", {}).get("usage", {})

    message = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls

    return jsonify(_build_response(model, message, usage, "tool_calls" if has_function_calls else "stop"))


def _codex_stream(codex_body, model):
    def generate():
        chat_id = f"chatcmpl-{uuid.uuid4().hex[:29]}"
        tool_calls_by_item = {}
        has_function_calls = False
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
                    has_function_calls = True
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

            elif t == "response.output_item.done":
                item = data.get("item", {})
                if item.get("type") == "web_search_call":
                    tool_id = item.get("id", f"ws_{uuid.uuid4().hex[:24]}")
                    action = item.get("action", {})
                    query = action.get("query", "")
                    queries = action.get("queries", [])
                    yield _sse(chat_id, model, {"tool_calls": [{"index": tool_index, "id": tool_id, "type": "function", "function": {"name": "web_search", "arguments": json.dumps({"query": query, "queries": queries}, ensure_ascii=False)}}]})
                    tool_index += 1

            elif t == "response.completed":
                finish = "tool_calls" if has_function_calls else "stop"
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
                codex_parts = []
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    pt = p.get("type", "")
                    if pt == "text":
                        codex_parts.append({"type": text_type, "text": p.get("text", "")})
                    elif pt == "image_url":
                        url = p.get("image_url", {})
                        if isinstance(url, dict):
                            url = url.get("url", "")
                        codex_parts.append({"type": "input_image", "image_url": url})
                if codex_parts:
                    items.append({"type": "message", "role": role, "content": codex_parts})
    return items


def _codex_convert_tools(tools):
    result = []
    for t in tools:
        if t.get("type") == "function":
            fn = t["function"]
            result.append({"type": "function", "name": fn["name"], "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object", "properties": {}})})
        else:
            result.append(t)
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
    models = [{"id": m, "object": "model", "owned_by": "openai"} for m in sorted(CODEX_MODELS)]
    return jsonify({"object": "list", "data": models})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


PORT = int(os.environ.get("PORT", 5010))


if __name__ == "__main__":
    print("=" * 50)
    print("  Codex Proxy Server")
    print("  OpenAI Chat Completions compatible")
    print("=" * 50)
    print()
    print("  POST /v1/chat/completions")
    print()
    print(f"  GPT ({codex.account_count} account(s)):")
    for m in sorted(CODEX_MODELS):
        print(f"    {m}")
    print()
    print(f"  base_url = http://localhost:{PORT}/v1")
    print("=" * 50)

    app.run(host="0.0.0.0", port=PORT, debug=False)
