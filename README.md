# Lyftr AI — Full-Stack LLM Chatbot with Key Failover & Response Caching

A full-stack multi-turn LLM chatbot featuring an ordered API key pool with automatic failover, LiteLLM built-in response caching, per-request token tracking, and SQLite session persistence.

---

## 1. Live URLs

- **Frontend (Public URL):** `<ADD_DEPLOYED_FRONTEND_URL>` (e.g. Vercel / Netlify / Cloudflare Pages)
- **Backend (Public URL):** `<ADD_DEPLOYED_BACKEND_URL>` (e.g. Render / Railway / Fly.io)
- **Health Check:** `<ADD_DEPLOYED_BACKEND_URL>/health`

---

## 2. Run locally: with Docker and without

### Option A: With Docker (Recommended)

1. Copy the example environment file and add at least 2 API keys:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and set `LLM_API_KEYS=your-key-1,your-key-2` and `LLM_MODEL` (default `gemini/gemini-2.0-flash`).

2. Start both services using `make`:
   ```bash
   make up
   ```
   - Frontend: `http://localhost:3000`
   - Backend: `http://localhost:8000`
   - SQLite database persists in the named Docker volume mounted at `/data/app.db`.

3. Stop the stack:
   ```bash
   make down
   ```

### Option B: Without Docker

**Backend (Python 3.12+):**
```bash
cd backend
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Configure env vars (or create a .env file in repo root / backend)
export LLM_API_KEYS="your-key-1,your-key-2"
export LLM_MODEL="gemini/gemini-2.0-flash"
export DATABASE_URL="sqlite:///./data/app.db"
export ALLOWED_ORIGINS="http://localhost:3000,http://127.0.0.1:3000"
export ENABLE_FAILURE_SIMULATION="true"

uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Frontend (Node 20+):**
```bash
cd frontend
npm install
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

**Run backend tests:**
```bash
cd backend
pytest -v
```

---

## 3. Design decisions: key fallback, caching, SQLite session storage

### Key Fallback (`backend/app/gateway.py`)
- Keys are parsed from `LLM_API_KEYS` in exact order (`key_1`, `key_2`, ...), optionally paired with per-key models via `LLM_MODELS` or a shared `LLM_MODEL`.
- `LLMGateway.generate_reply` iterates through the key pool sequentially with `num_retries=0` per key so failover is immediate and deterministic rather than hiding retries behind a single failing key.
- Any provider/transient error (HTTP `429`, quota exhaustion, `5xx`, timeouts, or connection errors) increments `fallback_attempts` and advances to the next key without exposing raw API keys or stack traces.
- If every key fails, the API raises `AllProvidersUnavailableError`, returning HTTP `503` with `{"detail": "all providers unavailable"}` and skipping database persistence for that turn.

### Response Caching (`litellm.Cache`)
- LiteLLM's built-in cache (`litellm.Cache(type="local")` by default, configurable via `LITELLM_CACHE_TYPE`) is enabled at startup.
- The cache key is computed by LiteLLM from the `model` and the full `messages` array (`system_prompt` + session history + new user message). Because `session_id` is **not** passed to `litellm.completion`, identical first prompts across separate sessions hit the cache immediately.
- Cache hits are detected via `response._hidden_params["cache_hit"]`, returning `resolution_source: "LLM_CACHE"`, `key_id: null`, `fallback_attempts: 0`, and the cached token counts.
- When `X-Simulate-Failure` is active (with `ENABLE_FAILURE_SIMULATION=true`), the gateway passes `cache={"no-cache": True, "no-store": True}` so failure simulation always exercises the key pool instead of short-circuiting on cache.

### SQLite Session Storage (`backend/app/db.py`)
- Uses Python's standard `sqlite3` with WAL mode (`PRAGMA journal_mode=WAL`) for concurrent read/write safety without external services.
- Schema (`sessions` and `messages` tables + `idx_messages_session_id` index) is created automatically on startup at the path parsed from `DATABASE_URL` (e.g. `/data/app.db` inside Docker).
- `system_prompt` overrides are stored in `sessions` so multi-turn calls keep the session's system prompt while `GET /sessions/{session_id}/messages` returns only `user` and `assistant` messages oldest-first.
- Turns are committed atomically via `save_turn()` **only after** a valid LLM response is produced, ensuring failed `422` and `503` requests never pollute session history.

---

## 4. Verification: how to see failover, cache hits and per-message token usage

1. **Health check:**
   ```bash
   curl http://localhost:8000/health
   # {"status":"ok","keys_configured":2,"cache_enabled":true}
   ```

2. **Live inference + cache hit across sessions:**
   ```bash
   # Request 1 -> LIVE_INFERENCE
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo-1","message":"What is the capital of France?"}'

   # Request 2 (new session, identical message) -> LLM_CACHE, key_id=null, faster latency_ms
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -d '{"session_id":"demo-2","message":"What is the capital of France?"}'
   ```

3. **Key failover with `X-Simulate-Failure`:**
   ```bash
   # key_1 fails with 429 -> succeeds via key_2 with fallback_attempts=1
   curl -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -H "X-Simulate-Failure: key_1=429" \
     -d '{"session_id":"demo-3","message":"Name two primary colors."}'

   # All keys fail -> HTTP 503 {"detail": "all providers unavailable"}
   curl -i -X POST http://localhost:8000/chat \
     -H "Content-Type: application/json" \
     -H "X-Simulate-Failure: *=429" \
     -d '{"session_id":"demo-4","message":"This should fail."}'
   ```

4. **Per-message token usage & session history:**
   - Send multiple turns in the React UI (`http://localhost:3000`); each assistant reply displays `LIVE_INFERENCE` / `LLM_CACHE`, model, latency in `ms`, and per-request `In` / `Out` token counts (`meta-input-tokens`, `meta-output-tokens`) rather than cumulative totals.
   - Verify stored SQLite history:
     ```bash
     curl http://localhost:8000/sessions/demo-1/messages
     ```

---

## 5. Setup Used

- **Editor / IDE:** Antigravity IDE / VS Code
- **AI Tools:** Antigravity AI coding assistant
- **Local Verification:** Python 3.13 + pytest + FastAPI TestClient

---

## 6. Bonus features (if any)

- **UI Failover Simulator (`X-Simulate-Failure` selector):** Added a dropdown in the React header bar (`None`, `key_1=429`, `key_1=500`, `*=429`) so evaluators can test failover and 503 error handling directly in the browser as well as via `curl`.
- **Live Backend Status Indicator & New Session Button:** The UI polls `GET /health` on load to display configured key count and cache status, and lets users start a fresh `session_id` without refreshing the page.
- **Per-Key Model Mapping (`LLM_MODELS`):** Optionally allows pairing each key in `LLM_API_KEYS` with a different LiteLLM model while defaulting cleanly to `LLM_MODEL`.
