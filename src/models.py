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
    volume: float | None = None
    turnover: float | None = None
    volumeVsAvg: float | None = None
    asOf: str = ""
    series: list[dict[str, Any]] = Field(default_factory=list)


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
    etfs: list[str] = Field(default_factory=list)
    benchmarks: list[str] = Field(default_factory=list)
    lookbackHours: int = 36
    maxItemsPerSource: int = 20
    focusKind: Literal["stock", "theme", "tape"] = "theme"


class NewsItem(BaseModel):
    title: str
    source: str
    url: str = ""
    publishedAt: str = ""
    snippet: str = ""
    score: float = 0.0
    sourceClass: str = "other"
    sourceWeight: float = 1.0
    highlight: bool = False


class HotSearchItem(BaseModel):
    rank: int = 0
    word: str
    category: str = ""
    heat: int | None = None
    label: str = ""
    url: str = ""
    onboardAt: str = ""
    fetchedAt: str = ""
    match: Literal["finance", "market", "focus", "llm"] = "finance"


class ThemeCluster(BaseModel):
    name: str
    summary: str = ""
    newsTitles: list[str] = Field(default_factory=list)
    hotWords: list[str] = Field(default_factory=list)
    heat: float = 0.0


class Report(BaseModel):
    generatedAt: str
    focus: str = ""
    timeWindow: dict[str, str]
    dataCoverage: dict[str, bool]
    marketSnapshot: dict[str, Any]
    marketPulse: dict[str, Any] = Field(default_factory=dict)
    hotSearch: list[HotSearchItem] = Field(default_factory=list)
    aggregates: list[ThemeCluster] = Field(default_factory=list)
    sectorOutlook: list[SectorOutlook]
    crossSectorNotes: str = ""
    trendNotes: str = ""
    limitations: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
