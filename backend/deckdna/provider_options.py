"""Optional OpenAI chat controls; never override routing or structured output."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatOptions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reasoning_effort: Literal["none", "minimal", "low", "medium", "high", "xhigh"] | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=32768)
    max_completion_tokens: int | None = Field(default=None, ge=1, le=32768)

    @model_validator(mode="after")
    def one_token_limit(self) -> "ChatOptions":
        if self.max_tokens is not None and self.max_completion_tokens is not None:
            raise ValueError("configure only one completion token limit")
        return self
