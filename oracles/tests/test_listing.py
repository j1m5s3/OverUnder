from listing.questions import question_id, winner_question
from listing.run import run, week_complete

ANVIL_0 = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

W3_BILLS = {
    "away": "Bills",
    "home": "Dolphins",
    "kickoff_unix": 1_800_000_000,
    "week": 3,
    "season": 2026,
    "status": "final",
}
W4_CHIEFS = {
    "away": "Chiefs",
    "home": "Broncos",
    "kickoff_unix": 1_800_864_000,
    "week": 4,
    "season": 2026,
    "status": "scheduled",
}


def test_week_complete_requires_all_final():
    q = winner_question("Dolphins", "Bills")
    assert week_complete([W3_BILLS], {q: "final"}) is True
    assert week_complete([W3_BILLS], {q: "in_progress"}) is False
    unlisted = dict(W3_BILLS, status="scheduled")
    assert week_complete([unlisted], {}) is False
    assert week_complete([dict(W3_BILLS, status="final")], {}) is True


def test_listing_week_not_final_no_posts(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    posts = []
    payloads = {
        "http://api.test/api/v1/markets": [],
        "http://api.test/api/v1/markets/schedule": [dict(W3_BILLS, status="scheduled"), W4_CHIEFS],
    }

    def http_get(url: str):
        return payloads[url]

    def http_post(url, body, headers=None):
        posts.append((url, body))
        return {}

    summary = run(http_get=http_get, http_post=http_post)
    assert posts == []
    assert summary["created"] == []


def test_listing_unlisted_week_game_scheduled_blocks(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    posts = []
    payloads = {
        "http://api.test/api/v1/markets": [],
        "http://api.test/api/v1/markets/schedule": [
            dict(W3_BILLS, status="final"),
            {
                "away": "Jets",
                "home": "Patriots",
                "kickoff_unix": 1_800_000_100,
                "week": 3,
                "season": 2026,
                "status": "scheduled",
            },
            W4_CHIEFS,
        ],
    }

    def http_get(url: str):
        return payloads[url]

    def http_post(url, body, headers=None):
        posts.append(url)
        return {}

    summary = run(http_get=http_get, http_post=http_post)
    assert posts == []
    assert summary["created"] == []


def test_listing_creates_next_week(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("OU_SEED_USDC", "200000000")
    posts = []
    payloads = {
        "http://api.test/api/v1/markets": [],
        "http://api.test/api/v1/markets/schedule": [W3_BILLS, W4_CHIEFS],
    }

    def http_get(url: str):
        return payloads[url]

    def http_post(url, body, headers=None):
        posts.append((url, body))
        return {"conditionId": "0x" + "ee" * 32, "question": body.get("question")}

    summary = run(http_get=http_get, http_post=http_post)
    market_posts = [body for url, body in posts if url.endswith("/markets")]
    assert len(market_posts) == 1
    assert market_posts[0]["close_time"] == W4_CHIEFS["kickoff_unix"]
    assert market_posts[0]["question"] == winner_question("Broncos", "Chiefs")
    assert market_posts[0]["question_id"] == question_id(
        W4_CHIEFS["season"], W4_CHIEFS["week"], W4_CHIEFS["away"], W4_CHIEFS["home"], W4_CHIEFS["kickoff_unix"]
    )
    assert market_posts[0]["seed_usdc"] == 200000000
    assert summary["created"][0]["close_time"] == W4_CHIEFS["kickoff_unix"]


def test_listing_skips_existing_question(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    existing_q = winner_question("Broncos", "Chiefs")
    cid = "0x" + "aa" * 32
    posts = []
    payloads = {
        "http://api.test/api/v1/markets": [
            {"primary": {"conditionId": cid, "question": existing_q, "marketType": 0}, "children": []},
        ],
        f"http://api.test/api/v1/markets/{cid}": {"score": {"status": "final"}, "question": existing_q},
        "http://api.test/api/v1/markets/schedule": [W3_BILLS, W4_CHIEFS],
    }

    def http_get(url: str):
        return payloads[url]

    def http_post(url, body, headers=None):
        posts.append((url, body))
        return {}

    summary = run(http_get=http_get, http_post=http_post)
    assert posts == []
    assert summary["created"] == []
    assert existing_q in summary["skipped"]
