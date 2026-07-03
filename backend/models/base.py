"""SQLAlchemy declarative base shared by all models."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Timezone-aware UTC now for column defaults.

    The service layer works exclusively with aware datetimes; naive
    ``datetime.utcnow`` (also deprecated since Python 3.12) would be
    reinterpreted in the session timezone by timestamptz columns.
    """
    return datetime.now(UTC)
