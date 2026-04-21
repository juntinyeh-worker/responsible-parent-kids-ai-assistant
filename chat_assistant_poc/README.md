# chat_assistant_poc

Voice AI assistant using OpenAI Realtime API via WebRTC.

## Architecture

```
Browser (mic/speaker via WebRTC)
    │                          │
    │ WebRTC (direct audio)    │ HTTPS (token + logs)
    ▼                          ▼
OpenAI Realtime API    CloudFront ── /api/* ──► ALB ──► ECS Fargate (FastAPI)
                           │                                  │
                           │ static/*                         ├── Secrets Manager (API key)
                           ▼                                  ├── S3 (text logs)
                       S3 Bucket                              └── Cognito (auth)
```

Audio flows directly between the browser and OpenAI via WebRTC — the server only creates ephemeral session tokens and receives client-reported conversation logs. This means lower latency but no server-side voice capture.

## Features

- Direct browser-to-OpenAI voice streaming via WebRTC
- Ephemeral token generation (GA endpoint with beta fallback)
- Client-reported text logging (transcripts sent to backend after each turn)
- Log browsing frontend with date-based navigation
- Cognito authentication (numpad PIN login)
- API key stored in AWS Secrets Manager (ECS) or `.env` (local)
- IP-based rate limiting with periodic cleanup

## Project Structure

```
config.py              # Environment config (local/aws, S3 buckets, OpenAI key)
main.py                # FastAPI app — session creation, logging APIs, static serving
session.py             # OpenAI Realtime API ephemeral token generation
logging_service.py     # Conversation + server turn logging, log browsing queries
log_shipper.py         # Background log shipping utility
api_secrets.py         # API key retrieval (env var or Secrets Manager)
auth.py                # Cognito authentication router
prompts.py             # Child-safety system prompt
rate_limit.py          # IP-based sliding window rate limiter
static/
  index.html           # Log browsing entry page (session list)
  log.html             # Date-based log browser
  chat.html            # Voice chat page (WebRTC)
  login.html           # Numpad PIN login (Cognito)
  app.js               # WebRTC client — SDP exchange, data channel events, transcript capture
infra/
  ecs-stack.yaml       # CloudFormation — VPC, ALB, ECS, CloudFront, Cognito, S3
  deploy.sh            # Build + deploy script
  deploy-frontend.sh   # S3 sync + CloudFront invalidation
tests/
  backend/             # Unit tests (config, health, logging, rate limit, session)
```

## Local Development

```bash
cp .env.example .env
# Set OPENAI_API_KEY

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/chat.html

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DEPLOY_ENV` | `local` | `local` or `aws` |
| `OPENAI_API_KEY` | — | Required in local mode |
| `OPENAI_REALTIME_MODEL` | — | Override model (empty = auto-detect GA/beta) |
| `SECRETS_NAME` | — | AWS Secrets Manager secret name (ECS mode) |
| `S3_LOG_BUCKET` | — | S3 bucket for text logs |
| `S3_LOG_PREFIX` | `conversation-logs` | S3 key prefix for logs |
| `S3_AUDIO_PREFIX` | `voice-responses` | S3 key prefix (reserved, not used in WebRTC mode) |
| `LOCAL_LOG_DIR` | `./logs` | Local log directory (dev mode) |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check |
| `POST` | `/api/session/create` | Create ephemeral WebRTC token |
| `POST` | `/api/session/log` | Client-reported conversation log |
| `GET` | `/api/logs/sessions` | List all logged sessions |
| `GET` | `/api/logs/dates` | List dates with logs |
| `GET` | `/api/logs/dates/{y}/{m}/{d}/sessions` | Sessions for a date |
| `GET` | `/api/logs/sessions/{id}/turns` | Turns for a session |
| `POST` | `/api/auth/login` | Cognito authentication |

## Deployment

```bash
# Build and push Docker image, then update ECS stack
cd infra && bash deploy.sh

# Update frontend only
bash deploy-frontend.sh
```

## Tests

```bash
OPENAI_API_KEY=sk-test pip install -r requirements.txt
OPENAI_API_KEY=sk-test pytest tests/backend/ -v
```

## Differences from chat_assistant_aws

| Feature | AWS (WebSocket) | POC (WebRTC) |
|---|---|---|
| Audio path | Browser ↔ Server ↔ Bedrock | Browser ↔ OpenAI (direct) |
| Voice replay | ✅ Server stores PCM | ❌ Audio never hits server |
| Text logging | Server captures from stream | Client reports after each turn |
| AI model | Amazon Nova Sonic | OpenAI GPT Realtime |
| System prompt override | SSM Parameter Store | Hardcoded |
| API key management | IAM role (Bedrock) | Secrets Manager |
