"""The sample CSVs are realistic, internally consistent, and use fake account numbers."""

import csv
import re
from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "sample_portfolios"
HEADER = ["Symbol", "Description", "Quantity", "Price", "Market Value", "Account Number"]


def money(text: str) -> float:
    return float(text.replace("$", "").replace(",", ""))


def rows(name: str) -> list[dict]:
    with (SAMPLES / f"{name}.csv").open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == HEADER
        return list(reader)


@pytest.mark.parametrize("name, target", [("beginner", 15_000), ("midcareer", 180_000), ("retiree", 650_000)])
def test_totals_near_target_and_rows_consistent(name, target):
    data = rows(name)
    total = sum(money(r["Market Value"]) for r in data)
    assert abs(total - target) / target < 0.03
    for r in data:
        assert money(r["Market Value"]) == pytest.approx(float(r["Quantity"]) * money(r["Price"]), abs=0.01)
        assert re.fullmatch(r"DEMO-0000-\d{4}", r["Account Number"])


def test_each_portfolio_shows_its_story():
    beginner = {r["Symbol"] for r in rows("beginner")}
    midcareer = {r["Symbol"] for r in rows("midcareer")}
    retiree = {r["Symbol"] for r in rows("retiree")}
    assert "VTTSX" in beginner  # target-date fund
    assert {"VOO", "VTI", "QQQ", "XLK"} <= midcareer and {"AAPL", "MSFT", "NVDA"} <= midcareer
    assert "GFACX" in retiree  # 1.35% expense ratio active fund
