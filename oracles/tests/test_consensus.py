from agents.alpha import AlphaAgent
from agents.base import MockSearch
from agents.beta import BetaAgent
from agents.gamma import GammaAgent
from consensus.coordinator import Coordinator
from consensus.fallback import combine, majority
from wildcard.generator import propose


def test_unanimous_mocked_search():
    search = MockSearch(["Chiefs defeated Broncos 27-24. Kelce fumbled once."])
    coord = Coordinator(
        agents=[
            AlphaAgent(search=search),
            BetaAgent(search=search),
            GammaAgent(search=search),
        ]
    )
    result = coord.run("Who won Chiefs vs Broncos?")
    assert result["unanimous"] is True
    assert result["outcome"] == 0


def test_fallback_majority_and_votes():
    assert majority([0, 0, 1]) == 0
    assert combine(0, yes_weight=10, no_weight=90) == "arbitrate"
    assert combine(0, yes_weight=80, no_weight=20) == "agree"


def test_wildcard_gates():
    kids = propose("Chiefs vs Broncos", close_time=1_700_000_000)
    assert kids
    assert all("?" in k.question or "fumble" in k.question.lower() for k in kids)
