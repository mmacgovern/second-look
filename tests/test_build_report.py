"""scripts/build_report.py: the report has the required sections and only numbers from the project's outputs."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_report", ROOT / "scripts" / "build_report.py")
br = importlib.util.module_from_spec(spec)
spec.loader.exec_module(br)


@pytest.fixture(scope="module")
def page():
    return br.build()


def test_sections_in_order(page):
    heads = re.findall(r"<h2>([^<]*)</h2>", page)
    assert heads == ["2. Motivation", "3. What is new here", "4. The agents", "5. Tokenomics", "6. Safety",
                     "7. Impact", "8. Limits and next steps", "Appendix: AI Prompt Log"]
    assert page.index("<h1>Second Look</h1>") < page.index("2. Motivation")


def test_self_contained_and_printable(page):
    assert "@media print" in page and "page-break-before" in page
    assert not re.search(r'<(?:link|script)[^>]+(?:href|src)="http', page)  # no external CSS or JS
    assert "<svg" in page and "data:image/" in page and "<video" in page


def test_numbers_come_from_cost_and_redteam_results(page):
    cost = json.loads((ROOT / "docs" / "cost_results.json").read_text(encoding="utf-8"))
    red = json.loads((ROOT / "docs" / "redteam_results.json").read_text(encoding="utf-8"))
    assert f"${cost['per_analysis']['mean_usd']:,.6f}" in page
    assert f"${cost['monthly']['without_cache']['users'][-1]['total_usd']:,.2f}" in page
    o = red["summary"]["overall"]
    assert f"{o['passed']}/{o['total']}" in page


def test_missing_inputs_are_todo_not_numbers(page):
    if not (ROOT / "docs" / "user_test_results.md").exists():
        assert "TODO: docs/user_test_results.md does not exist yet" in page
    assert "TODO: GitHub repository link" in page or br.REPO_URL


def test_required_pieces(page):
    assert "Safety policy." in page and "Morningstar Portfolio X-Ray" in page
    assert "AI_Prompts.md" in page and "<th>Phase</th>" in page
    assert "never tells" in page  # the no-advice policy is stated


def test_prompt_log_parses_phases():
    log = br.prompt_log()
    assert log and log[0]["n"] == 1 and all(e["phase"] for e in log)
