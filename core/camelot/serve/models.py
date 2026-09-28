"""Pydantic schemas for the serve API (source of the generated TS types)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
    root: str
    build: str = "unknown"


class RootInfo(BaseModel):
    root: str
    total: int
    archives: int
    errors: int
    scanned_at: float
    counts: dict[str, int]
    dirs: dict[str, int] = {}


class AssetSummary(BaseModel):
    id: str
    type: str
    name: str
    path: str
    origin: str
    size: int
    mtime: float
    archive: str | None = None
    entry: str | None = None


class CharacterOptions(BaseModel):
    race: str
    race_id: int
    female: bool
    faces: int
    hair_styles: int
    hair_colors: int
    eye_colors: int
    skin_tones: int
    has_decal: bool
    cloak_variants: int
    body_variants: int
    # `pskins.csv` armour materials, and the one a preview starts on.
    materials: list[str] = []
    material: str = ""


class AssetDetail(AssetSummary):
    metadata: dict[str, Any] = {}


class AssetPage(BaseModel):
    items: list[AssetSummary]
    total: int
    page: int
    limit: int
