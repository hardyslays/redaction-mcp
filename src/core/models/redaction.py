"""Pydantic models describing redaction work."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class BoundingBox(BaseModel):
    """A rectangular area on a zero-based PDF page."""

    page: int = Field(ge=0)
    x: float
    y: float
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    units: Literal["pixels", "inches", "normalized"] = "normalized"


class BaseTargetModel(BaseModel):
    type: str


class BoundingBoxTarget(BaseTargetModel):
    type: Literal["bounding_box"]
    values: list[BoundingBox] = Field(min_length=1)


class TextTarget(BaseTargetModel):
    type: Literal["text"]
    values: list[str] = Field(min_length=1)
    pages: list[int] | None = None


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
