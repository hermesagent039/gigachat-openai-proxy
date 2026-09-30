# GigaChat OpenAI-Compatible Proxy

Универсальный HTTP-прокси между клиентами с OpenAI-совместимым API и GigaChat.

Проект не привязан к конкретному приложению или фреймворку-клиенту.

```text
OpenAI-compatible client
          │
          ▼
GigaChat OpenAI-Compatible Proxy :8766
          │
          ├── OAuth GigaChat
          ├── TLS через CA bundle
          ├── адаптация chat completions
          └── адаптация tools/function calls
          │
          ▼
GigaChat API
```

## Возможности

- OAuth-токен GigaChat с кешированием до истечения срока действия;
- OpenAI-совместимые endpoints:
  - `GET /v1/models`;
  - `POST /v1/chat/completions`;
- поддержка обычных и streaming-запросов;
- преобразование OpenAI `tools`/`tool_calls` в формат GigaChat `functions`/`function_call`;
- нормализация JSON Schema для GigaChat;
- преобразование вызовов функций и результатов инструментов;
- проверка реального OAuth через `GET /health`;
- подключение пользовательского CA bundle для TLS GigaChat.

## Конфигурация

Прокси использует:

```text
GIGACHAT_PROXY_HOME/certs/gigachat-ca-bundle.pem
GIGACHAT_CREDENTIALS из окружения
```

Переменная окружения по умолчанию:

```text
GIGACHAT_PROXY_HOME=~/.gigachat-proxy
```

В корне проекта создайте `.env` на основе `.env.example`:

```env
GIGACHAT_CREDENTIALS=ваш-локальный-credential
```

`.env` используется Compose как `env_file` и передаётся процессу напрямую; монтировать его
в контейнер через volume не требуется. CA bundle GigaChat уже входит в Docker image.

Подготовка локального файла:

```bash
cp .env.example .env
chmod 600 .env
```

Не добавляйте `.env` в Git.

## API

### Проверка proxy и OAuth

```bash
curl -fsS http://127.0.0.1:18786/health
```

Успешный ответ:

```json
{"ok":true,"oauth":true}
```

HTTP 503 с `"oauth": false` означает, что proxy запущен, но credential не принят GigaChat.

### Список моделей

```bash
curl -fsS http://127.0.0.1:18786/v1/models
```

### Chat completions

```bash
curl -fsS -X POST \
  http://127.0.0.1:18786/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "GigaChat-2-Max",
    "messages": [
      {"role": "user", "content": "Кратко объясни, что делает этот proxy."}
    ],
    "temperature": 0.4,
    "max_tokens": 1200
  }'
```

## Вариант 1: локальная сборка из репозитория

Требуются Docker Engine и Compose v2.

```bash
git clone https://github.com/hermesagent039/gigachat-openai-proxy.git
cd gigachat-openai-proxy

cp .env.example .env
chmod 600 .env

sudo docker compose config --quiet
sudo docker compose up -d --build
```

Проверка:

```bash
curl -fsS http://127.0.0.1:18786/health
sudo docker compose ps
sudo docker compose logs --no-log-prefix gigachat-proxy
```

Остановка:

```bash
sudo docker compose down
```

Порт можно изменить без редактирования Compose:

```bash
GIGACHAT_PROXY_PORT=8766 sudo -E docker compose up -d --build
```

## Вариант 2: запуск готового образа из Docker Hub

Образ публикуется GitHub Actions в Docker Hub. CA bundle GigaChat уже включён в образ и отдельно скачивать или монтировать его не нужно. Подставьте имя пользователя Docker Hub вместо `<DOCKERHUB_USERNAME>`.

```bash
mkdir -p "$HOME/.gigachat-proxy"
cp .env.example "$HOME/.gigachat-proxy/.env"
chmod 600 "$HOME/.gigachat-proxy/.env"

sudo docker run -d \
  --name gigachat-openai-proxy \
  --restart unless-stopped \
  -p 18786:8766 \
  --env-file "$HOME/.gigachat-proxy/.env" \
  <DOCKERHUB_USERNAME>/gigachat-openai-proxy:main
```

Проверка образа:

```bash
curl -fsS http://127.0.0.1:18786/health
sudo docker ps --filter name=gigachat-openai-proxy
```

Запуск готового образа через Compose:

```yaml
services:
  gigachat-proxy:
    image: <DOCKERHUB_USERNAME>/gigachat-openai-proxy:main
    ports:
      - "18786:8766"
    env_file:
      - ./.env
    restart: unless-stopped
```

Для воспроизводимого запуска вместо `main` используйте SHA-тег, опубликованный workflow, например:

```text
<DOCKERHUB_USERNAME>/gigachat-openai-proxy:sha-<COMMIT_SHA>
```

## Docker Hub и GitHub Actions

Workflow находится в `.github/workflows/ci.yml`.

Для публикации используются только GitHub Actions secrets:

```text
DOCKERHUB_USERNAME
DOCKERHUB_TOKEN
```

Значения не находятся в исходниках, workflow, Dockerfile, Git history или README.

CI выполняет:

1. установку зависимостей;
2. unit-тесты;
3. проверку синтаксиса Python;
4. проверку Docker Compose;
5. сборку Docker image;
6. публикацию image в Docker Hub для `main`, tag push и ручного запуска;
7. проверку опубликованного image digest через Docker Buildx.

Pull Request проверяет и собирает image, но не выполняет Docker Hub login и push.

## Разработка

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m py_compile gigachat_proxy.py
```

## Безопасность

- credentials передаются через локальный `.env` или GitHub Actions secrets;
- credentials не попадают в Docker image;
- CA bundle входит в Docker image и не требует отдельного скачивания;
- `.env` исключён из Git и Docker build context;
- image запускается от непривилегированного пользователя по умолчанию;
- не публикуйте proxy в интернет без дополнительной аутентификации и сетевых ограничений;
- `/health` не скрывает ошибку OAuth: `oauth: false` считается неготовым состоянием provider-интеграции.
