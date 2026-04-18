# FinAgent Chat Interface — Technical Design Specification

> **Module:** `chat-ui` | **Stack:** Pure Go + Vanilla JS + SSE | **Version:** v1.0  
> **Constraint:** All processing local on Asus TUF A15. Zero raw financial data leaves the home network.

***

## 1. Executive Summary

This document specifies the design and implementation of the **chat interface module** for FinAgent — a privacy-first, self-hosted personal AI financial assistant. The chat interface is architected as a **cleanly decoupled, replaceable module** served directly by the existing Go backend, requiring no additional processes, frameworks, or Docker containers.

The interface supports three input modalities: **text messages**, **image uploads** (for receipt/passbook photos), and **PDF uploads** (for bank statements). All file processing — OCR, text extraction, and LLM inference — happens locally on the Asus TUF A15.

***

## 2. Architecture Philosophy: Modular Separation

The single most important design decision is treating the chat UI as a **swappable adapter**, not a hardcoded component. This means:

- The Go backend exposes a **stable, versioned HTTP API** (`/api/v1/chat`, `/api/v1/upload`)
- The frontend (`chat.html`) consumes that API via `fetch()` and WebSocket/SSE
- Replacing the frontend in the future (e.g., swapping to Open WebUI, a mobile app, or Telegram) requires zero changes to the backend

This follows the **Ports and Adapters (Hexagonal Architecture)** pattern — the chat UI is just one adapter to the core domain.

```
┌─────────────────────────────────────────────────────────┐
│                    CORE DOMAIN (Go)                      │
│  ┌────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │  Financial │  │  LLM Bridge  │  │   PostgreSQL     │  │
│  │   Engine   │  │  (Ollama)    │  │   Ledger         │  │
│  └────────────┘  └──────────────┘  └─────────────────┘  │
│                        │                                  │
│              ┌─────────▼──────────┐                      │
│              │  Stable HTTP API   │                      │
│              │  /api/v1/chat      │                      │
│              │  /api/v1/upload    │                      │
│              │  /api/v1/history   │                      │
│              └─────────┬──────────┘                      │
└────────────────────────┼────────────────────────────────┘
                         │  (Replaceable Adapter Layer)
          ┌──────────────┼───────────────┐
          │              │               │
   ┌──────▼──────┐ ┌─────▼──────┐ ┌────▼────────┐
   │  chat.html  │ │  Telegram  │ │  Mobile App │
   │ (Option A)  │ │    Bot     │ │  (Future)   │
   └─────────────┘ └────────────┘ └─────────────┘
```

***

## 3. Module Boundary Contract

The `chat-ui` module communicates with the Go backend through a well-defined contract. Any future replacement UI must implement the same contract.

### 3.1 API Endpoints

| Endpoint | Method | Purpose | Request | Response |
|----------|--------|---------|---------|----------|
| `/api/v1/chat` | `POST` | Send a text message | `{"message": "string", "session_id": "string"}` | SSE stream of tokens |
| `/api/v1/upload` | `POST` | Upload image or PDF | `multipart/form-data` with `file` + optional `password` | SSE stream of processing status |
| `/api/v1/history` | `GET` | Fetch past messages | `?session_id=&limit=50&before=timestamp` | `{"messages": [...]}` |
| `/api/v1/health` | `GET` | UI readiness check | — | `{"status": "ok", "ollama": "ready"}` |

### 3.2 Message Schema (Chat History in PostgreSQL)

```sql
CREATE TABLE chat_messages (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content     TEXT NOT NULL,        -- plain text for text messages
    media_type  TEXT,                 -- 'image', 'pdf', or NULL for text
    media_ref   TEXT,                 -- relative path to processed artifact (optional)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_chat_messages_session ON chat_messages(session_id, created_at DESC);
```

### 3.3 SSE Stream Format

All responses stream via **Server-Sent Events**. This gives token-by-token streaming from Ollama without needing WebSockets:

```
event: token
data: {"token": "Your"}

event: token
data: {"token": " balance"}

event: done
data: {"full_response": "Your balance is ₹12,400.", "transactions_logged": 3}

event: error
data: {"code": "SANITIZER_BLOCKED", "message": "Message contained sensitive pattern"}
```

***

## 4. Frontend Module: `chat.html`

The entire frontend lives in a single `chat.html` file served at `GET /`. It has **no build step, no npm, no bundler** — just HTML, CSS, and vanilla JavaScript.

### 4.1 File Structure

