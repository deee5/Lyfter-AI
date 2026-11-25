import json
import time
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient
import litellm

from app.config import KeyEntry, Settings
from app.main import create_app


def _make_settings(
    tmp_db_path: str,
    enable_failure_simulation: bool = True,
) -> Settings:
    return Settings(
        key_pool=(
            KeyEntry(key_id="key_1", api_key="secret-key-one", model="openai/gpt-4o-mini"),
            KeyEntry(key_id="key_2", api_key="secret-key-two", model="openai/gpt-4o-mini"),
        ),
        default_model="openai/gpt-4o-mini",
        database_url=f"sqlite:///{tmp_db_path}",
        database_path=tmp_db_path,
        allowed_origins=("http://localhost:3000",),
        enable_failure_simulation=enable_failure_simulation,
        default_system_prompt="You are a concise assistant.",
        cache_type="local",
        llm_timeout_seconds=10.0,
    )


def _build_litellm_response(
    content: str,
    model: str = "openai/gpt-4o-mini",
    prompt_tokens: int = 24,
    completion_tokens: int = 8,
):
    return litellm.ModelResponse(
        id="chatcmpl-test",
        model=model,
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    )


def test_health_endpoint(tmp_path):
    db_file = str(tmp_path / "health.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {
        "status": "ok",
        "keys_configured": 2,
        "cache_enabled": True,
    }


