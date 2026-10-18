import json

import pytest
from pydantic import ValidationError

from secondlook.quant.profile import (
    QUIZ, RISK_PROFILES, GlidePath, UserProfile, capacity_score, load_glide_path, load_persona,
    risk_profile, score_quiz,
)


def user(age=40, retirement_age=65, **kw):
    return UserProfile(age=age, retirement_age=retirement_age, desired_income_today_usd=kw.pop("income", 60000), **kw)


def test_user_profile_years_and_status():
    assert user(40, 65).years_to_retirement == 25 and not user(40, 65).is_retired
    assert user(65, 65).years_to_retirement == 0 and user(65, 65).is_retired
    assert user(70, 65).years_to_retirement == 0
    assert user().guaranteed_income_usd == 0
    assert user(social_security_or_pension_usd=24000).guaranteed_income_usd == 24000
    with pytest.raises(ValidationError):
        user(age=10)
    with pytest.raises(ValidationError):
        UserProfile(age=40, retirement_age=65, desired_income_today_usd=-1)


@pytest.mark.parametrize("years, score", [(0, 1), (2, 1), (3, 2), (7, 2), (8, 3), (14, 3), (15, 4), (24, 4), (25, 5), (45, 5)])
def test_capacity_bands(years, score):
    assert capacity_score(years) == score


@pytest.mark.parametrize("answers, tolerance", [
    ([4, 4, 3, 2, 2], 3),   # 15 / 5 = 3.0
    ([4, 4, 4, 3, 3], 4),   # 18 / 5 = 3.6 -> 4
    ([2, 2, 2, 3, 4], 3),   # 13 / 5 = 2.6 -> 3
    ([1, 2, 1, 2, 1], 1),   # 7 / 5 = 1.4 -> 1
    ([5, 5, 5, 5, 5], 5),
])
def test_quiz_scoring(answers, tolerance):
    assert score_quiz(answers) == tolerance


@pytest.mark.parametrize("bad", [[1, 2, 3, 4], [1, 2, 3, 4, 6], [0, 1, 1, 1, 1], [1, 1, 1, 1, 1.5]])
def test_quiz_rejects_bad_answers(bad):
    with pytest.raises(ValueError):
        score_quiz(bad)


def test_quiz_has_five_questions_with_five_options():
    assert len(QUIZ) == 5 and all(len(q["options"]) == 5 for q in QUIZ)


def test_risk_profile_is_lower_of_capacity_and_tolerance():
    # 39 years -> capacity 5, tolerance 3 -> Moderate
    p = risk_profile(user(26, 65), [4, 4, 3, 2, 2])
    assert (p.capacity, p.tolerance, p.level, p.label) == (5, 3, 3, "Moderate")
    # retired -> capacity 1, even with tolerance 5
    p = risk_profile(user(67, 65), [5, 5, 5, 5, 5])
    assert (p.level, p.label) == (1, "Conservative")
    assert set(RISK_PROFILES) == {1, 2, 3, 4, 5}


TOY_GLIDE = GlidePath(is_placeholder=False, source="Toy family, 2026 prospectus", points=[
    {"years_to_retirement": 20, "stock_share": 0.9},
    {"years_to_retirement": 0, "stock_share": 0.5},
    {"years_to_retirement": 10, "stock_share": 0.7},
])


@pytest.mark.parametrize("years, share", [(0, 0.5), (5, 0.6), (10, 0.7), (15, 0.8), (20, 0.9), (30, 0.9), (-5, 0.5)])
def test_glide_path_interpolates_and_clamps(years, share):
    assert TOY_GLIDE.stock_share_for(years) == pytest.approx(share)


def test_placeholder_glide_path_is_not_used():
    real_file = load_glide_path()
    assert real_file.is_placeholder and not real_file.ready
    assert "PLACEHOLDER" in real_file.source
    assert real_file.stock_share_for(20) is None
    # filled-in values but still flagged as placeholder -> still not used
    flagged = GlidePath(is_placeholder=True, source="x", points=[{"years_to_retirement": 0, "stock_share": 0.5}])
    assert flagged.stock_share_for(0) is None


def test_glide_path_file_round_trip(tmp_path):
    path = tmp_path / "glide.json"
    path.write_text(json.dumps(TOY_GLIDE.model_dump()), encoding="utf-8")
    assert load_glide_path(path).stock_share_for(5) == pytest.approx(0.6)


@pytest.mark.parametrize("name, age, years", [("beginner", 26, 39), ("midcareer", 42, 23), ("retiree", 67, 0)])
def test_personas_load(name, age, years):
    profile, household, answers = load_persona(name)
    assert profile.age == age and profile.years_to_retirement == years
    assert profile.social_security_or_pension_usd > 0 and profile.desired_income_today_usd > 0
    assert len(answers) == 5 and household.total_value > 0


def test_employer_ticker_validated():
    assert user(employer_ticker=" msft ").employer_ticker == "MSFT"
    assert user(employer_ticker="").employer_ticker is None and user().employer_ticker is None
    with pytest.raises(ValidationError):
        user(employer_ticker="NOT A TICKER")
