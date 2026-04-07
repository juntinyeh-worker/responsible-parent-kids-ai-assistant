# TODO

## 中文版

### 現況

核心功能已完成且穩定運行，包含即時語音對話、Cognito 認證、對話日誌記錄等基礎架構。

### 下一階段：政府應用場景擴展

#### 一、S3 對話日誌瀏覽介面

- 建立一個輕量級的日誌查詢介面，可直接瀏覽存放於 S3 的對話紀錄
- 支援依時間、使用者、Session 等條件篩選
- 提供對話內容預覽與匯出功能
- 適用於政府稽核與監管需求

#### 二、模型語音回應錄製與重播

- 擴展中間層（FastAPI），攔截並儲存模型回傳的語音回應（PCM 音訊）
- 將語音回應與對應的文字轉錄一併存檔至 S3
- 提供重播機制，可回放模型的語音回應
- 適用於內容審查、品質監控、以及教育成效追蹤

---

## English Version

### Current Status

Core features are complete and stable, including real-time voice conversation, Cognito authentication, and conversation logging infrastructure.

### Next Phase: Government Use Case Extensions

#### 1. S3 Conversation Log Browsing Interface

- Build a lightweight log browsing UI to query conversation records stored in S3
- Support filtering by time, user, session, etc.
- Provide conversation preview and export capabilities
- Designed for government audit and regulatory compliance

#### 2. Model Voice Response Recording & Replay

- Extend the middle layer (FastAPI) to intercept and persist model voice responses (PCM audio)
- Store voice responses alongside their text transcriptions in S3
- Provide a replay mechanism to play back model voice responses
- Designed for content review, quality monitoring, and educational effectiveness tracking
