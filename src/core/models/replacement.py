"""Pydantic models for text data replacement."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TextReplacementTarget(BaseModel):
    """Text to locate, and the strategy used to replace every occurrence."""

    type: Literal["text"]
    values: list[str] = Field(min_length=1)
    replacement_type: Literal["PARTIAL", "STATIC", "REGEX"]
    static_text: str | None = None
    pages: list[int] | None = None
    ignore_case: bool = False
    partial_match: bool = False

    @model_validator(mode="after")
    def validate_static_text(self) -> "TextReplacementTarget":
        if self.replacement_type == "STATIC" and not self.static_text:
            raise ValueError("static_text is required when replacement_type is STATIC")
        if self.replacement_type != "STATIC" and self.static_text is not None:
            raise ValueError("static_text is only valid when replacement_type is STATIC")
        return self


ReplacementTarget = TextReplacementTarget
