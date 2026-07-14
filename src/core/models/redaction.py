from typing import Literal, list, Annotated
from pydantic import BaseModel, Field

# Create different redaction targets. The possible types are:
# 1. Bounding box target
# 2. Text target
# 3. Polygon target
# 4. Page target
# 5. Regex target

class BaseTargetModel(BaseModel):
    """
    Base class for redaction targets.
    """
    type: str

# Bounding box redaction target model
class BoundingBoxTarget(BaseTargetModel):
    """
    Represents bounding box targets for redaction.
    """
    type: Literal["bounding_box"]
    values: list[BoundingBox]  # List of bounding boxes to redact

class BoundingBox(BaseModel):
    """
    Represents a bounding box for redaction.
    """
    page: int
    x: float
    y: float
    width: float
    height: float
    units: Literal["pixels", "inches", "normalized"] = "normalized"  # Default to normalized

# Text redaction target model
class TextTarget(BaseTargetModel):
    """
    Represents text targets for redaction.
    """
    type: Literal["text"]
    values: list[str]  # List of text strings to redact
    pages: list[int] | None = None

# Polygon redaction target model
class PolygonTarget(BaseTargetModel):
    """
    Represents polygon targets for redaction.
    """
    type: Literal["polygon"]
    values: list[Polygon]  # List of polygons to redact

class Polygon(BaseModel):
    page: int
    points: list[Point]

class Point(BaseModel):
    x: float
    y: float

# Page redaction target model
class PageTarget(BaseTargetModel):
    """
    Represents page targets for redaction.
    """
    type: Literal["page"]
    values: list[int]  # List of page numbers to redact

# Regex redaction target model
class RegexTarget(BaseTargetModel):
    """
    Represents regex targets for redaction.
    """
    type: Literal["regex"]
    patterns: list[str]  # List of regex patterns to redact
    pages: list[int] | None = None  # Optional list of pages to apply regex on
    ignore_case: bool = True  # Default to ignore case
    only_first_match: bool = False  # Default to not only first match
    allow_unicode: bool = False # Default to allow unicode characters in regex

# Universal redaction target model that can represent any type of target
RedactionTarget = Annotated[BoundingBoxTarget | TextTarget | PolygonTarget | PageTarget | RegexTarget, Field(discriminator="type")]


# Redaction options model
class RedactionOptions(BaseModel):
    """
    Represents options for the redaction process.
    """
    fill_color: str = "#000000"  # Default fill color for redaction
    fill_opacity: float = 1.0  # Default fill opacity for redaction
    permanent_redaction: bool = True  # Default to permanent redaction