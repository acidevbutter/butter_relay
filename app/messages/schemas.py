import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_serializer, model_validator

from app.core.redaction import mask_email, redact
from app.messages.models import MessageStatus


class MessageCreate(BaseModel):
    project_id: uuid.UUID
    template_id: uuid.UUID | None = None
    to_email: str
    payload: dict[str, Any] = {}
    broker_message_id: str | None = None
    provider: str | None = None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    provider: str | None
    to_email: str
    payload: dict[str, Any]
    rendered_subject: str | None
    status: MessageStatus
    attempts: int
    last_error: str | None
    terminal_failure: bool
    provider_message_id: str | None
    broker_message_id: str | None
    received_at: datetime
    sent_at: datetime | None


class MessagePublicRead(BaseModel):
    """What a project's API key may read back (DVB-208 / M-05): no payload (the rendered
    data is personal and transactional), the recipient masked, and the error already
    redacted at the source. The full row stays visible only in the authenticated admin.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    template_id: uuid.UUID | None
    channel: str
    provider: str | None
    to_email: str
    rendered_subject: str | None
    status: MessageStatus
    attempts: int
    last_error: str | None
    terminal_failure: bool
    provider_message_id: str | None
    broker_message_id: str | None
    email_type: str | None = None
    idempotency_key: str | None = None
    received_at: datetime
    sent_at: datetime | None

    @field_serializer("to_email")
    def _mask_to_email(self, value: str) -> str:
        return mask_email(value) or ""

    @field_serializer("last_error")
    def _redact_last_error(self, value: str | None) -> str | None:
        # Rows written before DVB-208 may still hold a raw provider body.
        return redact(value)


# --- HTTP send API (DVB-309 / S4) ----------------------------------------------

MAX_RECIPIENTS = 50
MAX_HTML_BYTES = 1024 * 1024
MAX_ATTACHMENTS_BYTES = 2 * 1024 * 1024
MAX_BODY_BYTES = 3 * 1024 * 1024


class SendAttachment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream", max_length=100)
    content_base64: str


class MessageSendRequest(BaseModel):
    """Body of POST /api/v1/messages/send. Template mode: `type` + `payload`. Raw mode:
    `type` + `subject` + `html` (+ `text`), sent as given. There is no `from`: the
    sender is always the project's own (extra fields are rejected)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=100)
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] = []
    bcc: list[EmailStr] = []
    reply_to: EmailStr | None = None
    subject: str | None = Field(default=None, max_length=500)
    html: str | None = None
    text: str | None = None
    headers: dict[str, str] = {}
    attachments: list[SendAttachment] = []
    payload: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    @model_validator(mode="after")
    def _check_mode_and_limits(self) -> "MessageSendRequest":
        if len(self.to) + len(self.cc) + len(self.bcc) > MAX_RECIPIENTS:
            raise ValueError(f"at most {MAX_RECIPIENTS} recipients (to + cc + bcc)")
        if (self.subject is None) != (self.html is None):
            raise ValueError("raw mode needs both subject and html")
        if self.html is not None and self.payload:
            raise ValueError("send either payload (template mode) or subject/html (raw mode)")
        for name in self.headers:
            if name.lower() in {"from", "sender", "to", "cc", "bcc", "subject", "reply-to"}:
                raise ValueError(f"header {name!r} cannot be set; use the dedicated field")
        return self

    @property
    def is_raw(self) -> bool:
        return self.html is not None


class MessageSendResponse(BaseModel):
    message_id: uuid.UUID
    status: MessageStatus
    type: str
