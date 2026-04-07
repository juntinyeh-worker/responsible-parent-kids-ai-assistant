# 🎓 Why? Why? Why! — 兒童語音 AI 有問必答小精靈

## 中文版

### 起因

工程師爸爸為了用來解釋小孩的十萬個為什麼問題，總不能一直叫他們走開吧？

起初用了不同的 solution 來建立語音對答的助理，後來發現把 System Prompt 單獨拉出來成為一個環境變數的時候，這個語音助理可以很快的被用到不同的場景上面。而且最好的是，不需要重新 deploy。

### 延伸想法

從這個語音助理的想法，延伸到一個負責的家長的觀念。與其擔心或限制下一代孩子在資訊接收的防護，不如主動的提供一個可控的、乾淨的、無毒的內容。畢竟我們都聽過太多案例是在語音的頻道內容裡，有過多有毒且有害的內容正在侵蝕兒童的數位學習。

---

### 系統架構

本專案使用 Amazon Bedrock Nova Sonic 模型，透過 WebSocket 實現瀏覽器端的即時雙向語音串流。

```
┌─────────────────────────────────────────────────────────────────┐
│                     使用者（兒童）瀏覽器                           │
│          麥克風擷取 (16kHz PCM) ↔ 喇叭播放 (24kHz PCM)           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket (JSON + Base64 音訊)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   CloudFront (CDN)                               │
│         靜態前端 (S3) + API 路由 (/api/*) → ALB                  │
│         Cognito 認證閘道 (CloudFront Function)                   │
└──────┬──────────────────────────────┬───────────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────┐          ┌───────────────────────────────────────┐
│  S3 Bucket   │          │  ALB (Application Load Balancer)      │
│  靜態網頁     │          │  WebSocket 升級支援                    │
│  + 對話日誌   │          │  X-CF-Origin-Verify 標頭驗證           │
└──────────────┘          └──────────────────┬────────────────────┘
                                             │
                                             ▼
                          ┌───────────────────────────────────────┐
                          │  ECS Fargate (FastAPI + Uvicorn)      │
                          │  • WebSocket 音訊代理                  │
                          │  • 對話日誌記錄                        │
                          │  • 健康檢查端點                        │
                          └──────────────────┬────────────────────┘
                                             │ Bedrock 雙向串流 API
                                             ▼
                          ┌───────────────────────────────────────┐
                          │  Amazon Bedrock — Nova Sonic           │
                          │  STT → LLM → TTS（統一串流管線）        │
                          │  模型: amazon.nova-2-sonic-v1:0        │
                          └───────────────────────────────────────┘
```

#### 涉及的 AWS 服務

| 服務 | 用途 |
|------|------|
| Amazon Bedrock (Nova Sonic) | 語音轉語音模型（STT + LLM + TTS 一體化） |
| ECS Fargate | 容器化後端運行環境 |
| ALB | 負載均衡，支援 WebSocket 升級 |
| CloudFront | CDN 分發靜態資源 + API 路由 |
| S3 | 靜態網頁託管 + 對話日誌儲存 |
| Cognito | 使用者認證（Email + PIN 碼登入） |
| CloudWatch | 日誌收集（30 天保留）與監控 |
| SSM Parameter Store | 執行時期設定覆寫（如 System Prompt） |
| IAM | 角色權限控制（Bedrock / SSM / S3） |

---

### 資料流程

1. 兒童開啟瀏覽器，CloudFront 提供靜態頁面（S3 來源）
2. CloudFront Function 檢查 `cvt-authed` Cookie，未認證則導向 `/login.html`
3. 使用者透過 Cognito 以 Email + PIN 碼登入，取得 IdToken 存入 Cookie
4. 前端建立 WebSocket 連線至 `/api/ws/audio`（經 CloudFront → ALB → ECS）
5. 瀏覽器透過 `getUserMedia` 擷取麥克風音訊，重新取樣至 16kHz 16-bit PCM
6. 音訊以 Base64 編碼透過 WebSocket 傳送至後端
7. 後端（FastAPI）將音訊轉發至 Bedrock Nova Sonic 雙向串流 API
8. Nova Sonic 執行 STT → LLM 推理 → TTS，串流回應音訊與文字
9. 後端將回應透過 WebSocket 轉發回瀏覽器
10. 瀏覽器即時播放 24kHz PCM 音訊回應，並顯示文字轉錄

