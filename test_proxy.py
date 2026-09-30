import json

from gigachat_proxy import _gigachat_schema, _to_gigachat


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
                    "function": {"name": "viking_search", "arguments": '{"query":"docs"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "call-1", "name": "viking_search", "content": "{\"ok\":true}"},
        ],
        "tools": [
            {"type": "function", "function": {"name": "viking_search", "parameters": {"type": "object"}}},
            {"type": "function", "function": {"name": "not_allowed", "parameters": {"type": "object"}}},
        ],
        "tool_choice": "auto",
        "stream": True,
    }
    result, client_stream = _to_gigachat(payload)
    assert client_stream is True
    assert result["stream"] is False
    assert [f["name"] for f in result["functions"]] == ["viking_search"]
    assert result["messages"][1]["function_call"]["arguments"] == {"query": "docs"}
    assert result["messages"][2]["role"] == "function"
    json.loads(result["messages"][2]["content"])
