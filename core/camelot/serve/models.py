"""Pydantic schemas for the serve API (source of the generated TS types)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Health(BaseModel):
    status: str
    version: str
    root: str


class RootInfo(BaseModel):
    root: str
    total: int
    archives: int
    errors: int
    scanned_at: float
    counts: dict[str, int]


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


class AssetDetail(AssetSummary):
    metadata: dict[str, Any] = {}


class AssetPage(BaseModel):
    items: list[AssetSummary]
    total: int
    page: int
    limit: int