#### WebSocket 訊息協定

**客戶端 → 伺服器：**
```json
{"type": "audio", "data": "<base64 PCM 16kHz 音訊>"}
{"type": "stop"}
```

**伺服器 → 客戶端：**
```json
{"type": "audio", "data": "<base64 PCM 24kHz 音訊>"}
{"type": "text", "role": "USER|ASSISTANT", "content": "轉錄/生成文字"}
{"type": "event", "type": "contentStart|contentEnd", "role": "...", "speculative": true|false}
```

#### 前端狀態機

```
IDLE → CONNECTING → LISTENING → SPEAKING → PROCESSING → RESPONDING → LISTENING
  ↑                                                                      │
  └──────────────────── 使用者點擊結束 ←─────────────────────────────────┘
                        任何狀態 → ERROR（連線中斷時）
```

---

### 協定差異比較：WebSocket vs WebRTC

本專案的 System Design Specification 最初設計基於 OpenAI Realtime API（使用 WebRTC），後來實作改為 Amazon Bedrock Nova Sonic（使用 WebSocket）。兩者的關鍵差異如下：

| 比較項目 | OpenAI Realtime API (WebRTC) | Amazon Bedrock Nova Sonic (WebSocket) |
|---------|------------------------------|---------------------------------------|
| 傳輸協定 | WebRTC (DTLS-SRTP 加密) | WebSocket over HTTPS/WSS |
| 連線方式 | 瀏覽器直連 OpenAI（P2P 風格） | 瀏覽器 → 後端伺服器 → Bedrock（代理模式） |
| 音訊格式 | 16-bit PCM, 24kHz | 輸入 16kHz / 輸出 24kHz, 16-bit PCM, Base64 編碼 |
| VAD（語音活動偵測） | OpenAI 伺服器端 VAD | Nova Sonic 內建 VAD |
| 認證方式 | Ephemeral Token（短期令牌，60 秒） | AWS IAM Task Role / 環境變數憑證 |
| 中間層需求 | Lambda 僅負責產生 Token，音訊不經過後端 | FastAPI 後端作為全程音訊代理 |
| 延遲特性 | 較低（P2P 直連，無中間跳轉） | 略高（多一層後端代理） |
| 安全模型 | API Key 存於 Secrets Manager，前端僅持有 Ephemeral Token | API 憑證完全在後端，前端零接觸 |
| 中斷處理 | WebRTC ICE 自動重連 | WebSocket 需手動重連邏輯 |
| 瀏覽器相容性 | 需要 WebRTC 支援（Chrome/Safari/Edge） | 僅需 WebSocket 支援（幾乎所有現代瀏覽器） |

#### 架構差異圖示

**OpenAI Realtime API（原始設計）：**
```
瀏覽器 ──WebRTC──→ OpenAI Realtime API
  ↑                      ↑
  │ Ephemeral Token       │ API Key
  │                       │
  └── Lambda ──→ Secrets Manager
```

**Amazon Bedrock Nova Sonic（目前實作）：**
```
瀏覽器 ──WebSocket──→ FastAPI (ECS) ──Bedrock API──→ Nova Sonic
                         ↑
                    IAM Task Role
                    SSM Parameter Store
```

---

### 成本估算比較

以下為每月 1,000 個活躍 session、每個 session 平均 10 分鐘的粗估成本比較（美元，僅供參考，實際費用依用量與區域而異）：