```
finagent/
├── main.go                  ← Go entrypoint
├── api/
│   ├── chat.go              ← /api/v1/chat handler + SSE
│   ├── upload.go            ← /api/v1/upload handler
│   └── history.go           ← /api/v1/history handler
├── domain/
│   ├── sanitizer.go         ← Security filter (runs before LLM)
│   ├── extractor.go         ← PDF/image text extraction
│   └── llm.go               ← Ollama bridge
├── static/
│   └── chat.html            ← The entire chat UI (this module)
└── db/
    └── schema.sql
```

### 4.2 UI Layout

```
┌────────────────────────────────────────────────────────┐
│  🏦 FinAgent                              [●] Connected │  ← Header
├──────────────────────────────┬─────────────────────────┤
│                              │  BALANCE SNAPSHOT        │
│   assistant  │               │  ┌─────────────────────┐ │
│  ┌─────────────────────────┐ │  │ Net Balance ₹12,400  │ │
│  │ Your April food spend   │ │  │ This month spend ... │ │
│  │ is ₹4,200 — 12% over   │ │  │ Safe to spend  ...   │ │
│  │ your ₹3,800 goal.       │ │  └─────────────────────┘ │
│  └─────────────────────────┘ │                           │
│                   │  you  │  │  ACTIVE GOALS             │
│  ┌─────────────────────────┐ │  ┌─────────────────────┐ │
│  │ Can I buy AirPods for   │ │  │ 🏖 Goa Trip  ₹8,000 │ │  ← Sidebar
│  │ ₹12,000?                │ │  │ ████░░░░░  40%       │ │    (live data
│  └─────────────────────────┘ │  └─────────────────────┘ │     from DB)
│                              │                           │
│  ┌─────────────────────────┐ │                           │
│  │ Not right now. After    │ │                           │
│  │ fencing your Goa trip   │ │                           │
│  │ funds, your disposable  │ │                           │
│  │ balance is ₹3,200.      │ │                           │
│  └─────────────────────────┘ │                           │
│                              │                           │
├──────────────────────────────┴─────────────────────────┤
│  📎  [  Type a message or drop a file...          ] [▶] │  ← Input bar
└────────────────────────────────────────────────────────┘
```

### 4.3 Key Frontend Behaviours

**Text Messaging (SSE Streaming)**

```javascript
async function sendMessage(text) {
    const res = await fetch('/api/v1/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, session_id: SESSION_ID })
    });

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let bubble = createAssistantBubble(); // creates a <div> in the chat

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value).split('\n');
        for (const line of lines) {
            if (line.startsWith('data:')) {
                const { token } = JSON.parse(line.slice(5));
                bubble.textContent += token; // tokens appear word-by-word
                scrollToBottom();
            }
        }
    }
}
```

**File Upload with Progress**

```javascript
async function uploadFile(file, password = '') {
    const form = new FormData();
    form.append('file', file);
    if (password) form.append('password', password);

    const res = await fetch('/api/v1/upload', { method: 'POST', body: form });
    // Process SSE stream for status updates:
    // "Extracting text from PDF..." → "Sanitizing..." → "Logging 47 transactions..."
}
```

**Drag-and-Drop Support**

The chat input area accepts drag-and-drop of files. On drop, the file type is detected:
- `.pdf` → triggers PDF upload flow (prompts for password if needed)
- `.jpg`, `.png`, `.webp` → triggers image/OCR upload flow
- Any other type → shows an inline error: "Only PDF and image files are supported."

***

## 5. Go Backend Handlers

### 5.1 Chat Handler (`api/chat.go`)

```go
func ChatHandler(w http.ResponseWriter, r *http.Request) {
    var req ChatRequest
    json.NewDecoder(r.Body).Decode(&req)

    // 1. Run security sanitizer — ALWAYS first
    if domain.Sanitizer.ShouldBlock(req.Message) {
        writeSSEError(w, "SANITIZER_BLOCKED", "Message contained a sensitive pattern")
        return
    }

    // 2. Fetch context from PostgreSQL (balance, goals, recent transactions)
    ctx := domain.BuildContext(req.SessionID)

    // 3. Set SSE headers
    w.Header().Set("Content-Type", "text/event-stream")
    w.Header().Set("Cache-Control", "no-cache")
    w.Header().Set("Connection", "keep-alive")

    // 4. Stream Ollama response token by token
    domain.LLM.StreamChat(ctx, req.Message, func(token string) {
        fmt.Fprintf(w, "event: token\ndata: {\"token\": %q}\n\n", token)
        w.(http.Flusher).Flush()
    })

    // 5. Persist both user message and assistant response to PostgreSQL
    domain.SaveMessage(req.SessionID, "user", req.Message)
    domain.SaveMessage(req.SessionID, "assistant", ctx.LastResponse)
}
```

