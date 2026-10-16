"""Shared pagination, sorting and filtering primitives for list endpoints."""

from __future__ import annotations

from typing import Annotated, Any, Generic, Literal, TypeVar

from fastapi import Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

T = TypeVar("T")

MAX_PAGE_SIZE = 200


class PageParams(BaseModel):
    """Query parameters common to every list endpoint."""

    page: Annotated[int, Query(ge=1, description="1-based page number")] = 1
    page_size: Annotated[
        int,
        Query(ge=1, le=MAX_PAGE_SIZE, description=f"Rows per page (max {MAX_PAGE_SIZE})"),
    ] = 25
    sort_by: Annotated[str | None, Query(description="Field name to sort by")] = None
    sort_dir: Annotated[Literal["asc", "desc"], Query(description="Sort direction")] = "asc"

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PageMeta(BaseModel):
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_previous: bool


class Page(BaseModel, Generic[T]):
    """Envelope returned by every list endpoint."""

    items: list[T]
    meta: PageMeta

    @classmethod
    def build(cls, items: list[T], *, total: int, params: PageParams) -> Page[T]:
        total_pages = max(1, -(-total // params.page_size))  # ceil division
        return cls(
            items=items,
            meta=PageMeta(
                page=params.page,
                page_size=params.page_size,
                total_items=total,
                total_pages=total_pages,
                has_next=params.page < total_pages,
                has_previous=params.page > 1,
            ),
        )


def count_query(session: Session, statement: Select[Any]) -> int:
    """Total rows the statement would return, ignoring limit/offset/order."""
    subquery = statement.order_by(None).options().subquery()
    return session.scalar(select(func.count()).select_from(subquery)) or 0


def apply_sort(
    statement: Select[Any],
    params: PageParams,
    *,
    sortable: dict[str, Any],
    default: Any,
) -> Select[Any]:
    """Apply ordering using an allow-list of sortable columns.

    The allow-list matters: interpolating a client-supplied column name into an
    ORDER BY clause would be an injection point, and it would also let clients
    sort by columns they are not permitted to read.
    """
    column = sortable.get(params.sort_by) if params.sort_by else None
    if column is None:
        column = default
    return statement.order_by(column.desc() if params.sort_dir == "desc" else column.asc())


def paginate(
    session: Session,
    statement: Select[Any],
    params: PageParams,
    *,
    sortable: dict[str, Any] | None = None,
    default_sort: Any = None,
) -> tuple[list[Any], int]:
    """Return ``(rows, total)`` for a statement under the given page parameters."""
    total = count_query(session, statement)
    if sortable is not None and default_sort is not None:
        statement = apply_sort(statement, params, sortable=sortable, default=default_sort)
    rows = list(
        session.scalars(statement.limit(params.page_size).offset(params.offset)).unique().all()
    )
    return rows, total


class SearchParams(BaseModel):
    """Free-text search shared by directory-style endpoints."""

    q: Annotated[str | None, Query(max_length=120, description="Free-text search")] = None


class AuditStamp(BaseModel):
    """Read-only creation/modification metadata exposed on detail responses."""

    created_at: Any = Field(description="UTC timestamp the record was created")
    updated_at: Any = Field(description="UTC timestamp the record was last modified")
