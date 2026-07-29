"""Pydantic models describing redaction work."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BoundingBox(BaseModel):
    """A rectangular area expressed as proportions of a zero-based page."""

    model_config = ConfigDict(extra="forbid")

    page: int = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)

    @model_validator(mode="after")
    def within_page(self) -> "BoundingBox":
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("normalized bounding boxes must fit within the page")
        return self


class BaseTargetModel(BaseModel):
    type: str


class BoundingBoxTarget(BaseTargetModel):
    type: Literal["bounding_box"]
    values: list[BoundingBox] = Field(min_length=1)


class TextTarget(BaseTargetModel):
    type: Literal["text"]
    values: list[str] = Field(min_length=1)
    pages: list[int] | None = None
    ignore_case: bool = False
    partial_match: bool = False


class PageTarget(BaseTargetModel):
    type: Literal["page"]
    values: list[int] = Field(min_length=1)


class RegexTarget(BaseTargetModel):
    type: Literal["regex"]
    patterns: list[str] = Field(min_length=1)
    pages: list[int] | None = None
    ignore_case: bool = True
    only_first_match: bool = False
    allow_unicode: bool = False


RedactionTarget = Annotated[
    BoundingBoxTarget | TextTarget | PageTarget | RegexTarget,
    Field(discriminator="type"),
]


class RedactionOptions(BaseModel):
    fill_color: str = "#000000"
    fill_opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    permanent_redaction: bool = True
    redaction_type: Literal["mask", "asterisks"] = "asterisks"
