"""The user's situation and risk profile.

Risk profile = the lower of
  - capacity: how much time there is before the money is needed, and
  - tolerance: a 5-question quiz, each answer scored 1-5, averaged and rounded.
Taking the lower of the two is deliberately cautious: high tolerance does not
add time, and lots of time does not make big drops easier to sit through.

The reference glide path (stock share by years to retirement) is loaded from
data/reference/glide_path.json and is only used once it has real, cited values.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

from secondlook.config import PROJECT_ROOT

GLIDE_PATH_FILE = PROJECT_ROOT / "data" / "reference" / "glide_path.json"

RISK_PROFILES = {
    1: "Conservative",
    2: "Moderately conservative",
    3: "Moderate",
    4: "Moderately aggressive",
    5: "Aggressive",
}

# (upper bound of years to retirement, capacity score)
CAPACITY_BANDS = [(2, 1), (7, 2), (14, 3), (24, 4)]  # 25+ years -> 5

QUIZ = [
    {
        "id": "drop_reaction",
        "question": "If your investments fell 25% over a few months, what would you most likely do?",
        "options": ["Sell everything", "Sell some", "Do nothing and worry", "Do nothing", "Invest more"],
    },
    {
        "id": "goal",
        "question": "Which matters more to you?",
        "options": [
            "Never losing money",
            "Mostly avoiding losses",
            "A balance of growth and safety",
            "Mostly growth",
            "Maximum growth, even with big swings",
        ],
    },
    {
        "id": "max_loss",
        "question": "What is the largest one-year drop you could live with?",
        "options": ["5%", "10%", "20%", "30%", "40% or more"],
    },
    {
        "id": "experience",
        "question": "How much investing experience do you have?",
        "options": ["None", "A little", "Some", "A lot", "Professional-level"],
    },
    {
        "id": "safety_net",
        "question": "How many months of expenses could you cover from savings outside these investments?",
        "options": ["Less than 1", "1-3", "3-6", "6-12", "More than 12"],
    },
]


class UserProfile(BaseModel):
    age: int = Field(ge=18, le=100)
    retirement_age: int = Field(ge=40, le=100)
    desired_income_today_usd: float = Field(ge=0)
    social_security_or_pension_usd: float | None = Field(default=None, ge=0)
    annual_contribution_usd: float = Field(default=0.0, ge=0)
    employer_ticker: str | None = None  # the user's employer stock, if they mark one

    @field_validator("employer_ticker", mode="before")
    @classmethod
    def _employer_ticker(cls, value):
        from secondlook.quant.models import TICKER_RE

        if value is None or not str(value).strip():
            return None
        ticker = str(value).strip().upper()
        if not TICKER_RE.fullmatch(ticker):
            raise ValueError("employer ticker must be 1-6 letters")
        return ticker

    @property
    def years_to_retirement(self) -> int:
        return max(0, self.retirement_age - self.age)

    @property
    def is_retired(self) -> bool:
        return self.age >= self.retirement_age

    @property
    def guaranteed_income_usd(self) -> float:
        return self.social_security_or_pension_usd or 0.0


class RiskProfile(BaseModel):
    capacity: int
    tolerance: int
    level: int
    label: str


class GlidePoint(BaseModel):
    years_to_retirement: int
    stock_share: float | None = Field(default=None, ge=0, le=1)


class GlidePath(BaseModel):
    is_placeholder: bool = True
    source: str
    source_url: str | None = None
    as_of: str | None = None
    points: list[GlidePoint]

    @model_validator(mode="after")
    def _sorted(self) -> "GlidePath":
        self.points.sort(key=lambda p: p.years_to_retirement)
        return self

    @property
    def ready(self) -> bool:
        return not self.is_placeholder and bool(self.points) and all(p.stock_share is not None for p in self.points)

    def stock_share_for(self, years_to_retirement: float) -> float | None:
        """Reference stock share, linearly interpolated and clamped at the ends. None until filled in."""
        if not self.ready:
            return None
        pts = self.points
        if years_to_retirement <= pts[0].years_to_retirement:
            return pts[0].stock_share
        if years_to_retirement >= pts[-1].years_to_retirement:
            return pts[-1].stock_share
        for lo, hi in zip(pts, pts[1:]):
            if lo.years_to_retirement <= years_to_retirement <= hi.years_to_retirement:
                span = hi.years_to_retirement - lo.years_to_retirement
                t = (years_to_retirement - lo.years_to_retirement) / span
                return lo.stock_share + t * (hi.stock_share - lo.stock_share)
        return None


def capacity_score(years_to_retirement: int) -> int:
    for upper, score in CAPACITY_BANDS:
        if years_to_retirement <= upper:
            return score
    return 5


def score_quiz(answers: list[int]) -> int:
    """Each answer is 1-5 (the option number); tolerance is the mean, rounded half up."""
    if len(answers) != len(QUIZ):
        raise ValueError(f"Expected {len(QUIZ)} answers, got {len(answers)}")
    if any(not isinstance(a, int) or not 1 <= a <= 5 for a in answers):
        raise ValueError("Each answer must be a whole number from 1 to 5")
    return int(sum(answers) / len(answers) + 0.5)


def risk_profile(user: UserProfile, answers: list[int]) -> RiskProfile:
    capacity = capacity_score(user.years_to_retirement)
    tolerance = score_quiz(answers)
    level = min(capacity, tolerance)
    return RiskProfile(capacity=capacity, tolerance=tolerance, level=level, label=RISK_PROFILES[level])


def load_glide_path(path: Path = GLIDE_PATH_FILE) -> GlidePath:
    return GlidePath(**json.loads(Path(path).read_text(encoding="utf-8")))


PERSONAS_FILE = PROJECT_ROOT / "data" / "sample_portfolios" / "personas.json"


def load_persona(name: str, path: Path = PERSONAS_FILE):
    """A sample persona: (UserProfile, Household, risk quiz answers)."""
    from secondlook.quant.accounts import household_from_csvs

    persona = json.loads(Path(path).read_text(encoding="utf-8"))[name]
    types = persona["account_types"]
    household = household_from_csvs([(Path(path).parent / persona["file"], next(iter(types.values())), types)])
    return UserProfile(**persona["profile"]), household, persona["risk_quiz_answers"]