### 5.2 Upload Handler (`api/upload.go`)

```go
func UploadHandler(w http.ResponseWriter, r *http.Request) {
    r.ParseMultipartForm(32 << 20) // 32MB max
    file, header, _ := r.FormFile("upload")
    password := r.FormValue("password")
    defer file.Close()

    // Set SSE headers for progress streaming
    w.Header().Set("Content-Type", "text/event-stream")

    // Route by file type
    ext := strings.ToLower(filepath.Ext(header.Filename))
    var rawText string

    switch ext {
    case ".pdf":
        sendSSEStatus(w, "Extracting text from PDF...")
        rawText = domain.Extractor.ExtractPDF(file, password)
    case ".jpg", ".jpeg", ".png", ".webp":
        sendSSEStatus(w, "Running OCR on image...")
        rawText = domain.Extractor.ExtractImage(file)
    default:
        writeSSEError(w, "UNSUPPORTED_TYPE", "Only PDF and image files are accepted")
        return
    }

    // Security sanitizer on extracted text
    sendSSEStatus(w, "Sanitizing extracted content...")
    cleanText := domain.Sanitizer.SanitizeExtractedText(rawText)

    // LLM structured extraction
    sendSSEStatus(w, "Parsing transactions with AI...")
    transactions := domain.LLM.ExtractTransactions(cleanText)

    // Batch insert into PostgreSQL
    sendSSEStatus(w, fmt.Sprintf("Logging %d transactions...", len(transactions)))
    domain.DB.BatchInsert(transactions)

    fmt.Fprintf(w, "event: done\ndata: {\"transactions_logged\": %d}\n\n", len(transactions))
}
```

***

## 6. Security Module (`domain/sanitizer.go`)

The sanitizer runs on **every single input** — both text messages and extracted file text — before the LLM ever sees it. It is the most critical security component.

### 6.1 Text Message Sanitizer

```go
var blockPatterns = []*regexp.Regexp{
    regexp.MustCompile(`(?i)\botp\b`),
    regexp.MustCompile(`(?i)\bpassword\b`),
    regexp.MustCompile(`(?i)\bpin\b`),
    regexp.MustCompile(`(?i)\bcvv\b`),
    regexp.MustCompile(`\b\d{6}\s*(is your|for your)\b`), // "123456 is your OTP"
    regexp.MustCompile(`(?i)verify.*code`),
}

func (s *Sanitizer) ShouldBlock(msg string) bool {
    for _, p := range blockPatterns {
        if p.MatchString(msg) {
            return true
        }
    }
    return false
}
```

### 6.2 Extracted Text Sanitizer (for PDF/Image)

For bank statement text, blocking isn't enough — we need to **mask** sensitive fields while preserving useful transaction data:

```go
var maskPatterns = []struct {
    pattern     *regexp.Regexp
    replacement string
}{
    // Mask full account numbers — keep only last 4 digits
    {regexp.MustCompile(`\b(\d{4})\s*(\d{4})\s*(\d{4})\s*(\d{4})\b`), "XXXX-XXXX-XXXX-$4"},
    // Mask IFSC codes
    {regexp.MustCompile(`\b[A-Z]{4}0[A-Z0-9]{6}\b`), "[IFSC-REDACTED]"},
    // Mask mobile numbers
    {regexp.MustCompile(`\b[6-9]\d{9}\b`), "[MOBILE-REDACTED]"},
    // Drop OTP lines entirely
    {regexp.MustCompile(`(?i).*otp.*\n?`), ""},
}

func (s *Sanitizer) SanitizeExtractedText(text string) string {
    for _, m := range maskPatterns {
        text = m.pattern.ReplaceAllString(text, m.replacement)
    }
    return text
}
```

### 6.3 Session Isolation

Only your own Telegram user ID and local browser session are accepted. All other callers are silently dropped:

```go
var ALLOWED_SESSION_PREFIX = os.Getenv("FINAGENT_SESSION_SECRET")

func AuthMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("X-Session-Token")
        if !strings.HasPrefix(token, ALLOWED_SESSION_SECRET) {
            http.Error(w, "Forbidden", 403)
            return
        }
        next.ServeHTTP(w, r)
    })
}
```

***

## 7. File Processing Module (`domain/extractor.go`)

### 7.1 Digital PDF Extraction

Uses `ledongthuc/pdftext` — a pure-Go library, zero C dependencies:

