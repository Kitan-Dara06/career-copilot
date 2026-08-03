"""Structured CRUD tool — generic read/write/delete over SQLAlchemy models."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel

# Pre-load all models so Base.registry knows about every table
import backbone.db.models  # noqa: F401 — triggers registry population
from backbone.db.base import Base
from backbone.db.session import async_session_factory
from backbone.tools.base import CostHint, LatencyHint, Tool, ToolContext

# ── Data models ──


class GetInput(BaseModel):
    table: str
    key: str


class GetOutput(BaseModel):
    data: dict[str, Any] | None


class SetInput(BaseModel):
    table: str
    key: str
    value: dict[str, Any]


class SetOutput(BaseModel):
    success: bool


class DeleteInput(BaseModel):
    table: str
    key: str


class DeleteOutput(BaseModel):
    success: bool


# ── Helpers ──


def _get_model(table_name: str) -> type[Base]:
    for mapper in Base.registry.mappers:
        if mapper.class_.__tablename__ == table_name:
            return mapper.class_
    raise ValueError(f"Unknown table: {table_name!r}")


def _pk_column(model: type[Base]) -> str:
    pk = next((c for c in model.__table__.columns if c.primary_key), None)
    if pk is None:
        raise ValueError(f"No pk in {model.__tablename__}")
    return str(pk.name)


# ── Tools ──


class GetTool(Tool[GetInput, GetOutput]):
    name = "structured.get"
    description = (
        "Read a row from a database table by its primary key,"
        " or list all rows with key='__all__'."
    )
    input_schema = GetInput
    output_schema = GetOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: GetInput) -> GetOutput:
        model = _get_model(input.table)
        pk = _pk_column(model)
        columns = list(model.__table__.columns)

        # Handle __all__ — fetch all rows
        if input.key == "__all__":
            async with async_session_factory()() as session:
                result = await session.execute(sa.select(model))
                rows = []
                for r in result.all():
                    instance = r[0]
                    rows.append(
                        {c.name: getattr(instance, c.name, None) for c in columns}
                    )
                return GetOutput(
                    data={"rows": rows, "count": len(rows)}
                )

        # Try int PK, fallback to text PK
        pk_col = model.__table__.c[pk]
        try:
            key_val = int(input.key)
        except ValueError:
            key_val = input.key

        stmt = sa.select(model).where(pk_col == key_val)

        async with async_session_factory()() as session:
            result = await session.execute(stmt)
            row = result.one_or_none()
            if row is None:
                return GetOutput(data=None)
            instance = row[0]
            data = {c.name: getattr(instance, c.name, None) for c in columns}
            return GetOutput(data=data)


class SetTool(Tool[SetInput, SetOutput]):
    name = "structured.set"
    description = "Insert or update a row by primary key."
    input_schema = SetInput
    output_schema = SetOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: SetInput) -> SetOutput:
        model = _get_model(input.table)
        pk = _pk_column(model)
        table = model.__table__
        pk_col = table.c[pk]

        # Try int PK, fallback to text PK
        try:
            key_val = int(input.key)
        except ValueError:
            key_val = input.key

        async with async_session_factory()() as session:
            stmt = sa.update(model).where(pk_col == key_val).values(**input.value)
            result = await session.execute(stmt)
            if result.rowcount == 0:  # type: ignore[attr-defined]
                stmt_insert = sa.insert(model).values(**{pk: key_val}, **input.value)
                await session.execute(stmt_insert)
            await session.commit()

        return SetOutput(success=True)


class DeleteTool(Tool[DeleteInput, DeleteOutput]):
    name = "structured.delete"
    description = "Delete a row from a table by primary key."
    input_schema = DeleteInput
    output_schema = DeleteOutput
    cost_hint = CostHint.FREE
    latency_hint = LatencyHint.FAST
    owner = "paper_tracker"

    async def __call__(self, ctx: ToolContext, input: DeleteInput) -> DeleteOutput:
        model = _get_model(input.table)
        pk = _pk_column(model)
        pk_col = model.__table__.c[pk]

        # Try int PK, fallback to text PK
        try:
            key_val = int(input.key)
        except ValueError:
            key_val = input.key

        stmt = sa.delete(model).where(pk_col == key_val)

        async with async_session_factory()() as session:
            await session.execute(stmt)
            await session.commit()

        return DeleteOutput(success=True)


from backbone.tools.registry import register

register(GetTool(), agent="paper_tracker")
register(SetTool(), agent="paper_tracker")
register(DeleteTool(), agent="paper_tracker")
