from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    claim: str
    sourceTitle: str
    url: str = ""
    publishedAt: str = ""
    weight: Literal["primary", "supporting"] = "supporting"


class QuoteRow(BaseModel):
    symbol: str
    name: str
    price: float | None = None
    changePct: float | None = None
    changePct5d: float | None = None
    asOf: str = ""


class SectorOutlook(BaseModel):
    sector: str
    heat: int
    heatScore: float = 0.0
    priceAction: Literal["up", "down", "mixed", "flat", "unknown"] = "unknown"
    calibration: Literal["confirming", "pricedIn", "divergence", "insufficientData"] = (
        "insufficientData"
    )
    direction: Literal["up", "down", "mixed", "unclear"] = "unclear"
    narrative: str
    evidence: list[Evidence] = Field(default_factory=list)
    counterEvidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.4
    invalidatedIf: str = ""


class AnalysisPlan(BaseModel):
    sectors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    newsQueries: list[str] = Field(default_factory=list)
    tickers: list[str] = Field(default_factory=list)
    benchmarks: list[str] = Field(default_factory=list)
    lookbackHours: int = 36
    maxItemsPerSource: int = 20


class NewsItem(BaseModel):
    title: str
    source: str
    url: str = ""
    publishedAt: str = ""
    snippet: str = ""
    score: float = 0.0


class Report(BaseModel):
    generatedAt: str
    focus: str = ""
    timeWindow: dict[str, str]
    dataCoverage: dict[str, bool]
    marketSnapshot: dict[str, Any]
    sectorOutlook: list[SectorOutlook]
    crossSectorNotes: str = ""
    limitations: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