def test_validation_errors_return_422_and_do_not_persist(tmp_path):
    db_file = str(tmp_path / "validation.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    # Empty message after whitespace trim
    r1 = client.post("/chat", json={"session_id": "s-val-1", "message": "   "})
    assert r1.status_code == 422
    assert "detail" in r1.json()

    # Message exceeding 4000 characters after trim
    r2 = client.post("/chat", json={"session_id": "s-val-1", "message": "a" * 4001})
    assert r2.status_code == 422
    assert "detail" in r2.json()

    # Empty session_id
    r3 = client.post("/chat", json={"session_id": "   ", "message": "hello"})
    assert r3.status_code == 422
    assert "detail" in r3.json()

    # Malformed JSON
    r4 = client.post(
        "/chat",
        content="{bad json",
        headers={"Content-Type": "application/json"},
    )
    assert r4.status_code == 422
    assert "detail" in r4.json()

    # Ensure nothing was persisted for s-val-1
    hist = client.get("/sessions/s-val-1/messages")
    assert hist.status_code == 404
    assert hist.json() == {"detail": "session not found"}


def test_sessions_stay_isolated(tmp_path):
    db_file = str(tmp_path / "isolation.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    seen_calls: list[list[dict[str, str]]] = []

    def fake_openai_completion(*args, **kwargs):
        messages = kwargs.get("messages") or args[1]
        seen_calls.append(list(messages))
        return _build_litellm_response(
            f"Echo: {messages[-1]['content']}",
            prompt_tokens=20,
            completion_tokens=4,
        )

    with patch("litellm.openai_chat_completions.completion", side_effect=fake_openai_completion):
        r1 = client.post("/chat", json={"session_id": "s-alpha", "message": "Secret in alpha"})
        assert r1.status_code == 200

        r2 = client.post("/chat", json={"session_id": "s-beta", "message": "Hello from beta"})
        assert r2.status_code == 200

        # Beta must only contain system prompt + beta's user message (no alpha history)
        assert seen_calls[1] == [
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Hello from beta"},
        ]

        h1 = client.get("/sessions/s-alpha/messages").json()["messages"]
        h2 = client.get("/sessions/s-beta/messages").json()["messages"]
        assert len(h1) == 2
        assert len(h2) == 2
        assert h1[0]["content"] == "Secret in alpha"
        assert h2[0]["content"] == "Hello from beta"


def test_real_litellm_caching_across_sessions(tmp_path):
    """Verify LiteLLM's built-in cache returns LLM_CACHE on identical first messages across sessions."""
    db_file = str(tmp_path / "cache.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    call_count = 0

    def fake_openai_completion(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        time.sleep(0.03)
        messages = kwargs.get("messages") or args[1]
        user_msg = messages[-1]["content"]
        return _build_litellm_response(
            f"Answer: {user_msg}",
            prompt_tokens=30 + len(user_msg),
            completion_tokens=7,
        )

    with patch("litellm.openai_chat_completions.completion", side_effect=fake_openai_completion):
        # 1st call: live inference
        r1 = client.post(
            "/chat",
            json={"session_id": "s-cache-1", "message": "What is the capital of France?"},
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["session_id"] == "s-cache-1"
        assert d1["reply"] == "Answer: What is the capital of France?"
        assert d1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert d1["metadata"]["key_id"] == "key_1"
        assert d1["metadata"]["fallback_attempts"] == 0
        assert d1["metadata"]["input_tokens"] > 0
        assert d1["metadata"]["output_tokens"] == 7
        assert call_count == 1

        # 2nd call in a brand new session with identical message: cache hit
        r2 = client.post(
            "/chat",
            json={"session_id": "s-cache-2", "message": "What is the capital of France?"},
        )
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2["session_id"] == "s-cache-2"
        assert d2["reply"] == d1["reply"]
        assert d2["metadata"]["resolution_source"] == "LLM_CACHE"
        assert d2["metadata"]["key_id"] is None
        assert d2["metadata"]["fallback_attempts"] == 0
        assert d2["metadata"]["input_tokens"] == d1["metadata"]["input_tokens"]
        assert d2["metadata"]["output_tokens"] == d1["metadata"]["output_tokens"]
        assert d2["metadata"]["latency_ms"] < d1["metadata"]["latency_ms"]
        assert call_count == 1

        # 3rd call with a different message: must NOT hit cache
        r3 = client.post(
            "/chat",
            json={"session_id": "s-cache-3", "message": "What is the capital of Spain?"},
        )
        assert r3.status_code == 200
        d3 = r3.json()
        assert d3["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert call_count == 2


def test_failover_with_simulate_failure_header(tmp_path):
    db_file = str(tmp_path / "failover.db")
    app = create_app(_make_settings(db_file, enable_failure_simulation=True))
    client = TestClient(app)

    seen_api_keys: list[str] = []

    def fake_openai_completion(*args, **kwargs):
        seen_api_keys.append(kwargs.get("api_key", ""))
        return _build_litellm_response("Paris is the capital.", prompt_tokens=22, completion_tokens=5)

    with patch("litellm.openai_chat_completions.completion", side_effect=fake_openai_completion):
        # key_1=429 -> fails over to key_2
        r1 = client.post(
            "/chat",
            json={"session_id": "s-fail-1", "message": "Capital of France?"},
            headers={"X-Simulate-Failure": "key_1=429"},
        )
        assert r1.status_code == 200
        d1 = r1.json()
        assert d1["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert d1["metadata"]["key_id"] == "key_2"
        assert d1["metadata"]["fallback_attempts"] == 1
        assert seen_api_keys == ["secret-key-two"]

        # key_1=500 -> fails over to key_2
        r2 = client.post(
            "/chat",
            json={"session_id": "s-fail-2", "message": "Capital of Germany?"},
            headers={"X-Simulate-Failure": "key_1=500"},
        )
        assert r2.status_code == 200
        assert r2.json()["metadata"]["key_id"] == "key_2"
        assert r2.json()["metadata"]["fallback_attempts"] == 1

        # *=429 -> 503 and nothing added to history
        r3 = client.post(
            "/chat",
            json={"session_id": "s-fail-all", "message": "Should fail"},
            headers={"X-Simulate-Failure": "*=429"},
        )
        assert r3.status_code == 503
        assert r3.json() == {"detail": "all providers unavailable"}

        hist = client.get("/sessions/s-fail-all/messages")
        assert hist.status_code == 404
        assert hist.json() == {"detail": "session not found"}


def test_simulate_failure_ignored_when_disabled(tmp_path):
    db_file = str(tmp_path / "sim_disabled.db")
    app = create_app(_make_settings(db_file, enable_failure_simulation=False))
    client = TestClient(app)

    def fake_openai_completion(*args, **kwargs):
        return _build_litellm_response("Hello!", prompt_tokens=15, completion_tokens=2)

    with patch("litellm.openai_chat_completions.completion", side_effect=fake_openai_completion):
        res = client.post(
            "/chat",
            json={"session_id": "s-sim-off", "message": "Hi"},
            headers={"X-Simulate-Failure": "*=429"},
        )
        assert res.status_code == 200
        assert res.json()["metadata"]["key_id"] == "key_1"


def test_provider_exception_failover_and_never_leaks_keys(tmp_path):
    db_file = str(tmp_path / "real_failover.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    def flaky_completion(*args, **kwargs):
        api_key = kwargs.get("api_key")
        if api_key == "secret-key-one":
            raise litellm.RateLimitError(
                message="429 Too Many Requests for key secret-key-one",
                llm_provider="openai",
                model="openai/gpt-4o-mini",
            )
        return _build_litellm_response("Recovered via key 2", prompt_tokens=18, completion_tokens=4)

    with patch("litellm.openai_chat_completions.completion", side_effect=flaky_completion):
        res = client.post(
            "/chat",
            json={"session_id": "s-real-fail", "message": "Test recovery"},
        )
        assert res.status_code == 200
        body = res.json()
        assert body["metadata"]["key_id"] == "key_2"
        assert body["metadata"]["fallback_attempts"] == 1
        assert "secret-key" not in json.dumps(body)


def test_multi_turn_context_and_session_messages_endpoint(tmp_path):
    db_file = str(tmp_path / "history.db")
    app = create_app(_make_settings(db_file))
    client = TestClient(app)

    seen_histories: list[list[dict[str, str]]] = []

    def tracking_completion(*args, **kwargs):
        messages = kwargs.get("messages") or args[1]
        seen_histories.append(list(messages))
        return _build_litellm_response(
            f"Turn {len(seen_histories)}",
            prompt_tokens=20 * len(seen_histories),
            completion_tokens=5,
        )

    with patch("litellm.openai_chat_completions.completion", side_effect=tracking_completion):
        r1 = client.post(
            "/chat",
            json={
                "session_id": "s-multi",
                "message": "My name is Alice.",
                "system_prompt": "Always be polite.",
            },
        )
        assert r1.status_code == 200

        r2 = client.post(
            "/chat",
            json={"session_id": "s-multi", "message": "What is my name?"},
        )
        assert r2.status_code == 200

        # Second call to LLM must include system prompt + turn 1 + new user message
        assert seen_histories[1] == [
            {"role": "system", "content": "Always be polite."},
            {"role": "user", "content": "My name is Alice."},
            {"role": "assistant", "content": "Turn 1"},
            {"role": "user", "content": "What is my name?"},
        ]

        # GET /sessions/s-multi/messages must omit the system prompt
        hist = client.get("/sessions/s-multi/messages")
        assert hist.status_code == 200
        data = hist.json()
        assert data["session_id"] == "s-multi"
        assert len(data["messages"]) == 4
        assert data["messages"][0] == {"role": "user", "content": "My name is Alice."}
        assert data["messages"][1]["role"] == "assistant"
        assert data["messages"][1]["content"] == "Turn 1"
        assert data["messages"][1]["metadata"]["resolution_source"] == "LIVE_INFERENCE"
        assert data["messages"][2] == {"role": "user", "content": "What is my name?"}
        assert data["messages"][3]["content"] == "Turn 2"
