"""Request and response models for reports."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ReportCatalogueEntry(BaseModel):
    code: str
    title: str
    description: str
    permission: str
    parameters: list[str] = Field(
        description=(
            "The filters this report accepts. Sent so the interface builds its "
            "form from the backend rather than keeping a second copy."
        )
    )
    scope: str = Field(
        description=(
            "'all' when the caller's permissions cover every employee, 'team' "
            "when the report will be narrowed to the people they can already see."
        )
    )


class ReportColumn(BaseModel):
    key: str
    label: str
    kind: str = Field(description="text, number, money, days, date or boolean - for formatting.")


class ReportResponse(BaseModel):
    code: str
    title: str
    description: str
    columns: list[ReportColumn]
    rows: list[dict[str, Any]]
    filters: dict[str, Any]
    totals: dict[str, str] = Field(
        description="Totals for the columns where a total means something."
    )
    row_count: int
    truncated: bool = Field(
        description="True when the row cap was hit, so the filters need narrowing."
    )
