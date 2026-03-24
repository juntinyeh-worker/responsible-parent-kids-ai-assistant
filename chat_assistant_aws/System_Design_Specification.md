# System Design Specification: Continuous Voice Conversational AI Tutor for Children

**Version:** 1.0
**Date:** 2026-03-21
**Status:** Draft
**Source:** Child_Voice_Tutor_Technical_Specification.pdf

---

## 1. Introduction

### 1.1 Purpose

This document provides the complete system design specification for a browser-based, continuous voice conversational AI tutor designed for children. It translates the high-level technical specification into actionable architecture, component design, data flows, API contracts, and operational requirements.

### 1.2 Scope

The system enables children to speak naturally with an AI tutor through a browser interface supporting continuous voice interaction, real-time responses, and child-safe conversational controls. The primary language is simplified Mandarin Chinese.

### 1.3 Intended Audience

- Software engineers and architects
- Frontend and backend developers
- DevOps / cloud infrastructure engineers
- QA and security reviewers
- Product managers

### 1.4 Glossary

| Term | Definition |
|------|-----------|
| WebRTC | Web Real-Time Communication protocol for peer-to-peer audio/video streaming |
| Ephemeral Token | A short-lived session credential that expires after a defined TTL |
| VAD | Voice Activity Detection — automatic detection of when a user starts/stops speaking |
| TTS | Text-to-Speech synthesis |
| STT | Speech-to-Text recognition |
| LLM | Large Language Model used for generating conversational responses |

---

## 2. System Objectives

1. Provide a natural, low-latency voice conversation experience for children in a web browser.
2. Ensure all interactions are child-safe with strict content guardrails.
3. Maintain a secure architecture where API credentials are never exposed to the client.
4. Deliver sub-second response latency for a fluid conversational feel.
5. Support graceful degradation when the real-time pipeline is unavailable.

---

## 3. High-Level Architecture

### 3.1 System Context Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                          End User (Child)                           │
│                        Browser on Device                            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ WebRTC Audio Stream (bidirectional)
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                     AWS CloudFront (CDN)                             │
│              Static Frontend (S3) + Edge Caching                    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTPS REST
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    API Gateway (Session API)                         │
│                  Rate limiting, auth, routing                       │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│               AWS Lambda — Session Gateway Service                  │
│  • Ephemeral token generation                                       │
│  • Assistant instruction configuration                              │
│  • Session lifecycle management                                     │
│  • Secrets Manager integration                                      │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ Authenticated API call
                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                   OpenAI Realtime API                                │
│         STT → LLM → TTS (unified streaming pipeline)                │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Data Flow Summary

1. Child opens the browser app served from CloudFront/S3.
2. Frontend requests an ephemeral session token from the Session Gateway (Lambda via API Gateway).
3. Lambda retrieves the OpenAI API key from AWS Secrets Manager, creates a short-lived session token, and returns it.
4. Frontend establishes a WebRTC connection to the OpenAI Realtime API using the ephemeral token.
5. Microphone audio streams continuously to OpenAI; VAD detects speech boundaries.
6. OpenAI processes speech → generates response → synthesizes audio, streamed back to the browser.
7. Browser plays the synthesized audio response in real time.

---

## 4. Technology Stack

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| Frontend Audio | WebRTC, Web Audio API | Low-latency bidirectional audio streaming natively in the browser |
| Frontend App | HTML/CSS/JS (SPA) | Lightweight, no install required, served from S3 |
| Backend Runtime | Node.js (Express) on AWS Lambda | Event-driven, low cold-start with provisioned concurrency |
| API Layer | AWS API Gateway | Managed REST API with throttling, auth, and CORS |
| CDN | AWS CloudFront | Global edge distribution for static assets and low-latency token endpoint |
| Secrets | AWS Secrets Manager | Secure storage and rotation of the OpenAI API key |
| Monitoring | AWS CloudWatch | Centralized logging, metrics, and alarms |
| AI Pipeline | OpenAI Realtime API | Integrated STT + LLM + TTS in a single streaming connection |

