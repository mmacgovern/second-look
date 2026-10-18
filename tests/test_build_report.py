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
    assert "TODO: GitHub repository link" in page or br.REPO_URL


def test_no_user_test_todos_and_study_is_first_next_step(page):
    lowered = page.lower()
    assert "user_test_results" not in page and "todo: user" not in lowered and "pilot results" not in lowered
    assert "A user study was not run" in page
    next_steps = page[page.index("<h3>With another month</h3>"):]
    first = next_steps[next_steps.index("<li>"):next_steps.index("</li>")]
    assert "Run a user study (5-6 people, timed against a spreadsheet, with a comprehension check)" in first


def test_impact_uses_measured_and_persona_numbers(page):
    cost = json.loads((ROOT / "docs" / "cost_results.json").read_text(encoding="utf-8"))
    impact = page[page.index("<h2>7. Impact</h2>"):page.index("<h2>8. Limits")]
    for u in cost["monthly"]["without_cache"]["users"]:
        assert f"${u['total_usd']:,.2f}" in impact
    assert impact.count('class="est"') >= 5  # assumption-based numbers are labelled
    assert "Starting out" in impact and "Mid-career" in impact and "Retired" in impact


def test_required_pieces(page):
    assert "Safety policy." in page and "Morningstar Portfolio X-Ray" in page
    assert "AI_Prompts.md" in page and "<th>Phase</th>" in page
    assert "never" in page and "recommends a specific stock, fund or other security" in page  # the policy is stated
    assert "generative AI and is not a professional financial advisor" in page


def test_prompt_log_parses_phases():
    log = br.prompt_log()
    assert log and log[0]["n"] == 1 and all(e["phase"] for e in log)


def test_big_model_comparison_reported_from_quality_file(page):
    q = json.loads((ROOT / "docs" / "quality_vs_cost.json").read_text(encoding="utf-8"))["routings"]
    if "MODEL_SMART for every agent" in q:
        smart = q["MODEL_SMART for every agent"]
        assert f"{100 * smart['first_draft_pass_rate']:.0f}% of its first drafts passed" in page
        assert "TODO: MODEL_SMART" not in page


def test_report_traces_each_agent_and_a_verifier_rejection(page):
    # the page is built from the real recordings (the per-test isolation points demo mode at an empty folder)
    import html

    trace = html.unescape(page[page.index("What each agent returned in this run"):page.index("<h2>5. Tokenomics</h2>")])
    for agent in ("analyst", "explainer", "verifier"):
        assert f"<strong>{agent}</strong>" in trace
    assert '"findings"' in trace and '"passed"' in trace
    assert "When the verifier says no" in trace and "explainer_rewrite" in trace


def test_no_tools_choice_is_explained(page):
    assert "Why the agents have no tools (a deliberate choice)." in page
    red = json.loads((ROOT / "docs" / "redteam_results.json").read_text(encoding="utf-8"))
    wrong = red["summary"]["by_suite"]["wrong-number drafts / verifier"]
    assert f"caught {wrong['passed']} of {wrong['total']} red-team drafts" in page
