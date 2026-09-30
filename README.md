# GigaChat OAuth Proxy

Standalone Python proxy between Hermes/OpenAI-compatible clients and the GigaChat API.

```text
Hermes / OpenAI-compatible client
              │
              ▼
       gigachat-proxy :8766
              │  OAuth + API adaptation
              ▼
       GigaChat OAuth/API
```

## Why this proxy exists

GigaChat's API and tool-calling format differ from the OpenAI-compatible interface used by Hermes and other clients. The proxy keeps that compatibility logic in one service:

- obtains and caches GigaChat OAuth access tokens;
- validates TLS with the configured GigaChat CA bundle;
- exposes `/v1/models` and `/v1/chat/completions`;
- converts OpenAI `tools` / `tool_calls` to GigaChat legacy `functions` / `function_call`;
- filters tools through an explicit allowlist;
- supports streaming responses at the compatibility boundary;
- exposes `/health`, where `oauth: true` means that the credential was actually accepted;
- provides read-only OpenViking tools: `viking_search`, `viking_read`, `viking_browse`.

This repository contains the proxy only. Prompt Vault is an existing client/integration and is not part of this repository.

## Configuration

The proxy reads `GIGACHAT_CREDENTIALS` from `${HERMES_HOME}/.env` and the CA bundle from `${HERMES_HOME}/certs/gigachat-ca-bundle.pem`.

For local Compose:

```bash
mkdir -p secrets
cp secrets/gigachat.env.example secrets/gigachat.env
# Put the actual GigaChat credential into secrets/gigachat.env locally.
# Put the actual CA bundle into secrets/gigachat-ca-bundle.pem.
chmod 600 secrets/gigachat.env
```

Never commit either real file or print the credential to logs/chat.

## Docker Compose

Requirements: Docker Engine and Compose v2.

```bash
docker compose config --quiet
docker compose up -d --build
curl -fsS http://127.0.0.1:8766/health
docker compose logs --no-log-prefix gigachat-proxy
docker compose down
```

The service is published on `0.0.0.0:18786` by default through the Compose port mapping. Override with `GIGACHAT_PROXY_PORT` if required.

Expected successful health response:

```json
{"ok":true,"oauth":true}
```

A JSON response with HTTP 200 and `"oauth": false` is a failed proxy health state, not a successful OAuth check.

## API

- `GET /health` — liveness plus real OAuth validation;
- `GET /v1/models` — GigaChat model list;
- `POST /v1/chat/completions` — OpenAI-compatible chat endpoint.

Example:

```bash
curl -fsS http://127.0.0.1:8766/v1/models
```

## Development and verification

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m py_compile gigachat_proxy.py
```

The test suite validates request translation and tool/schema filtering without contacting GigaChat or requiring secrets.

## GitHub Actions

`.github/workflows/ci.yml` runs on pushes, pull requests, and manual dispatch. It:

1. installs Python dependencies;
2. runs unit tests and syntax checks;
3. validates Docker Compose;
4. builds the proxy image with Docker Buildx;
5. starts the Compose service with CI placeholder files and checks container health.

The workflow does not publish an image and does not require GigaChat credentials. Registry publishing can be added later using GitHub Actions secrets.

## Security

- credentials and CA files are excluded by `.gitignore` and `.dockerignore`;
- the image runs as a non-root user by default;
- Compose uses `user: "0:0"` only to read root-owned bind-mounted secret files, while the image itself remains non-root outside this local bind-mount mode;
- tool execution is restricted by `TOOL_ALLOWLIST`;
- do not expose the proxy publicly without authentication and network policy.