---

## 5. Component Design

### 5.1 Frontend Application

#### 5.1.1 Responsibilities

- Capture microphone input via `getUserMedia` and Web Audio API.
- Establish and maintain a WebRTC connection to the OpenAI Realtime API.
- Stream raw audio continuously (no push-to-talk).
- Receive and play synthesized audio responses with minimal buffering.
- Handle interruption: if the child speaks while the AI is responding, stop playback and send the new input.
- Maintain session continuity across brief network interruptions.
- Display a simple, child-friendly UI with visual feedback (e.g., animated avatar indicating listening/speaking state).

#### 5.1.2 State Machine

```
┌──────────┐   user grants mic   ┌────────────┐   token received   ┌─────────────┐
│   IDLE   │ ──────────────────► │ CONNECTING │ ─────────────────► │  LISTENING   │
└──────────┘                     └────────────┘                    └──────┬──────┘
                                                                         │ VAD: speech detected
                                                                         ▼
                                                                  ┌─────────────┐
                                                    ┌──────────── │  SPEAKING    │
                                                    │             │  (child)     │
                                                    │             └──────┬──────┘
                                                    │                    │ VAD: speech ended
                                                    │                    ▼
                                                    │             ┌─────────────┐
                                                    │             │ PROCESSING   │
                                                    │             └──────┬──────┘
                                                    │                    │ audio stream begins
                                                    │                    ▼
                                                    │             ┌─────────────┐
                                                    └──── child   │ RESPONDING   │
                                                     interrupts   │ (AI playing) │
                                                                  └──────┬──────┘
                                                                         │ playback complete
                                                                         ▼
                                                                  ┌─────────────┐
                                                                  │  LISTENING   │
                                                                  └─────────────┘
```

#### 5.1.3 Key Technical Details

- Audio format: 16-bit PCM, 24 kHz sample rate (per OpenAI Realtime API requirements).
- WebRTC ICE candidates handled automatically; STUN/TURN servers provided by OpenAI.
- Audio playback via `AudioContext` with a streaming decode buffer.
- Session heartbeat every 30 seconds to detect connection loss.
- Automatic reconnection with exponential backoff (max 3 retries).

### 5.2 Session Gateway Service (Backend)

#### 5.2.1 Responsibilities

- Generate ephemeral session tokens by calling the OpenAI session creation endpoint.
- Inject child-safety assistant instructions into the session configuration.
- Retrieve and cache the OpenAI API key from Secrets Manager (cache TTL: 5 minutes).
- Enforce rate limiting per client (max 5 session requests per minute per IP).
- Log session metadata (session ID, timestamp, client fingerprint) to CloudWatch.

#### 5.2.2 API Contract

**POST /api/session/create**

Request:
```json
{
  "clientId": "string (anonymous device fingerprint)",
  "mode": "story | science | english | quiz"  // optional, defaults to general
}
```

Response (200):
```json
{
  "sessionToken": "eph_xxxxxxxxxxxx",
  "expiresAt": "2026-03-21T04:00:00Z",
  "sessionId": "sess_xxxxxxxxxxxx",
  "wsEndpoint": "wss://api.openai.com/v1/realtime?..."
}
```

Error Responses:
| Code | Meaning |
|------|---------|
| 429 | Rate limit exceeded |
| 500 | Token generation failed |
| 503 | OpenAI API unavailable — client should activate fallback pipeline |

#### 5.2.3 Assistant Instruction Template

The following system prompt is injected into every session:

```
You are a friendly, patient tutor for children aged 5–12.
- Speak in simplified Mandarin Chinese.
- Keep every response to five sentences or fewer.
- Never discuss violence, politics, religion, or any adult topics.
- If asked about unsafe topics, gently redirect to a fun learning subject.
- Explain concepts using stories, analogies, and examples from nature or daily life.
- Encourage curiosity by asking follow-up questions.
- Match your vocabulary to the child's apparent age and comprehension level.
- Mode: {mode_specific_instructions}
```

