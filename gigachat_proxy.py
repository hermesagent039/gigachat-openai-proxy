#!/usr/bin/env python3
"""Local OpenAI-compatible GigaChat proxy with OAuth refresh."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

PROXY_HOME = Path(os.environ.get("GIGACHAT_PROXY_HOME", str(Path.home() / ".gigachat-proxy")))
ENV_FILE = PROXY_HOME / ".env"
CA_BUNDLE = PROXY_HOME / "certs" / "gigachat-ca-bundle.pem"
OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
API_BASE = "https://gigachat.devices.sberbank.ru/api/v1"
SCOPE = "GIGACHAT_API_PERS"
# Keep the exposed tool surface explicit: provider requests may contain many
# unrelated tools, while this proxy only forwards the approved compatibility set.
TOOL_ALLOWLIST = {
    "terminal", "execute_code", "process", "read_file", "search_files",
    "skill_view", "skills_list", "clarify",
    "viking_search", "viking_read", "viking_browse",
}

app = FastAPI(title="GigaChat OpenAI-Compatible Proxy")
_token: str | None = None
_expires_at = 0.0
_lock = asyncio.Lock()


def _credential() -> str:
    if not ENV_FILE.exists():
        raise RuntimeError("GigaChat proxy .env is missing")
    for raw in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GIGACHAT_CREDENTIALS":
            value = value.strip().strip("\"").strip("'")
            if value:
                return value
    raise RuntimeError("GIGACHAT_CREDENTIALS is not configured")


async def _access_token() -> str:
    global _token, _expires_at
    now = time.time()
    if _token and now < _expires_at - 60:
        return _token
    async with _lock:
        now = time.time()
        if _token and now < _expires_at - 60:
            return _token
        headers = {
            "Authorization": f"Basic {_credential()}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=40, verify=str(CA_BUNDLE)) as client:
            response = await client.post(OAUTH_URL, headers=headers, data={"scope": SCOPE})
        if response.status_code != 200:
            raise RuntimeError(f"GigaChat OAuth failed with HTTP {response.status_code}")
        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise RuntimeError("GigaChat OAuth response has no access_token")
        expiry = float(payload.get("expires_at") or 0)
        if expiry > 10_000_000_000:
            expiry /= 1000.0
        _token = token
        _expires_at = expiry if expiry > now else now + 25 * 60
        return token


async def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {await _access_token()}", "Accept": "application/json"}


@app.get("/health")
async def health() -> dict[str, object]:
    try:
        await _access_token()
        return {"ok": True, "oauth": True}
    except Exception as exc:
        return {"ok": False, "oauth": False, "error": str(exc)}


@app.get("/v1/models")
async def models() -> Response:
    try:
        async with httpx.AsyncClient(timeout=40, verify=str(CA_BUNDLE)) as client:
            upstream = await client.get(f"{API_BASE}/models", headers=await _headers())
        return Response(upstream.content, status_code=upstream.status_code,
                        media_type=upstream.headers.get("content-type", "application/json"))
    except Exception as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "proxy_error"}}, status_code=502)


async def _stream_body(upstream: httpx.Response, client: httpx.AsyncClient) -> AsyncIterator[bytes]:
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    finally:
        await upstream.aclose()
        await client.aclose()


def _parse_arguments(value: object) -> object:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {"value": value}
    return value if value is not None else {}


def _function_result_content(value: object) -> str:
    """GigaChat requires function results to be a valid JSON string."""
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    try:
        json.loads(value)
        return value
    except json.JSONDecodeError:
        return json.dumps({"output": value}, ensure_ascii=False)


def _gigachat_schema(value: object) -> object:
    """Reduce modern JSON Schema to the subset accepted by GigaChat."""
    if isinstance(value, list):
        return [_gigachat_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    schema = dict(value)
    alternatives = schema.pop("oneOf", None) or schema.pop("anyOf", None)
    if alternatives:
        # GigaChat rejects union schemas. Prefer the first non-null branch;
        # The client still validates the actual tool call before execution.
        branch = next(
            (item for item in alternatives if isinstance(item, dict) and item.get("type") != "null"),
            alternatives[0],
        )
        merged = dict(branch) if isinstance(branch, dict) else {}
        for key, item in schema.items():
            if key not in {"default"}:
                merged.setdefault(key, item)
        schema = merged

    schema.pop("$schema", None)
    schema.pop("$id", None)
    schema.pop("examples", None)
    schema.pop("nullable", None)
    if "const" in schema:
        schema["enum"] = [schema.pop("const")]
    if isinstance(schema.get("type"), list):
        schema["type"] = next((item for item in schema["type"] if item != "null"), "string")

    return {key: _gigachat_schema(item) for key, item in schema.items()}


def _to_gigachat(payload: dict) -> tuple[dict, bool]:
    """Translate modern OpenAI tool calling to GigaChat legacy functions."""
    data = dict(payload)
    client_stream = bool(data.get("stream"))
    data["stream"] = False
    data.pop("stream_options", None)
    data.pop("reasoning_effort", None)
    data.pop("parallel_tool_calls", None)
    data.pop("response_format", None)

    tools = data.pop("tools", None) or []
    if tools:
        data["functions"] = []
        for tool in tools:
            if tool.get("type") != "function" or not tool.get("function"):
                continue
            function = dict(tool["function"])
            if function.get("name") not in TOOL_ALLOWLIST:
                continue
            if function.get("name") == "terminal":
                function["description"] = (function.get("description") or "") + (
                    "\nCalendar fast path: for two or more Google Calendar events, first list once, "
                    "then run google_api.py calendar batch-create once with repeated "
                    "--event 'SUMMARY|START|END|LOCATION' arguments, then list once to verify. "
                    "Prefer repeated --event over --events-json to avoid shell quoting errors. "
                    "Never call calendar create separately for each event."
                )
            function["parameters"] = _gigachat_schema(function.get("parameters") or {"type": "object", "properties": {}})
            data["functions"].append(function)
        choice = data.pop("tool_choice", "auto")
        if isinstance(choice, dict):
            name = ((choice.get("function") or {}).get("name"))
            data["function_call"] = {"name": name} if name else "auto"
        elif choice in ("none", "auto"):
            data["function_call"] = choice
        else:
            data["function_call"] = "auto"
    else:
        data.pop("tool_choice", None)

    converted = []
    pending_call: tuple[str, str] | None = None
    for original in data.get("messages", []):
        message = dict(original)
        role = message.get("role")
        if role == "assistant" and message.get("tool_calls"):
            # GigaChat supports one legacy function_call per assistant message.
            # Keep the first call and only accept its immediately following
            # tool result; old orphaned results poison the whole request.
            call = message["tool_calls"][0]
            function = call.get("function") or {}
            call_id = call.get("id") or ""
            name = function.get("name") or "unknown_function"
            converted.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "function_call": {
                    "name": name,
                    "arguments": _parse_arguments(function.get("arguments")),
                },
            })
            pending_call = (call_id, name)
        elif role == "tool":
            call_id = message.get("tool_call_id") or ""
            if pending_call and (not pending_call[0] or call_id == pending_call[0]):
                converted.append({
                    "role": "function",
                    "name": message.get("name") or pending_call[1],
                    "content": _function_result_content(message.get("content") or ""),
                })
            # Drop historical orphan tool results: GigaChat rejects them with
            # "every assistant function result must have ... function call".
            pending_call = None
        else:
            if pending_call:
                # An unfulfilled historical function call is also invalid.
                previous = converted[-1]
                previous.pop("function_call", None)
                if not previous.get("content"):
                    converted.pop()
                pending_call = None
            message.pop("tool_calls", None)
            message.pop("tool_call_id", None)
            converted.append(message)
    if pending_call:
        previous = converted[-1]
        previous.pop("function_call", None)
        if not previous.get("content"):
            converted.pop()
    data["messages"] = converted
    return data, client_stream


def _from_gigachat(data: dict) -> dict:
    """Translate GigaChat legacy function_call to modern OpenAI tool_calls."""
    result = dict(data)
    choices = []
    for raw_choice in data.get("choices", []):
        choice = dict(raw_choice)
        message = dict(choice.get("message") or {})
        function_call = message.pop("function_call", None)
        if function_call:
            arguments = function_call.get("arguments", {})
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False)
            call_id = "call_" + uuid.uuid4().hex
            message["tool_calls"] = [{
                "id": call_id,
                "type": "function",
                "function": {"name": function_call.get("name"), "arguments": arguments},
            }]
            message["content"] = message.get("content") or None
            choice["finish_reason"] = "tool_calls"
        message.pop("functions_state_id", None)
        choice.pop("functions_state_id", None)
        choice["message"] = message
        choices.append(choice)
    result["choices"] = choices
    return result


def _as_sse(data: dict) -> bytes:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    common = {
        "id": data.get("id") or "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion.chunk",
        "created": data.get("created") or int(time.time()),
        "model": data.get("model") or "GigaChat",
    }
    chunks = [{**common, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}]
    if message.get("tool_calls"):
        call = message["tool_calls"][0]
        chunks.append({**common, "choices": [{"index": 0, "delta": {"tool_calls": [{
            "index": 0, "id": call["id"], "type": "function", "function": call["function"],
        }]}, "finish_reason": None}]})
    elif message.get("content") is not None:
        chunks.append({**common, "choices": [{"index": 0, "delta": {"content": message.get("content")}, "finish_reason": None}]})
    chunks.append({**common, "choices": [{"index": 0, "delta": {}, "finish_reason": choice.get("finish_reason") or "stop"}]})
    return ("".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n").encode("utf-8")


@app.post("/v1/{endpoint:path}")
async def forward(endpoint: str, request: Request) -> Response:
    try:
        body = await request.body()
        if endpoint == "chat/completions":
            payload, client_stream = _to_gigachat(json.loads(body))
            headers = await _headers()
            headers["Content-Type"] = "application/json"
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=40), verify=str(CA_BUNDLE)) as client:
                upstream = await client.post(f"{API_BASE}/{endpoint}", headers=headers,
                                             content=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            if upstream.status_code >= 400:
                return Response(upstream.content, status_code=upstream.status_code,
                                media_type=upstream.headers.get("content-type", "application/json"))
            translated = _from_gigachat(upstream.json())
            if client_stream:
                return Response(_as_sse(translated), status_code=200, media_type="text/event-stream")
            return JSONResponse(translated)

        headers = await _headers()
        headers["Content-Type"] = request.headers.get("content-type", "application/json")
        client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=40), verify=str(CA_BUNDLE))
        upstream_request = client.build_request("POST", f"{API_BASE}/{endpoint}", headers=headers, content=body)
        upstream = await client.send(upstream_request, stream=True)
        media_type = upstream.headers.get("content-type", "application/json")
        if upstream.status_code >= 400:
            content = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            return Response(content, status_code=upstream.status_code, media_type=media_type)
        if "text/event-stream" in media_type:
            return StreamingResponse(_stream_body(upstream, client), status_code=upstream.status_code,
                                     media_type="text/event-stream")
        content = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        return Response(content, status_code=upstream.status_code, media_type=media_type)
    except Exception as exc:
        return JSONResponse({"error": {"message": str(exc), "type": "proxy_error"}}, status_code=502)
