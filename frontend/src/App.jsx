import { useEffect, useRef, useState } from "react";
import { fetchHealth, sendChatMessage } from "./api.js";

function createSessionId() {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return `s-${crypto.randomUUID().slice(0, 8)}`;
  }
  return `s-${Math.random().toString(36).slice(2, 10)}`;
}

const STARTER_PROMPTS = [
  "What is the capital of France?",
  "Explain API key failover in 2 sentences.",
  "Write a haiku about response caching.",
];

export default function App() {
  const [sessionId, setSessionId] = useState(() => createSessionId());
  const [systemPrompt, setSystemPrompt] = useState("");
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [health, setHealth] = useState(null);
  const [healthError, setHealthError] = useState(null);
  const [simulateFailure, setSimulateFailure] = useState("");

  const messagesEndRef = useRef(null);

  useEffect(() => {
    let active = true;
    fetchHealth()
      .then((data) => {
        if (active) {
          setHealth(data);
          setHealthError(null);
        }
      })
      .catch((err) => {
        if (active) {
          setHealthError(err.message);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isSending]);

  function handleNewSession() {
    setSessionId(createSessionId());
    setMessages([]);
  }

  async function handleSubmit(overrideText) {
    const textToSend =
      typeof overrideText === "string" ? overrideText : input;

    if (isSending) {
      return;
    }

    setInput("");
    setMessages((prev) => [
      ...prev,
      {
        id: `u-${Date.now()}-${Math.random()}`,
        role: "user",
        content: textToSend,
      },
    ]);

    setIsSending(true);
    try {
      const data = await sendChatMessage({
        sessionId,
        message: textToSend,
        systemPrompt,
        simulateFailure,
      });

      setMessages((prev) => [
        ...prev,
        {
          id: `a-${Date.now()}-${Math.random()}`,
          role: "assistant",
          content: data.reply,
          metadata: data.metadata,
        },
      ]);
    } catch (err) {
      setMessages((prev) => [
        ...prev,
        {
          id: `e-${Date.now()}-${Math.random()}`,
          role: "error",
          content: err.message || "An unexpected error occurred.",
        },
      ]);
    } finally {
      setIsSending(false);
    }
  }

  function handleFormSubmit(e) {
    e.preventDefault();
    if (!input.trim()) {
      return;
    }
    handleSubmit();
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (input.trim() && !isSending) {
        handleSubmit();
      }
    }
  }

  return (
    <div className="app-shell">
      <header className="top-bar">
        <div className="brand-block">
          <div className="brand-dot" aria-hidden="true" />
          <div>
            <h1 className="brand-title">Lyftr AI Gateway</h1>
            <p className="brand-subtitle">
              Multi-turn LLM Chatbot with Key Failover &amp; Response Caching
            </p>
          </div>
        </div>

        <div className="header-controls">
          <div className="status-pill" title="Backend /health status">
            <span
              className={`status-indicator ${
                health ? "online" : healthError ? "offline" : "checking"
              }`}
            />
            {health ? (
              <span>
                Online · {health.keys_configured} keys · Cache{" "}
                {health.cache_enabled ? "ON" : "OFF"}
              </span>
            ) : healthError ? (
              <span>Backend unreachable</span>
            ) : (
              <span>Checking backend…</span>
            )}
          </div>

          <div className="session-pill" title="Active session ID">
            Session: <code>{sessionId}</code>
          </div>

          <button
            type="button"
            className="secondary-btn"
            onClick={handleNewSession}
            disabled={isSending}
          >
            New Session
          </button>
        </div>
      </header>

      <section className="config-bar">
        <div className="config-field config-prompt">
          <label htmlFor="system-prompt-input">System Prompt (optional)</label>
          <input
            id="system-prompt-input"
            data-testid="system-prompt-input"
            type="text"
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
            placeholder="Override default system prompt for this session..."
            disabled={isSending}
          />
        </div>

        <div className="config-field config-sim">
          <label htmlFor="simulate-failure-select">
            Failover Test (<code>X-Simulate-Failure</code>)
          </label>
          <select
            id="simulate-failure-select"
            value={simulateFailure}
            onChange={(e) => setSimulateFailure(e.target.value)}
            disabled={isSending}
          >
            <option value="">None (Normal routing)</option>
            <option value="key_1=429">key_1=429 (Rate limit → key_2)</option>
            <option value="key_1=500">key_1=500 (Server err → key_2)</option>
            <option value="*=429">*=429 (All keys fail → 503)</option>
          </select>
        </div>
      </section>

      <main className="chat-stream">
        {messages.length === 0 ? (
          <div className="empty-state">
            <h2>Start a resilient conversation</h2>
            <p>
              Messages are stored per session in SQLite. Repeat an identical
              prompt in a new session to observe an instant{" "}
              <code>LLM_CACHE</code> hit, or simulate a key failure above.
            </p>
            <div className="starter-grid">
              {STARTER_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  className="starter-btn"
                  onClick={() => handleSubmit(prompt)}
                  disabled={isSending}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          messages.map((msg) => {
            if (msg.role === "user") {
              return (
                <div key={msg.id} className="message-row user-row">
                  <div className="message-bubble user-bubble">
                    <div className="message-role">You</div>
                    <div className="message-text">{msg.content}</div>
                  </div>
                </div>
              );
            }

            if (msg.role === "error") {
              return (
                <div key={msg.id} className="message-row assistant-row">
                  <div className="message-bubble error-bubble" role="alert">
                    <div className="message-role">Gateway Error</div>
                    <div className="message-text">{msg.content}</div>
                  </div>
                </div>
              );
            }

            const meta = msg.metadata || {};
            const isCache = meta.resolution_source === "LLM_CACHE";

            return (
              <div key={msg.id} className="message-row assistant-row">
                <div
                  className="message-bubble assistant-bubble"
                  data-testid="assistant-message"
                >
                  <div className="message-role">Assistant</div>
                  <div className="message-text">{msg.content}</div>

                  <div className="meta-bar">
                    <span
                      className={`meta-badge ${
                        isCache ? "badge-cache" : "badge-live"
                      }`}
                      data-testid="meta-source"
                    >
                      {meta.resolution_source}
                    </span>

                    <span className="meta-chip" data-testid="meta-model">
                      {meta.model}
                    </span>

                    <span className="meta-chip" data-testid="meta-latency">
                      {meta.latency_ms} ms
                    </span>

                    <span className="meta-chip">
                      In:{" "}
                      <strong data-testid="meta-input-tokens">
                        {meta.input_tokens}
                      </strong>
                    </span>

                    <span className="meta-chip">
                      Out:{" "}
                      <strong data-testid="meta-output-tokens">
                        {meta.output_tokens}
                      </strong>
                    </span>

                    {meta.key_id && (
                      <span className="meta-chip meta-key">
                        Key: <strong>{meta.key_id}</strong>
                      </span>
                    )}

                    {meta.fallback_attempts > 0 && (
                      <span className="meta-chip meta-fallback">
                        Failovers: <strong>{meta.fallback_attempts}</strong>
                      </span>
                    )}
                  </div>
                </div>
              </div>
            );
          })
        )}

        {isSending && (
          <div className="message-row assistant-row">
            <div className="message-bubble assistant-bubble loading-bubble">
              <div className="message-role">Assistant</div>
              <div className="typing-dots">
                <span />
                <span />
                <span />
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </main>

      <form className="composer" onSubmit={handleFormSubmit}>
        <textarea
          data-testid="chat-input"
          rows={2}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything... (Enter to send, Shift+Enter for new line)"
          disabled={isSending}
        />
        <button
          type="submit"
          data-testid="send-button"
          className="send-btn"
          disabled={isSending || !input.trim()}
        >
          {isSending ? "Sending…" : "Send"}
        </button>
      </form>
    </div>
  );
}