```go
import pdf "github.com/ledongthuc/pdf"

func (e *Extractor) ExtractPDF(f multipart.File, password string) string {
    // Write to a temp file (pdftext needs a file path)
    tmp, _ := os.CreateTemp("", "finagent-*.pdf")
    defer os.Remove(tmp.Name())
    io.Copy(tmp, f)

    r, err := pdf.Open(tmp.Name())
    if err != nil { return "" }

    var sb strings.Builder
    for i := 1; i <= r.NumPage(); i++ {
        text, _ := r.Page(i).GetPlainText(nil)
        sb.WriteString(text)
    }
    return sb.String()
}
```

### 7.2 Image OCR Extraction

Uses `gosseract` (Go wrapper for Tesseract). Tesseract is installed once as a system package:

```bash
sudo apt install tesseract-ocr tesseract-ocr-eng
```

```go
import "github.com/otiai10/gosseract/v2"

func (e *Extractor) ExtractImage(f multipart.File) string {
    tmp, _ := os.CreateTemp("", "finagent-*.jpg")
    defer os.Remove(tmp.Name())
    io.Copy(tmp, f)

    client := gosseract.NewClient()
    defer client.Close()
    client.SetImage(tmp.Name())
    client.Languages = []string{"eng"}
    text, _ := client.Text()
    return text
}
```

### 7.3 Scanned PDF (PDF with Embedded Images)

Scanned bank PDFs require an intermediate step: convert each page to an image, then OCR each image:

```go
// Uses pdfcpu to render pages + ImageMagick (system) to convert to PNG
// Then runs gosseract on each page image
func (e *Extractor) ExtractScannedPDF(f multipart.File, password string) string {
    // Step 1: Use pdfcpu to extract page images
    // Step 2: Run gosseract on each image
    // Step 3: Concatenate all page texts
}
```

### 7.4 Password-Protected PDFs

```go
// pdfcpu handles decryption with a known password
import "github.com/pdfcpu/pdfcpu/pkg/api"

func (e *Extractor) DecryptPDF(srcPath, dstPath, password string) error {
    conf := model.NewDefaultConfiguration()
    conf.UserPW = password
    return api.DecryptFile(srcPath, dstPath, conf)
}
```

***

## 8. LLM Bridge Module (`domain/llm.go`)

The LLM bridge communicates with Ollama's local REST API. It never touches any external service.

### 8.1 Transaction Extraction Prompt

```go
const EXTRACTION_PROMPT = `
You are a financial data extractor. Extract ALL transactions from the bank 
statement text below. Output ONLY a valid JSON array. No commentary, no 
explanation — only the JSON.

Schema per transaction:
{ "date": "YYYY-MM-DD", "description": "string", "debit": number_or_null, 
  "credit": number_or_null, "balance": number_or_null }

STATEMENT TEXT:
%s
`

func (l *LLMBridge) ExtractTransactions(text string) []Transaction {
    prompt := fmt.Sprintf(EXTRACTION_PROMPT, text)
    raw := l.callOllama(prompt)
    
    var transactions []Transaction
    json.Unmarshal([]byte(raw), &transactions)
    return transactions
}
```

### 8.2 Ollama Streaming

```go
func (l *LLMBridge) StreamChat(ctx *FinancialContext, message string, onToken func(string)) {
    body := map[string]interface{}{
        "model":  l.model,  // e.g. "llama3:8b"
        "prompt": buildPrompt(ctx, message),
        "stream": true,
    }

    resp, _ := http.Post("http://localhost:11434/api/generate",
        "application/json", marshalJSON(body))
    defer resp.Body.Close()

    scanner := bufio.NewScanner(resp.Body)
    for scanner.Scan() {
        var chunk OllamaStreamChunk
        json.Unmarshal(scanner.Bytes(), &chunk)
        if chunk.Response != "" {
            onToken(chunk.Response)
        }
        if chunk.Done { break }
    }
}
```

***

## 9. Replacing the Chat UI Module

When you're ready to swap `chat.html` for a different interface, here is the exact process:

### What Changes (New Adapter)
- A new frontend file, a Telegram bot handler, or a mobile app
- Must call the same `/api/v1/*` endpoints
- Must send `X-Session-Token` header with every request
- Must handle SSE streaming for both text chat and file upload progress

### What Does NOT Change (Core Domain)
- `domain/sanitizer.go` — security filter stays identical
- `domain/extractor.go` — PDF/OCR processing stays identical
- `domain/llm.go` — Ollama bridge stays identical
- PostgreSQL schema — chat history and financial ledger stay identical
- All business logic — financial calculations, goal fencing, budget checks

### Replacing with Telegram Bot

