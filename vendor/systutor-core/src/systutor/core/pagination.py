from __future__ import annotations

from pydantic import BaseModel


class OffsetPageRead[ItemT](BaseModel):
    items: list[ItemT]
    total: int
    limit: int
    offset: int


class NumberedPaginationRead(BaseModel):
    page: int
    per_page: int
    total: int
    total_pages: int


class NumberedPageRead[ItemT](BaseModel):
    items: list[ItemT]
    pagination: NumberedPaginationRead
