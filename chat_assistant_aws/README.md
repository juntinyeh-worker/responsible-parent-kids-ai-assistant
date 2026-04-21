# chat_assistant_aws

AWS-native voice AI assistant using Amazon Bedrock Nova Sonic via WebSocket.

## Architecture

```
Browser (mic 16kHz / speaker 24kHz)
    │ WebSocket (JSON + base64 audio)
    ▼
CloudFront ── /api/* ──► ALB ──► ECS Fargate (FastAPI)
    │                                  │
    │ static/*                         ├── Nova Sonic (Bedrock)
    ▼                                  ├── S3 (logs + voice storage)
S3 Bucket                              └── SSM Parameter Store
```

Audio flows through the server — the FastAPI backend proxies bidirectional audio between the browser and Nova Sonic. This enables server-side logging of both text and voice responses.

## Features

- Bidirectional voice streaming via WebSocket (`/api/ws/audio`)
- Server-side text I/O logging (captures user and assistant text from Nova Sonic events)
- Voice response storage in S3 (raw PCM, 24kHz mono) with replay endpoint
- Log browsing frontend with date-based navigation and voice replay
- SSM Parameter Store override for system prompt (no redeploy needed)
- IP-based rate limiting with periodic cleanup
- Cognito authentication (numpad PIN login)

## Project Structure

```
config.py              # Environment config (local/aws, S3 buckets, Bedrock region)
main.py                # FastAPI app — WebSocket proxy, REST APIs, static serving
session.py             # Nova Sonic bidirectional stream session management
logging_service.py     # Conversation + server turn logging, voice upload, log browsing queries
log_shipper.py         # Background log shipping utility
prompts.py             # System prompt with SSM override
ssm_config.py          # SSM Parameter Store helper
rate_limit.py          # IP-based sliding window rate limiter
static/
  index.html           # Log browsing entry page (session list)
  log.html             # Date-based log browser with voice replay
  chat.html            # Voice chat page
  login.html           # Numpad PIN login (Cognito)
  app.js               # WebSocket audio client
infra/
  ecs-stack.yaml       # CloudFormation — VPC, ALB, ECS, CloudFront, Cognito, S3
  deploy.sh            # Build + deploy script
  deploy-frontend.sh   # S3 sync + CloudFront invalidation
tests/
  backend/             # Unit tests (config, health, logging, rate limit, session)
  e2e/                 # E2E tests (S3 log production, API access, frontend)
```

## Local Development

```bash
cp .env.example .env
# Set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, BEDROCK_REGION

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/chat.html

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DEPLOY_ENV` | `local` | `local` or `aws` |
| `BEDROCK_REGION` | `us-east-1` | AWS region for Nova Sonic |
| `NOVA_VOICE_ID` | `tiffany` | Nova Sonic voice |
| `S3_LOG_BUCKET` | — | S3 bucket for logs and voice storage |
| `S3_LOG_PREFIX` | `conversation-logs` | S3 key prefix for text logs |
| `S3_AUDIO_PREFIX` | `voice-responses` | S3 key prefix for voice PCM files |
| `LOCAL_LOG_DIR` | `./logs` | Local log directory (dev mode) |
| `LOG_LEVEL` | `INFO` | Python log level |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `WS` | `/api/ws/audio` | Bidirectional audio streaming |
| `POST` | `/api/session/log` | Client-side conversation log |
| `GET` | `/api/logs/sessions` | List all logged sessions |
| `GET` | `/api/logs/dates` | List dates with logs |
| `GET` | `/api/logs/dates/{y}/{m}/{d}/sessions` | Sessions for a date |
| `GET` | `/api/logs/sessions/{id}/turns` | Turns for a session |
| `GET` | `/api/audio/{session_id}/{turn}?date=Y/M/D` | Voice replay (raw PCM) |

## Deployment

```bash
# Build and push Docker image, then update ECS stack
cd infra && bash deploy.sh

# Update frontend only
bash deploy-frontend.sh
```

## Tests

```bash
pip install -r requirements.txt moto[s3] httpx
pytest tests/backend/ -v          # Unit tests
pytest tests/e2e/ -v              # E2E tests (requires AWS credentials)
```
