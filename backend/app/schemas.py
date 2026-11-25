from typing import Any, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator


class HealthResponse(BaseModel):
    status: str
    keys_configured: int
    cache_enabled: bool


class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-generated session identifier (1-128 chars)")
    message: str = Field(..., description="User message (1-4000 chars after trimming)")
    system_prompt: Optional[str] = Field(
        default=None,
        description="Optional system prompt override for this session",
    )

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("session_id must be a string")
        trimmed = value.strip()
        if not trimmed or len(value) > 128 or len(trimmed) > 128:
            raise ValueError("session_id must be between 1 and 128 characters")
        return value

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        if not isinstance(value, str):
            raise ValueError("message must be a string")
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("message must not be empty")
        if len(trimmed) > 4000:
            raise ValueError("message must not exceed 4000 characters after trimming")
        return trimmed

    @field_validator("system_prompt")
    @classmethod
    def validate_system_prompt(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        if len(trimmed) > 4000:
            raise ValueError("system_prompt must not exceed 4000 characters")
        return trimmed if trimmed else None


class ChatMetadata(BaseModel):
    resolution_source: Literal["LIVE_INFERENCE", "LLM_CACHE"]
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: Optional[str]
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: ChatMetadata


class UserMessageItem(BaseModel):
    role: Literal["user"]
    content: str


class AssistantMessageItem(BaseModel):
    role: Literal["assistant"]
    content: str
    metadata: dict[str, Any]


MessageItem = Union[UserMessageItem, AssistantMessageItem]


class SessionMessagesResponse(BaseModel):
    session_id: str
    messages: list[MessageItem]
