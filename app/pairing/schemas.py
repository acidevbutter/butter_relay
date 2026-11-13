import uuid

from pydantic import BaseModel, EmailStr, Field


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=10, max_length=200)


class ExchangeResponse(BaseModel):
    """The plaintext key appears only here, once."""

    api_key_id: uuid.UUID
    plaintext_key: str
    scopes: list[str]
    project_slug: str


class ConnectRequestCreate(BaseModel):
    """Sent by the plugin, server to server, before the browser goes to the consent page."""

    return_url: str = Field(min_length=10, max_length=500)
    state: str = Field(min_length=8, max_length=200)
    site_name: str = Field(min_length=1, max_length=200)
    site_url: str = Field(min_length=8, max_length=500)
    sender_email: EmailStr
    sender_name: str = Field(min_length=1, max_length=200)


class ConnectRequestCreated(BaseModel):
    request_id: str
    expires_in_seconds: int