Mode-specific instruction variants:

| Mode | Additional Instructions |
|------|----------------------|
| Story | "Tell imaginative stories and invite the child to contribute to the plot." |
| Science | "Explain science concepts with simple experiments the child can try at home." |
| English | "Help the child practice English vocabulary and pronunciation. Mix Mandarin explanations with English target words." |
| Quiz | "Ask age-appropriate trivia questions. Celebrate correct answers and gently explain incorrect ones." |

### 5.3 Security Model

#### 5.3.1 Credential Isolation

```
┌──────────┐                    ┌──────────────┐                  ┌─────────────────┐
│ Browser  │ ── ephemeral ───► │   Lambda     │ ── permanent ──► │ Secrets Manager │
│          │    token only      │              │    API key        │                 │
└──────────┘                    └──────────────┘                  └─────────────────┘
```

- The permanent OpenAI API key is stored exclusively in AWS Secrets Manager.
- The browser never receives or has access to the permanent key.
- Ephemeral tokens have a TTL of 60 seconds and are single-use.
- Lambda retrieves the key at runtime; it is never embedded in code or environment variables.

#### 5.3.2 Transport Security

- All client-server communication over HTTPS (TLS 1.2+).
- WebRTC audio streams encrypted via DTLS-SRTP.
- API Gateway enforces CORS to allow only the application's origin domain.

#### 5.3.3 Rate Limiting and Abuse Prevention

- API Gateway throttle: 100 requests/second burst, 50 requests/second sustained.
- Per-IP session creation limit: 5 per minute.
- CloudWatch alarm on anomalous request patterns (>10x baseline).

---

## 6. Privacy Requirements

| Requirement | Implementation |
|-------------|---------------|
| No raw audio storage | Audio streams are transient; no server-side recording or persistence |
| Anonymized transcripts (optional) | If enabled, transcripts are stripped of PII and stored with a hashed session ID |
| Learning progress tracking (optional) | Requires explicit parental opt-in; data stored in DynamoDB with encryption at rest |
| Parental control | Parent dashboard (optional enhancement) controls data retention and feature toggles |
| Data retention | All optional data auto-deleted after 90 days unless parent extends |
| COPPA alignment | No personal data collected from children without parental consent mechanism |

---

## 7. AWS Deployment Architecture

### 7.1 Infrastructure Diagram

```
                        ┌──────────────┐
                        │  Route 53    │
                        │  (DNS)       │
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │ CloudFront   │
                        │ Distribution │
                        └──┬───────┬───┘
                           │       │
              ┌────────────▼─┐   ┌─▼────────────────┐
              │  S3 Bucket   │   │  API Gateway      │
              │  (Frontend)  │   │  /api/session/*    │
              └──────────────┘   └────────┬──────────┘
                                          │
                                 ┌────────▼──────────┐
                                 │  Lambda Function   │
                                 │  (Session Service) │
                                 └────────┬──────────┘
                                          │
                              ┌───────────┼───────────┐
                              │           │           │
                     ┌────────▼──┐  ┌─────▼─────┐  ┌─▼──────────┐
                     │ Secrets   │  │CloudWatch │  │ DynamoDB   │
                     │ Manager   │  │ Logs +    │  │ (optional  │
                     │           │  │ Metrics   │  │  progress) │
                     └───────────┘  └───────────┘  └────────────┘
```

### 7.2 Infrastructure Configuration

| Resource | Configuration |
|----------|--------------|
| S3 Bucket | Static website hosting, versioning enabled, public access blocked (CloudFront OAI only) |
| CloudFront | HTTPS only, HTTP/2 enabled, custom domain with ACM certificate, cache TTL 1 hour for static assets |
| API Gateway | REST API, regional endpoint, usage plan with throttling, API key not required (session-based auth) |
| Lambda | Node.js 20.x runtime, 256 MB memory, 10-second timeout, provisioned concurrency: 5 |
| Secrets Manager | Automatic rotation every 30 days, resource policy restricting access to Lambda execution role only |
| CloudWatch | Log retention: 30 days, custom metrics namespace: `ChildVoiceTutor` |
| DynamoDB (optional) | On-demand capacity, encryption at rest with AWS-managed key, TTL attribute for auto-expiry |

