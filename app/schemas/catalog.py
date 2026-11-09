"""Validated options shared by saved collections and export preflight."""
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CatalogSelection(BaseModel):
    family_codes: list[str] = Field(min_length=1, max_length=250)
    stock_mode: Literal["all", "available"] = "available"
    min_stock: int = Field(default=1, ge=1, le=1_000_000)
    show_stock: bool = False
    show_prices: bool = True
    output_mode: Literal["single", "split"] = "single"

    @field_validator("family_codes")
    @classmethod
    def normalize_codes(cls, values):
        codes = list(dict.fromkeys(" ".join(value.strip().upper().split()) for value in values))
        if any(not code or len(code) > 100 for code in codes):
            raise ValueError("Family codes must contain 1–100 characters.")
        return codes


class CollectionInput(CatalogSelection):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value):
        value = " ".join(value.split())
        if not value:
            raise ValueError("Enter a collection name.")
        return value
