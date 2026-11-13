import uuid

from pydantic import BaseModel, Field


class ExchangeRequest(BaseModel):
    code: str = Field(min_length=10, max_length=200)


class ExchangeResponse(BaseModel):
    """The plaintext key appears only here, once."""

    api_key_id: uuid.UUID
    plaintext_key: str
    scopes: list[str]
    project_slug: str