---

## 8. Latency Optimization Strategy

### 8.1 Latency Budget

| Segment | Target | Technique |
|---------|--------|-----------|
| Mic capture → network | < 50 ms | WebRTC direct streaming, no local buffering |
| Token acquisition | < 200 ms | Lambda provisioned concurrency, Secrets Manager caching |
| STT processing | < 300 ms | OpenAI Realtime API streaming (incremental recognition) |
| LLM generation | < 500 ms | Short structured responses (≤5 sentences), streaming output |
| TTS synthesis | < 200 ms | Streaming synthesis, playback begins before full response is generated |
| Total round-trip | < 1.2 s | End-to-end target for first audio byte of response |

### 8.2 Optimization Techniques

- WebRTC eliminates HTTP overhead for audio transport.
- Edge-hosted token service via CloudFront + Lambda@Edge (optional) reduces geographic latency.
- Short, structured responses (max 5 sentences) reduce LLM generation time.
- Streaming TTS: audio playback begins as soon as the first audio chunk is available, not after full synthesis.
- Frontend audio buffer: 100 ms jitter buffer to smooth playback without perceptible delay.

---

## 9. Failure Recovery Strategy

### 9.1 Fallback Pipeline

When the WebRTC connection to the OpenAI Realtime API drops:

```
┌──────────┐    audio blob     ┌──────────────┐    text     ┌──────────────┐
│ Browser  │ ───────────────► │  STT API     │ ─────────► │  LLM API     │
│ (record) │                   │  (Whisper)   │            │  (GPT-4o)    │
└──────────┘                   └──────────────┘            └──────┬───────┘
                                                                  │ text
                                                           ┌──────▼───────┐
                                                           │  TTS API     │
                                                           │  (OpenAI)    │
                                                           └──────┬───────┘
                                                                  │ audio
                                                           ┌──────▼───────┐
                                                           │  Browser     │
                                                           │  (playback)  │
                                                           └──────────────┘
```

### 9.2 Fallback Behavior

| Condition | Action |
|-----------|--------|
| WebRTC connection lost | Attempt reconnection (3 retries, exponential backoff) |
| Reconnection fails | Switch to fallback pipeline (STT → LLM → TTS over REST) |
| Fallback pipeline fails | Display child-friendly "I need a moment" message with retry button |
| Token expired mid-session | Silently request new token and re-establish connection |
| OpenAI API 5xx errors | Retry with backoff; after 3 failures, activate fallback |

### 9.3 Client-Side Resilience

- Audio input is buffered locally during reconnection attempts (up to 10 seconds).
- Session context (conversation history) is maintained in-memory on the client to resume seamlessly.
- Visual indicator shows connection status without alarming the child (e.g., avatar "thinking" animation).

---

## 10. Monitoring and Observability

### 10.1 Metrics

| Metric | Source | Alarm Threshold |
|--------|--------|----------------|
| Session duration | Lambda + CloudWatch | Alert if avg < 30s (indicates connection issues) |
| Response latency (first audio byte) | Frontend telemetry → CloudWatch | P95 > 2 seconds |
| Interruption frequency | Frontend telemetry → CloudWatch | > 10 per minute (indicates VAD miscalibration) |
| Topic engagement | Transcript analysis (optional) | Informational only |
| Token generation latency | Lambda → CloudWatch | P99 > 500 ms |
| Fallback activation rate | Frontend telemetry → CloudWatch | > 5% of sessions |
| Error rate (4xx/5xx) | API Gateway → CloudWatch | > 1% of requests |
| Lambda cold starts | CloudWatch | > 10% of invocations |

