from dataclasses import dataclass
import os
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class KeyEntry:
    """Represents one configured API key and its associated LiteLLM model."""

    key_id: str
    api_key: str
    model: str


@dataclass(frozen=True)
class Settings:
    """Application settings loaded from environment variables."""

    key_pool: tuple[KeyEntry, ...]
    default_model: str
    database_url: str
    database_path: str
    allowed_origins: tuple[str, ...]
    enable_failure_simulation: bool
    default_system_prompt: str
    cache_type: str
    llm_timeout_seconds: float


def parse_sqlite_path(database_url: str) -> str:
    """Convert a SQLite DATABASE_URL into a filesystem path or ':memory:'."""
    url = (database_url or "").strip()
    if not url or url in (":memory:", "sqlite:///:memory:", "sqlite://:memory:"):
        return ":memory:"
    if url.startswith("sqlite:///"):
        return url[len("sqlite:///") :]
    if url.startswith("sqlite://"):
        return url[len("sqlite://") :]
    return url


def _parse_csv(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def load_settings() -> Settings:
    """Read and normalize all backend settings from environment variables."""
    default_model = os.getenv("LLM_MODEL", "gemini/gemini-2.0-flash").strip() or "gemini/gemini-2.0-flash"
    raw_keys = _parse_csv(os.getenv("LLM_API_KEYS", ""))
    raw_models = _parse_csv(os.getenv("LLM_MODELS", ""))

    entries: list[KeyEntry] = []
    for idx, api_key in enumerate(raw_keys, start=1):
        model = raw_models[idx - 1] if idx - 1 < len(raw_models) else default_model
        entries.append(KeyEntry(key_id=f"key_{idx}", api_key=api_key, model=model))

    database_url = os.getenv("DATABASE_URL", "sqlite:///./data/app.db").strip() or "sqlite:///./data/app.db"
    database_path = parse_sqlite_path(database_url)

    origins_raw = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    allowed_origins = tuple(_parse_csv(origins_raw)) or ("http://localhost:3000",)

    enable_sim = os.getenv("ENABLE_FAILURE_SIMULATION", "false").strip().lower() == "true"
    default_system_prompt = (
        os.getenv(
            "DEFAULT_SYSTEM_PROMPT",
            "You are a helpful, accurate, and concise AI assistant.",
        ).strip()
        or "You are a helpful, accurate, and concise AI assistant."
    )
    cache_type = os.getenv("LITELLM_CACHE_TYPE", "local").strip().lower() or "local"

    try:
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    except ValueError:
        timeout = 30.0

    return Settings(
        key_pool=tuple(entries),
        default_model=default_model,
        database_url=database_url,
        database_path=database_path,
        allowed_origins=allowed_origins,
        enable_failure_simulation=enable_sim,
        default_system_prompt=default_system_prompt,
        cache_type=cache_type,
        llm_timeout_seconds=timeout,
    )