| 成本項目 | OpenAI Realtime API 方案 | Amazon Bedrock Nova Sonic 方案 |
|---------|--------------------------|-------------------------------|
| AI 模型費用 | OpenAI Realtime API 按音訊時長計費，約 $0.06/min（輸入）+ $0.24/min（輸出）。10,000 分鐘 ≈ **$3,000/月** | Nova Sonic 按輸入/輸出 token 計費。語音對話成本顯著較低，預估 **$100–$300/月** |
| 運算資源 | Lambda（僅 Token 產生），極低成本 ≈ **$1–$5/月** | ECS Fargate（全程音訊代理），0.5 vCPU + 1GB ≈ **$15–$30/月** |
| 負載均衡 | 不需要（WebRTC 直連） | ALB ≈ **$20–$30/月**（含 LCU） |
| CDN | CloudFront 靜態資源 ≈ **$1–$5/月** | CloudFront 靜態 + API 路由 ≈ **$5–$15/月** |
| 認證 | Secrets Manager ≈ **$1/月** | Cognito（50,000 MAU 免費）≈ **$0/月** |
| 日誌/監控 | CloudWatch ≈ **$5–$10/月** | CloudWatch + S3 日誌 ≈ **$5–$15/月** |
| **月估總計** | **≈ $3,000–$3,100/月** | **≈ $150–$400/月** |

