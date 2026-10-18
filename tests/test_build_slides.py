"""scripts/build_slides.py: 9 slides, keyboard and click navigation, short slides, numbers from the result files."""

import html
import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_slides", ROOT / "scripts" / "build_slides.py")
bs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bs)


@pytest.fixture(scope="module")
def page():
    return bs.build()


def words(slide_html: str) -> int:
    text = re.sub(r"<svg.*?</svg>", " ", slide_html, flags=re.S)  # diagram labels are not slide text
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    return len(re.findall(r"[A-Za-z0-9$%/.,']+", text))


def test_nine_slides_in_order(page):
    titles = re.findall(r'<section class="slide[^"]*" id="s(\d)"[^>]*>(?:<h2>([^<]*)</h2>)?', page)
    assert [int(n) for n, _ in titles] == list(range(1, 10))
    assert [t for _, t in titles][1:] == ["The problem", "What&#x27;s new", "How the agents work", "Demo", "Cost to run",
                                          "Safety", "Impact: the numbers", "What&#x27;s next"]
    assert "<h1>Second Look</h1>" in page and bs.br.AUTHOR in page


@pytest.mark.parametrize("n", range(9))
def test_about_30_words_per_slide(n):
    assert words(bs.slides()[n]) <= 35


def test_navigation_and_16_by_9(page):
    assert "ArrowRight" in page and "ArrowLeft" in page and "addEventListener('click'" in page
    assert "width:1280px;height:720px" in page
    assert not re.search(r'<(?:link|script)[^>]+(?:href|src)="http', page)  # self-contained
    assert "<svg" in page and page.count("data:image/") == 3


def test_numbers_come_from_result_files(page):
    cost = json.loads((ROOT / "docs" / "cost_results.json").read_text(encoding="utf-8"))
    red = json.loads((ROOT / "docs" / "redteam_results.json").read_text(encoding="utf-8"))
    assert f"${cost['per_analysis']['mean_usd']:,.4f}" in page
    assert f"${cost['monthly']['without_cache']['users'][-1]['total_usd']:,.2f}" in page
    o = red["summary"]["overall"]
    assert f"{o['passed']}/{o['total']}" in page
    for v in red["summary"]["by_suite"].values():
        assert f"<b>{v['passed']}/{v['total']}</b>" in page


def test_impact_slide_uses_labelled_numbers_and_no_user_todo(page):
    impact = bs.slides()[7]
    cost = json.loads((ROOT / "docs" / "cost_results.json").read_text(encoding="utf-8"))
    assert f"${cost['per_analysis']['mean_usd']:,.4f}" in impact and "measured" in impact
    assert impact.count('class="est"') == 2 and "No user study yet" in impact
    assert "TODO" not in page and "user_test_results" not in page
