import asyncio
import json

import gigachat_proxy
from gigachat_proxy import _credential, _gigachat_schema, _to_gigachat, health


def test_health_returns_503_when_oauth_fails(monkeypatch):
    async def fail_token():
        raise RuntimeError("oauth failed")

    monkeypatch.setattr(gigachat_proxy, "_access_token", fail_token)
    response = asyncio.run(health())
    assert response.status_code == 503
    assert json.loads(response.body) == {
        "ok": False,
        "oauth": False,
        "error": "oauth failed",
    }


def test_credential_prefers_environment(monkeypatch):
    monkeypatch.setenv("GIGACHAT_CREDENTIALS", "env-credential")
    assert _credential() == "env-credential"


def test_schema_drops_openai_only_fields_and_flattens_union():
    schema = {
        "type": "object",
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "properties": {
            "value": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "value",
            }
        },
    }
    result = _gigachat_schema(schema)
    assert "$schema" not in result
    assert result["properties"]["value"]["type"] == "string"


def test_to_gigachat_filters_tools_and_converts_tool_messages():
    payload = {
        "model": "GigaChat-2-Max",
        "messages": [
            {"role": "user", "content": "Find docs"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "lookup_record", "arguments": '{"id":42}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "lookup_record", "content": "{\"ok\":true}"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "lookup_record", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "send_notification", "parameters": {"type": "object"}}},
        ],
        "tool_choice": "auto",
        "stream": True,
    }
    result, client_stream = _to_gigachat(payload)
    assert client_stream is True
    assert result["stream"] is False
    assert [f["name"] for f in result["functions"]] == ["lookup_record", "send_notification"]
    assert result["messages"][1]["function_call"]["arguments"] == {"id": 42}
    assert result["messages"][2]["role"] == "function"
    json.loads(result["messages"][2]["content"])
