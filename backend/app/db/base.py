"""Declarative base, naming conventions and shared column mixins."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated

from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Explicit constraint naming is required for Alembic to be able to emit
# ALTER/DROP statements for constraints and indexes it did not name itself.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"


# --------------------------------------------------------------- column types
# Named aliases keep precision decisions in one place. Money is NUMERIC, never
# float: payroll totals must be exact and reproducible.

PrimaryKey = Annotated[int, mapped_column(BigInteger, primary_key=True, autoincrement=True)]
Money = Annotated[Decimal, mapped_column(Numeric(14, 2))]
"""Currency amount. Two decimal places (paise)."""

Days = Annotated[Decimal, mapped_column(Numeric(6, 2))]
"""Leave / comp-off quantity in days. Supports half-days and finer accruals."""

Rate = Annotated[Decimal, mapped_column(Numeric(9, 6))]
"""A statutory or policy rate stored as a fraction (0.120000 == 12%)."""

TimestampTz = Annotated[datetime, mapped_column(DateTime(timezone=True))]


class TimestampMixin:
    """``created_at`` / ``updated_at``, maintained by the database."""

    @declared_attr
    @classmethod
    def created_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
        )

    @declared_attr
    @classmethod
    def updated_at(cls) -> Mapped[datetime]:
        return mapped_column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            onupdate=text("now()"),
        )


class ActorMixin:
    """Records which user created and last modified a row.

    This is a convenience for display and debugging. It is *not* a substitute for
    ``audit_logs``, which captures before/after values for sensitive operations
    and is never updated in place.
    """

    @declared_attr
    @classmethod
    def created_by_user_id(cls) -> Mapped[int | None]:
        return mapped_column(
            BigInteger,
            ForeignKey("users.id", ondelete="SET NULL", use_alter=True),
            nullable=True,
        )

    @declared_attr
    @classmethod
    def updated_by_user_id(cls) -> Mapped[int | None]:
        return mapped_column(
            BigInteger,
            ForeignKey("users.id", ondelete="SET NULL", use_alter=True),
            nullable=True,
        )


#: JSONB whose Python ``None`` means **SQL NULL**, not the JSON value ``null``.
#:
#: SQLAlchemy's default for a JSON column is ``none_as_null=False``, which
#: stores ``None`` as ``'null'::jsonb`` - a value that is *not* SQL NULL. Any
#: ``IS NULL`` test then reads false for a column nobody set, which silently
#: breaks a check constraint written as "exactly one of these is present" and a
#: query written as "the ones with flags". Every JSONB column in this project
#: uses this type so that an unset column reads as unset.
Jsonb = JSONB(none_as_null=True)