```go
// In your Telegram webhook handler, instead of SSE streaming,
// you collect the full response and send it as a Telegram message:

func TelegramChatHandler(msg *tgbotapi.Message) {
    if domain.Sanitizer.ShouldBlock(msg.Text) {
        bot.Send(tgbotapi.NewMessage(msg.Chat.ID, "⚠️ Sensitive pattern detected. Message not processed."))
        return
    }
    ctx := domain.BuildContext(fmt.Sprintf("tg-%d", msg.Chat.ID))
    response := domain.LLM.Chat(ctx, msg.Text) // non-streaming version
    bot.Send(tgbotapi.NewMessage(msg.Chat.ID, response))
}
```

### Replacing with Open WebUI

Since Open WebUI speaks the **OpenAI-compatible API**, you can add a thin compatibility shim to your Go backend:

```go
// Add a /v1/chat/completions endpoint that maps to your domain layer
// Open WebUI will connect to http://localhost:8080 as if it's OpenAI
```

***

## 10. Environment Configuration

All sensitive values are loaded from environment variables — never hardcoded:

```bash
# .env (never committed to git)
FINAGENT_SESSION_SECRET=your-strong-random-secret
FINAGENT_DB_URL=postgresql://localhost:5432/finagent
FINAGENT_OLLAMA_URL=http://localhost:11434
FINAGENT_OLLAMA_MODEL=llama3:8b
FINAGENT_MAX_UPLOAD_MB=32
FINAGENT_BACKUP_BUCKET=s3://your-encrypted-backup-bucket
```

***

## 11. Go Dependencies Summary

| Package | Purpose | External Network? |
|---------|---------|-------------------|
| `net/http` (stdlib) | HTTP server, file upload, SSE | Never |
| `encoding/json` (stdlib) | JSON parsing/serialization | Never |
| `database/sql` + `lib/pq` | PostgreSQL client | Local only |
| `ledongthuc/pdf` | Digital PDF text extraction | Never |
| `pdfcpu/pdfcpu` | Password-protected PDF decryption | Never |
| `otiai10/gosseract` | OCR via Tesseract (C binding) | Never |
| `google/uuid` | Session ID generation | Never |
| System: `tesseract-ocr` | OCR engine (apt package) | Never |

All dependencies are pure local computation. No package phones home. No analytics. No telemetry.

***

## 12. V1 Scope vs. Future Enhancements

### V1 (Build Now)
- Text chat with SSE streaming
- PDF upload (digital only — most Indian bank statements)
- Image upload with OCR
- Chat history stored in PostgreSQL
- Session token auth (single user)
- Sanitizer for text + extracted content
- Drag-and-drop file support in UI

### V2 (After V1 Proves Useful)
- Scanned PDF support (pdfcpu → Tesseract pipeline)
- Voice input (Whisper.cpp local transcription)
- Password-protected PDF prompt in UI
- Live balance/goals sidebar (requires additional PostgreSQL queries)
- Cloudflare Tunnel exposure for access outside home Wi-Fi
- Telegram bot as second adapter (parallel to web UI)

### V3 (Long Term)
- Mobile PWA wrapper around the same `/api/v1/*` endpoints
- OpenAI-compatible shim for Open WebUI compatibility
- Multi-session support with per-session encryption keys

***

*This document reflects the V1 design decisions made during the FinAgent architecture brainstorm session (April 2026). The primary driver throughout is absolute data sovereignty — every design choice is evaluated against the question: does this send unencrypted financial data outside the Asus TUF A15?*

---
## Related Documents

| Document                 | Location                                                  | Relationship                                               |
| ------------------------ | --------------------------------------------------------- | ---------------------------------------------------------- |
| 🏭 Infrastructure        | [[🏭 Infrastructure]]                                     | Hardware, tunnel, backup — inherited assumptions           |
| 🗣️ Initial Conversation | [[🗣️ Initial Conversation]]                              | Origin decisions: why local, why Telegram, why self-hosted |
| 💪🏻 Capabilities v1.0.0 | [[AI Finance Assistant/💪🏻 Capabilities/v1.0.0\|v1.0.0]] | Agent features the UI must expose                          |
| 🧰 Tools and MCPs v1.0.0 | [[AI Finance Assistant/🧰 Tools and MCPs/v1.0.0\|v1.0.0]] | MCP tool calls that affect SSE stream length               |
| 🛠️ Tech: LangChainGo    | [[Agent Framework LangChainGo over Google ADK Go]]        | LLM bridge implementation choice                           |
| 🤖 Ollama Try-Outs       | [[Ollama Try-Outs]]                                       | Model selection for streaming speed                        |