### 10.2 Logging

- Lambda: Structured JSON logs with session ID, request duration, error details.
- Frontend: Client-side telemetry sent via `navigator.sendBeacon` to a CloudWatch ingestion endpoint.
- Log levels: ERROR (always), WARN (always), INFO (production), DEBUG (staging only).

### 10.3 Dashboards

- Operational dashboard: session count, latency percentiles, error rates, fallback activations.
- Usage dashboard: daily active sessions, average session duration, mode distribution.
- Safety dashboard: flagged topic attempts, redirect frequency.

---

## 11. Optional Enhancements

### 11.1 Parent Dashboard

- Web-based dashboard (separate authenticated route) for parents to:
  - View session summaries (anonymized transcripts if opted in).
  - Toggle learning modes.
  - Set session time limits.
  - Enable/disable learning progress tracking.
  - Delete stored data.

### 11.2 Learning Modes

| Mode | Description |
|------|------------|
| Story Mode | Interactive storytelling where the child co-creates narratives |
| Science Mode | Guided exploration of science topics with suggested experiments |
| English Mode | Mandarin-English bilingual practice with pronunciation feedback |
| Quiz Mode | Age-appropriate trivia with positive reinforcement |

### 11.3 Long-Term Memory (Vector Database)

- Store conversation embeddings in a vector database (e.g., Amazon OpenSearch Serverless or Pinecone).
- Enable the tutor to recall previous topics: "Last time we talked about dinosaurs. Want to learn more?"
- Requires parental opt-in; embeddings are anonymized and tied to a hashed device ID.

---

## 12. Minimum Viable Product (MVP) Scope

The MVP includes only the following:

| Component | MVP Scope |
|-----------|----------|
| Frontend | Browser-based SPA with microphone capture, WebRTC streaming, audio playback, basic UI |
| Backend | Lambda session service with ephemeral token generation |
| AI Pipeline | OpenAI Realtime API integration (STT + LLM + TTS) |
| Safety | Child-safe system prompt, topic guardrails, 5-sentence response limit |
| Infrastructure | S3 + CloudFront + API Gateway + Lambda + Secrets Manager |
| Monitoring | Basic CloudWatch logging and latency metrics |

Explicitly excluded from MVP:
- Parent dashboard
- Learning modes (single general mode only)
- Long-term memory / vector database
- Learning progress tracking
- Fallback pipeline (stretch goal)

---

## 13. Non-Functional Requirements

| Requirement | Target |
|-------------|--------|
| Availability | 99.9% uptime (leveraging AWS managed services) |
| Scalability | Support up to 1,000 concurrent sessions (Lambda auto-scaling) |
| Response latency | < 1.2 seconds to first audio byte (P50) |
| Browser support | Chrome 90+, Safari 16+, Edge 90+ (WebRTC required) |
| Accessibility | Large touch targets, high-contrast visuals, screen-reader compatible controls |
| Localization | Primary: Simplified Mandarin Chinese; extensible to other languages |

---

## 14. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| OpenAI Realtime API outage | Medium | High | Fallback pipeline (STT → LLM → TTS) |
| Child encounters unsafe content | Low | Critical | Multi-layer guardrails: system prompt + content filter + topic redirect |
| High latency degrades experience | Medium | High | Latency budget enforcement, provisioned concurrency, streaming responses |
| API key exposure | Low | Critical | Secrets Manager + ephemeral tokens + no client-side key access |
| Cost overrun from high usage | Medium | Medium | API Gateway throttling, session limits, CloudWatch billing alarms |
| WebRTC incompatibility on older browsers | Low | Medium | Browser detection with graceful fallback message |

---

## 15. Future Considerations

- Multi-language support beyond Mandarin Chinese.
- Mobile native apps (iOS/Android) for improved audio handling.
- Adaptive difficulty based on child's demonstrated comprehension level.
- Integration with school curriculum standards.
- Multi-child household support with individual profiles.
- Offline mode with cached conversation starters.
