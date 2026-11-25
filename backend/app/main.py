from contextlib import asynccontextmanager
import logging
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, load_settings
from app.db import Database
from app.gateway import AllProvidersUnavailableError, LLMGateway
from app.schemas import (
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SessionMessagesResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def create_app(custom_settings: Optional[Settings] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = custom_settings or load_settings()
    db = Database(settings.database_path)
    db.init_db()
    gateway = LLMGateway(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db()
        yield

    application = FastAPI(
        title="Lyftr AI LLM Gateway Chatbot",
        version="1.0.0",
        lifespan=lifespan,
    )

    application.state.settings = settings
    application.state.db = db
    application.state.gateway = gateway

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()},
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled server error")
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error"},
        )

    @application.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        try:
            db_ok = db.check_health()
        except Exception as exc:
            logger.error("Health check DB failure: %s", type(exc).__name__)
            raise HTTPException(status_code=503, detail="database unavailable")

        if not db_ok:
            raise HTTPException(status_code=503, detail="database unavailable")
        if len(settings.key_pool) == 0:
            raise HTTPException(status_code=503, detail="no api keys configured")

        return HealthResponse(
            status="ok",
            keys_configured=len(settings.key_pool),
            cache_enabled=gateway.cache_enabled,
        )

    @application.post("/chat", response_model=ChatResponse)
    def chat(
        payload: ChatRequest,
        x_simulate_failure: Optional[str] = Header(default=None, alias="X-Simulate-Failure"),
    ) -> ChatResponse:
        stored_prompt, history = db.get_session_context(payload.session_id)

        effective_system_prompt = (
            payload.system_prompt
            if payload.system_prompt is not None
            else (stored_prompt or settings.default_system_prompt)
        )

        llm_messages: list[dict[str, str]] = [
            {"role": "system", "content": effective_system_prompt},
            *history,
            {"role": "user", "content": payload.message},
        ]

        try:
            result = gateway.generate_reply(
                messages=llm_messages,
                simulate_failure_header=x_simulate_failure,
            )
        except AllProvidersUnavailableError:
            raise HTTPException(status_code=503, detail="all providers unavailable")

        persisted_prompt = (
            payload.system_prompt if payload.system_prompt is not None else stored_prompt
        )
        db.save_turn(
            session_id=payload.session_id,
            user_content=payload.message,
            assistant_content=result["reply"],
            metadata=result["metadata"],
            system_prompt=persisted_prompt,
        )

        return ChatResponse(
            session_id=payload.session_id,
            reply=result["reply"],
            metadata=result["metadata"],
        )

    @application.get(
        "/sessions/{session_id}/messages",
        response_model=SessionMessagesResponse,
    )
    def get_session_messages(session_id: str) -> SessionMessagesResponse:
        messages = db.get_session_messages(session_id)
        if messages is None:
            raise HTTPException(status_code=404, detail="session not found")
        return SessionMessagesResponse(
            session_id=session_id,
            messages=messages,
        )

    return application


app = create_app()