> ⚠️ 以上為粗估值。OpenAI Realtime API 的主要成本在於音訊處理費用較高；Nova Sonic 方案雖然基礎設施成本略高（需要 ECS 代理層），但模型費用顯著較低，整體成本優勢明顯。實際費用請參考 [Amazon Bedrock 定價](https://aws.amazon.com/bedrock/pricing/) 與 [OpenAI 定價](https://openai.com/api/pricing/)。

---

### 專案結構

```
chat_assistant_aws/
├── main.py                 # FastAPI 應用程式入口（WebSocket + REST）
├── session.py              # Nova Sonic 雙向串流 session 管理
├── prompts.py              # 兒童安全 System Prompt（支援 SSM 覆寫）
├── config.py               # 環境設定（local / aws）
├── ssm_config.py           # SSM Parameter Store 快取載入器
├── rate_limit.py           # 滑動視窗速率限制器（Per-IP）
├── logging_service.py      # 對話日誌寫入（本地 JSONL / S3）
├── log_shipper.py          # JSONL 日誌搬運至 S3
├── requirements.txt        # Python 依賴
├── Dockerfile              # 容器映像定義
├── .env.example            # 環境變數範本
├── index-local.html        # 本地測試用簡易前端
├── static/                 # 正式前端
│   ├── index.html          # 主頁面（語音對話 UI）
│   ├── login.html          # Cognito PIN 碼登入頁
│   ├── app.js              # 前端核心邏輯（狀態機 + 音訊串流）
│   └── favicon.svg         # 網站圖示
├── infra/                  # 基礎設施
│   ├── ecs-stack.yaml      # CloudFormation（ECS + ALB + CloudFront + Cognito）
│   ├── deploy.sh           # 後端部署腳本（Docker → ECR → ECS）
│   └── deploy-frontend.sh  # 前端部署腳本（S3 + CloudFront + Cognito 注入）
├── tests/                  # 測試
│   └── backend/            # 後端單元測試
└── System_Design_Specification.md  # 系統設計規格書
```

---

### 快速開始

#### 本地開發

```bash
cd chat_assistant_aws

# 1. 複製環境變數範本
cp .env.example .env

# 2. 編輯 .env，填入 AWS 憑證
#    DEPLOY_ENV=local
#    AWS_ACCESS_KEY_ID=your-key
#    AWS_SECRET_ACCESS_KEY=your-secret
#    BEDROCK_REGION=us-east-1

# 3. 安裝依賴
pip install -r requirements.txt

# 4. 啟動伺服器
uvicorn main:app --host 0.0.0.0 --port 8000

# 5. 開啟瀏覽器 http://localhost:8000
#    或使用 index-local.html 進行簡易測試
```

#### AWS 部署

```bash
# 1. 部署後端（ECS + ALB）
./infra/deploy.sh --region us-east-1

# 2. 部署前端（CloudFront + S3 + Cognito）
./infra/deploy-frontend.sh --ecs-stack child-voice-tutor --admin-email user@example.com

# 3. 透過 SSM 動態更新 System Prompt（無需重新部署）
aws ssm put-parameter \
  --name "/child-voice-tutor/system-prompt" \
  --value "你是一個友善的科學老師..." \
  --type String --overwrite
```

---

### 安全設計

- **傳輸加密**：CloudFront 強制 HTTPS/WSS，ALB 與 ECS 間透過 VPC 內部通訊
- **認證閘道**：CloudFront Function 檢查 Cookie，未認證導向 Cognito 登入
- **ALB 鎖定**：僅接受帶有 `X-CF-Origin-Verify` 標頭的請求（防止繞過 CloudFront 直接存取）
- **憑證隔離**：Bedrock API 憑證透過 ECS Task Role 取得，前端完全無法接觸
- **兒童安全**：System Prompt 內建內容過濾，禁止暴力、政治、宗教等不適當話題
- **速率限制**：Per-IP 滑動視窗限制（預設 5 次/分鐘）
- **音訊隱私**：音訊串流為暫態傳輸，伺服器端不錄音、不儲存原始音訊

---
---

## English Version

### Origin Story

An engineer dad needed a way to answer his kids' endless "why?" questions — you can't just keep telling them to go away, right?

Initially, different solutions were tried to build a voice Q&A assistant. The breakthrough came when the System Prompt was extracted as an environment variable — suddenly the voice assistant could be repurposed for entirely different scenarios without redeployment.

### The Bigger Picture

This evolved into a responsible parenting concept. Rather than worrying about or restricting children's information intake, why not proactively provide a controlled, clean, and safe content channel? We've all heard too many cases of toxic and harmful content in voice channels eroding children's digital learning.

---

### System Architecture

This project uses Amazon Bedrock Nova Sonic for real-time bidirectional voice streaming via WebSocket.

```
┌─────────────────────────────────────────────────────────────────┐
│                   User (Child) Browser                           │
│         Mic Capture (16kHz PCM) ↔ Speaker Playback (24kHz PCM)  │
└──────────────────────────┬──────────────────────────────────────┘
                           │ WebSocket (JSON + Base64 Audio)
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                   CloudFront (CDN)                               │
│       Static Frontend (S3) + API Routing (/api/*) → ALB         │
│       Cognito Auth Gate (CloudFront Function)                    │
└──────┬──────────────────────────────┬───────────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────┐          ┌───────────────────────────────────────┐
│  S3 Bucket   │          │  ALB (Application Load Balancer)      │
│  Static Site │          │  WebSocket Upgrade Support             │
│  + Conv Logs │          │  X-CF-Origin-Verify Header Check      │
└──────────────┘          └──────────────────┬────────────────────┘
                                             │
                                             ▼
                          ┌───────────────────────────────────────┐
                          │  ECS Fargate (FastAPI + Uvicorn)      │
                          │  • WebSocket Audio Proxy              │
                          │  • Conversation Logging               │
                          │  • Health Check Endpoint              │
                          └──────────────────┬────────────────────┘
                                             │ Bedrock Bidirectional Stream
                                             ▼
                          ┌───────────────────────────────────────┐
                          │  Amazon Bedrock — Nova Sonic           │
                          │  STT → LLM → TTS (Unified Pipeline)   │
                          │  Model: amazon.nova-2-sonic-v1:0       │
                          └───────────────────────────────────────┘
```

#### AWS Services Used

| Service | Purpose |
|---------|---------|
| Amazon Bedrock (Nova Sonic) | Speech-to-speech model (STT + LLM + TTS unified) |
| ECS Fargate | Containerized backend runtime |
| ALB | Load balancing with WebSocket upgrade support |
| CloudFront | CDN for static assets + API routing |
| S3 | Static website hosting + conversation log storage |
| Cognito | User authentication (Email + PIN login) |
| CloudWatch | Log collection (30-day retention) and monitoring |
| SSM Parameter Store | Runtime config overrides (e.g., System Prompt) |
| IAM | Role-based access control (Bedrock / SSM / S3) |

---

### Data Flow

1. Child opens browser; CloudFront serves static pages from S3
2. CloudFront Function checks `cvt-authed` cookie; redirects to `/login.html` if unauthenticated
3. User logs in via Cognito with Email + PIN; IdToken stored in cookie
4. Frontend establishes WebSocket to `/api/ws/audio` (via CloudFront → ALB → ECS)
5. Browser captures mic audio via `getUserMedia`, resamples to 16kHz 16-bit PCM
6. Audio is Base64-encoded and sent over WebSocket to the backend
7. Backend (FastAPI) forwards audio to Bedrock Nova Sonic bidirectional streaming API
8. Nova Sonic performs STT → LLM inference → TTS, streaming back audio and text
9. Backend relays responses back to browser via WebSocket
10. Browser plays 24kHz PCM audio in real-time and displays text transcription

#### WebSocket Message Protocol

**Client → Server:**
```json
{"type": "audio", "data": "<base64 PCM 16kHz audio>"}
{"type": "stop"}
```

**Server → Client:**
```json
{"type": "audio", "data": "<base64 PCM 24kHz audio>"}
{"type": "text", "role": "USER|ASSISTANT", "content": "transcribed/generated text"}
{"type": "event", "type": "contentStart|contentEnd", "role": "...", "speculative": true|false}
```

#### Frontend State Machine

```
IDLE → CONNECTING → LISTENING → SPEAKING → PROCESSING → RESPONDING → LISTENING
  ↑                                                                      │
  └──────────────────── User clicks End ←────────────────────────────────┘
                        Any state → ERROR (on connection loss)
```

---

### Protocol Comparison: WebSocket vs WebRTC

The System Design Specification was originally designed around the OpenAI Realtime API (WebRTC). The current implementation uses Amazon Bedrock Nova Sonic (WebSocket). Key differences:

| Aspect | OpenAI Realtime API (WebRTC) | Amazon Bedrock Nova Sonic (WebSocket) |
|--------|------------------------------|---------------------------------------|
| Transport | WebRTC (DTLS-SRTP encrypted) | WebSocket over HTTPS/WSS |
| Connection Model | Browser connects directly to OpenAI (P2P-style) | Browser → Backend Server → Bedrock (proxy model) |
| Audio Format | 16-bit PCM, 24kHz | Input 16kHz / Output 24kHz, 16-bit PCM, Base64 encoded |
| VAD | OpenAI server-side VAD | Nova Sonic built-in VAD |
| Authentication | Ephemeral Token (60s TTL) | AWS IAM Task Role / Environment credentials |
| Backend Role | Lambda only generates tokens; audio bypasses backend | FastAPI backend acts as full audio proxy |
| Latency | Lower (P2P direct, no intermediate hops) | Slightly higher (extra backend proxy hop) |
| Security Model | API Key in Secrets Manager; frontend holds only ephemeral token | API credentials entirely server-side; frontend has zero access |
| Reconnection | WebRTC ICE auto-reconnection | Manual WebSocket reconnection logic required |
| Browser Compatibility | Requires WebRTC (Chrome/Safari/Edge) | Only needs WebSocket (virtually all modern browsers) |

#### Architecture Comparison

**OpenAI Realtime API (Original Design):**
```
Browser ──WebRTC──→ OpenAI Realtime API
  ↑                      ↑
  │ Ephemeral Token       │ API Key
  │                       │
  └── Lambda ──→ Secrets Manager
```

**Amazon Bedrock Nova Sonic (Current Implementation):**
```
Browser ──WebSocket──→ FastAPI (ECS) ──Bedrock API──→ Nova Sonic
                         ↑
                    IAM Task Role
                    SSM Parameter Store
```

---

### Cost Estimate Comparison

Rough monthly estimate for 1,000 active sessions, averaging 10 minutes each (USD, for reference only — actual costs vary by usage and region):

| Cost Item | OpenAI Realtime API | Amazon Bedrock Nova Sonic |
|-----------|---------------------|--------------------------|
| AI Model | ~$0.06/min (input) + $0.24/min (output). 10,000 min ≈ **$3,000/mo** | Nova Sonic per-token pricing. Voice conversation costs significantly lower, est. **$100–$300/mo** |
| Compute | Lambda (token generation only), minimal ≈ **$1–$5/mo** | ECS Fargate (full audio proxy), 0.5 vCPU + 1GB ≈ **$15–$30/mo** |
| Load Balancer | Not needed (WebRTC direct) | ALB ≈ **$20–$30/mo** (incl. LCU) |
| CDN | CloudFront static assets ≈ **$1–$5/mo** | CloudFront static + API routing ≈ **$5–$15/mo** |
| Auth | Secrets Manager ≈ **$1/mo** | Cognito (50K MAU free tier) ≈ **$0/mo** |
| Logging/Monitoring | CloudWatch ≈ **$5–$10/mo** | CloudWatch + S3 logs ≈ **$5–$15/mo** |
| **Monthly Total** | **≈ $3,000–$3,100/mo** | **≈ $150–$400/mo** |

> ⚠️ These are rough estimates. The primary cost driver for OpenAI Realtime API is its higher audio processing fees. The Nova Sonic approach has slightly higher infrastructure costs (ECS proxy layer) but significantly lower model costs, resulting in a clear overall cost advantage. See [Amazon Bedrock Pricing](https://aws.amazon.com/bedrock/pricing/) and [OpenAI Pricing](https://openai.com/api/pricing/) for current rates.

---

### Project Structure

```
chat_assistant_aws/
├── main.py                 # FastAPI entry point (WebSocket + REST)
├── session.py              # Nova Sonic bidirectional streaming session
├── prompts.py              # Child-safe system prompt (SSM override support)
├── config.py               # Environment config (local / aws)
├── ssm_config.py           # SSM Parameter Store cached loader
├── rate_limit.py           # Sliding window rate limiter (per-IP)
├── logging_service.py      # Conversation log writer (local JSONL / S3)
├── log_shipper.py          # Ship JSONL logs to S3
├── requirements.txt        # Python dependencies
├── Dockerfile              # Container image definition
├── .env.example            # Environment variable template
├── index-local.html        # Simple local test frontend
├── static/                 # Production frontend
│   ├── index.html          # Main page (voice chat UI)
│   ├── login.html          # Cognito PIN login page
│   ├── app.js              # Frontend core (state machine + audio streaming)
│   └── favicon.svg         # Site icon
├── infra/                  # Infrastructure
│   ├── ecs-stack.yaml      # CloudFormation (ECS + ALB + CloudFront + Cognito)
│   ├── deploy.sh           # Backend deploy (Docker → ECR → ECS)
│   └── deploy-frontend.sh  # Frontend deploy (S3 + CloudFront + Cognito injection)
├── tests/                  # Tests
│   └── backend/            # Backend unit tests
└── System_Design_Specification.md  # System design specification
```

---

### Quick Start

#### Local Development

```bash
cd chat_assistant_aws

# 1. Copy environment template
cp .env.example .env

# 2. Edit .env with your AWS credentials
#    DEPLOY_ENV=local
#    AWS_ACCESS_KEY_ID=your-key
#    AWS_SECRET_ACCESS_KEY=your-secret
#    BEDROCK_REGION=us-east-1

# 3. Install dependencies
pip install -r requirements.txt

# 4. Start the server
uvicorn main:app --host 0.0.0.0 --port 8000

# 5. Open browser at http://localhost:8000
#    Or use index-local.html for simple testing
```

#### AWS Deployment

```bash
# 1. Deploy backend (ECS + ALB)
./infra/deploy.sh --region us-east-1

# 2. Deploy frontend (CloudFront + S3 + Cognito)
./infra/deploy-frontend.sh --ecs-stack child-voice-tutor --admin-email user@example.com

# 3. Update System Prompt dynamically via SSM (no redeployment needed)
aws ssm put-parameter \
  --name "/child-voice-tutor/system-prompt" \
  --value "You are a friendly science teacher..." \
  --type String --overwrite
```

---

### Security Design

- **Transport Encryption**: CloudFront enforces HTTPS/WSS; ALB-to-ECS communication within VPC
- **Auth Gate**: CloudFront Function checks cookies; unauthenticated users redirected to Cognito login
- **ALB Lockdown**: Only accepts requests with `X-CF-Origin-Verify` header (prevents bypassing CloudFront)
- **Credential Isolation**: Bedrock API credentials obtained via ECS Task Role; frontend has zero access
- **Child Safety**: System Prompt includes content filtering; blocks violence, politics, religion, and inappropriate topics
- **Rate Limiting**: Per-IP sliding window (default 5 requests/minute)
- **Audio Privacy**: Audio streams are transient; no server-side recording or raw audio storage

---

### License

See [LICENSE](LICENSE) for details.
