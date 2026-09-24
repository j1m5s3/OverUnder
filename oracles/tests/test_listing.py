import pytest

from listing.questions import question_id, winner_question
from listing.run import DEFAULT_STALE_GRACE_SECONDS, run, stale_grace_seconds, week_complete

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


NOW = 1_790_000_000
W3_JETS = {
    "away": "Jets",
    "home": "Patriots",
    "kickoff_unix": 1_800_000_100,
    "week": 3,
    "season": 2026,
    "status": "scheduled",
}
W4_BILLS = {
    "away": "Bills",
    "home": "Jets",
    "kickoff_unix": 1_800_870_000,
    "week": 4,
    "season": 2026,
    "status": "scheduled",
}


BILLS_CID = "0x" + "b1" * 32


@pytest.fixture(autouse=True)
def _listing_env(monkeypatch):
    monkeypatch.delenv("OU_LISTING_STALE_GRACE_SECONDS", raising=False)
    monkeypatch.delenv("OU_QUESTION_ID_KEY", raising=False)
    # Link checks read the chain only when RPC + oracle config is present.
    for name in ("ANVIL_RPC_URL", "OU_RPC_URL", "ORACLE_ADDRESS"):
        monkeypatch.delenv(name, raising=False)


def _operator_env(monkeypatch):
    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("JWT_SECRET", "test-secret-please-use-32b-min!!")
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", ANVIL_0)
    monkeypatch.setenv("OU_SEED_USDC", "200000000")


def _fake_api(schedule, cards=None, details=None, fail_questions=(), conflict_questions=()):
    posts = []
    payloads = {
        "http://api.test/api/v1/markets": cards or [],
        "http://api.test/api/v1/markets/schedule": schedule,
    }
    payloads.update(details or {})

    def http_get(url: str):
        value = payloads[url]
        if isinstance(value, Exception):
            raise value
        return value

    def http_post(url, body, headers=None):
        posts.append((url, body))
        if url.endswith("/markets") and body.get("question") in fail_questions:
            raise RuntimeError("factory reverted: close in past")
        if url.endswith("/markets") and body.get("question") in conflict_questions:
            import httpx

            request = httpx.Request("POST", url)
            response = httpx.Response(409, json={"detail": "condition prepared outside this factory"}, request=request)
            raise httpx.HTTPStatusError("409 Conflict", request=request, response=response)
        return {"conditionId": "0x" + "ee" * 32, "question": body.get("question") if isinstance(body, dict) else None}

    return http_get, http_post, posts


def _market_posts(posts):
    return [body for url, body in posts if url.endswith("/markets")]


def test_week_complete_requires_all_final():
    listed = dict(W3_BILLS, listedConditionId=BILLS_CID)
    assert week_complete([listed], {BILLS_CID: "final"}) is True
    assert week_complete([listed], {BILLS_CID: "in_progress"}) is False
    unlisted = dict(W3_BILLS, status="scheduled")
    assert week_complete([unlisted], {}) is False
    assert week_complete([dict(W3_BILLS, status="final")], {}) is True
    # A live status only counts for the row linked to that market.
    assert week_complete([unlisted], {BILLS_CID: "final"}) is False


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

    summary = run(http_get=http_get, http_post=http_post, now=NOW)
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

    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert posts == []
    assert summary["created"] == []
    assert summary["blocked"] == [
        {
            "season": 2026,
            "week": 3,
            "blockedBy": [{"id": None, "away": "Jets", "home": "Patriots", "status": "scheduled", "kickoff_unix": 1_800_000_100}],
        }
    ]


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

    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    market_posts = [body for url, body in posts if url.endswith("/markets")]
    assert len(market_posts) == 1
    assert summary["ok"] is True
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
            {
                "primary": {"conditionId": cid, "question": existing_q, "marketType": 0, "closeTime": W4_CHIEFS["kickoff_unix"]},
                "children": [],
            },
        ],
        f"http://api.test/api/v1/markets/{cid}": {"score": {"status": "scheduled"}, "question": existing_q},
        "http://api.test/api/v1/markets/schedule": [W3_BILLS, W4_CHIEFS],
    }

    def http_get(url: str):
        return payloads[url]

    def http_post(url, body, headers=None):
        posts.append((url, body))
        return {}

    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert posts == []
    assert summary["created"] == []
    assert existing_q in summary["skipped"]


def test_week_complete_postponed_cancelled_done():
    bills_q = BILLS_CID
    W3_BILLS_L = dict(W3_BILLS, listedConditionId=BILLS_CID)
    games = [
        dict(W3_BILLS, status="postponed"),
        dict(W3_JETS, status="cancelled"),
        dict(W3_BILLS, away="Bears", home="Packers", status="final"),
    ]
    assert week_complete(games, {}) is True
    assert week_complete([dict(W3_BILLS_L, status="postponed")], {bills_q: None}) is True
    assert week_complete([dict(W3_BILLS_L, status="scheduled")], {bills_q: "cancelled"}) is True
    assert week_complete([dict(W3_BILLS_L, status="final")], {bills_q: "in_progress"}) is False
    assert week_complete([dict(W3_BILLS, status="scheduled")], {}) is False
    assert week_complete([], {}) is False


def test_listing_skips_kickoff_within_lead(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS])
    summary = run(http_get=http_get, http_post=http_post, now=W4_CHIEFS["kickoff_unix"] - 300)
    assert _market_posts(posts) == []
    assert summary["tooLate"] == [winner_question("Broncos", "Chiefs")]
    assert summary["ok"] is True

    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS])
    summary = run(http_get=http_get, http_post=http_post, now=W4_CHIEFS["kickoff_unix"] - 601)
    assert len(_market_posts(posts)) == 1
    assert summary["tooLate"] == []


def test_listing_one_game_failure_does_not_block_others(monkeypatch, capsys):
    _operator_env(monkeypatch)
    chiefs_q = winner_question("Broncos", "Chiefs")
    bills_q = winner_question("Jets", "Bills")
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS, W4_BILLS], fail_questions={chiefs_q})
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert [body["question"] for body in _market_posts(posts)] == [chiefs_q, bills_q]
    assert [row["question"] for row in summary["created"]] == [bills_q]
    assert len(summary["errors"]) == 1
    assert summary["errors"][0]["question"] == chiefs_q
    assert "close in past" in summary["errors"][0]["error"]
    assert summary["ok"] is False
    schedule_posts = [body for url, body in posts if url.endswith("/schedule")]
    assert [rows[0]["away"] for rows in schedule_posts] == ["Bills"]
    assert "listing create failed" in capsys.readouterr().err


def test_listing_postponed_week_unblocks_next(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W3_BILLS, dict(W3_JETS, status="postponed"), W4_CHIEFS])
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert [body["question"] for body in _market_posts(posts)] == [winner_question("Broncos", "Chiefs")]
    assert summary["ok"] is True


def test_listing_skips_non_scheduled_next_week_game(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W3_BILLS, dict(W4_CHIEFS, status="cancelled")])
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert posts == []
    assert summary["inactive"] == [winner_question("Broncos", "Chiefs")]
    assert summary["created"] == []


def test_listing_detail_error_treated_as_no_live_status(monkeypatch):
    _operator_env(monkeypatch)
    bills_q = winner_question("Dolphins", "Bills")
    cid = "0x" + "bb" * 32
    cards = [{"primary": {"conditionId": cid, "question": bills_q, "marketType": 0, "closeTime": W3_BILLS["kickoff_unix"]}, "children": []}]
    details = {f"http://api.test/api/v1/markets/{cid}": RuntimeError("502 bad gateway")}
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS], cards=cards, details=details)
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    # Bills is listed but its detail failed, so the schedule row's "final" decides.
    assert [body["question"] for body in _market_posts(posts)] == [winner_question("Broncos", "Chiefs")]
    assert summary["skipped"] == []


def test_listing_missing_jwt_secret_fails_stage(monkeypatch):
    _operator_env(monkeypatch)
    monkeypatch.delenv("JWT_SECRET")
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS])
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        run(http_get=http_get, http_post=http_post, now=NOW)
    assert posts == []



# --- dedupe by schedule row, not question text ---------------------------------


def _resolved_2026_card(cid="0x" + "a2" * 32):
    q = winner_question("Bills", "Dolphins")
    return cid, [{"primary": {"conditionId": cid, "question": q, "marketType": 0, "closeTime": 1_790_000_000, "resolved": True}, "children": []}]


NEXT_W1 = {"away": "Jets", "home": "Patriots", "kickoff_unix": 1_830_000_000, "week": 1, "season": 2027, "status": "final"}
NEXT_W2 = {"away": "Dolphins", "home": "Bills", "kickoff_unix": 1_830_600_000, "week": 2, "season": 2027, "status": "scheduled"}


def test_prior_season_same_question_does_not_block_listing(monkeypatch):
    _operator_env(monkeypatch)
    cid, cards = _resolved_2026_card()
    details = {f"http://api.test/api/v1/markets/{cid}": {"score": {"status": "final"}}}
    http_get, http_post, posts = _fake_api([NEXT_W1, NEXT_W2], cards=cards, details=details)
    summary = run(http_get=http_get, http_post=http_post, now=1_829_000_000)
    q = winner_question("Bills", "Dolphins")
    assert [body["question"] for body in _market_posts(posts)] == [q]
    assert summary["skipped"] == []
    assert summary["collisions"] == [{"question": q, "season": 2027, "week": 2}]


def test_prior_season_final_does_not_complete_new_week(monkeypatch):
    _operator_env(monkeypatch)
    cid, cards = _resolved_2026_card()
    details = {f"http://api.test/api/v1/markets/{cid}": {"score": {"status": "final"}}}
    w1_bills = dict(NEXT_W2, week=1, kickoff_unix=1_830_000_000)
    w2 = dict(NEXT_W1, week=2, kickoff_unix=1_830_600_000, status="scheduled")
    http_get, http_post, posts = _fake_api([w1_bills, w2], cards=cards, details=details)
    summary = run(http_get=http_get, http_post=http_post, now=1_829_000_000)
    assert posts == []
    assert summary["blocked"][0]["blockedBy"][0]["home"] == "Bills"
    assert week_complete([w1_bills], {cid: "final"}) is False


def test_listed_condition_id_links_live_status_and_skips(monkeypatch):
    _operator_env(monkeypatch)
    w3 = dict(W3_BILLS, status="scheduled", listedConditionId=BILLS_CID)
    w4 = dict(W4_CHIEFS, listedConditionId="0x" + "c4" * 32)
    details = {f"http://api.test/api/v1/markets/{BILLS_CID}": {"score": {"status": "final"}}}
    details[f"http://api.test/api/v1/markets/{w4['listedConditionId']}"] = {"score": None}
    http_get, http_post, posts = _fake_api([w3, w4, dict(W4_BILLS)], details=details)
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert [body["question"] for body in _market_posts(posts)] == [winner_question("Jets", "Bills")]
    assert summary["skipped"] == [winner_question("Broncos", "Chiefs")]


def test_same_game_twice_in_schedule_created_once(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS, dict(W4_CHIEFS)])

    def no_cid(url, body, headers=None):
        posts.append((url, body))
        return {}

    summary = run(http_get=http_get, http_post=no_cid, now=NOW)
    assert len(_market_posts(posts)) == 1
    assert summary["skipped"] == [winner_question("Broncos", "Chiefs")]


# --- stale schedule rows ----------------------------------------------------------


W2_STALE = {"away": "Giants", "home": "Rams", "kickoff_unix": 1000, "week": 2, "season": 2026, "status": "scheduled", "id": 16}
W3_FUTURE = {"away": "Jets", "home": "Patriots", "kickoff_unix": 1_800_000_100, "week": 3, "season": 2026, "status": "scheduled"}


def test_stale_unlisted_row_past_grace_unblocks(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W2_STALE, W3_FUTURE])
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert [body["question"] for body in _market_posts(posts)] == [winner_question("Patriots", "Jets")]
    assert summary["assumedDone"] == [
        {"season": 2026, "week": 2, "id": 16, "away": "Giants", "home": "Rams", "status": "scheduled", "kickoff_unix": 1000}
    ]
    assert summary["blocked"] == [] and summary["ok"] is True


def test_stale_row_inside_grace_blocks_and_is_reported(monkeypatch):
    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W2_STALE, W3_FUTURE])
    summary = run(http_get=http_get, http_post=http_post, now=1000 + DEFAULT_STALE_GRACE_SECONDS - 1)
    assert posts == []
    assert summary["blocked"][0]["week"] == 2
    assert [(g["away"], g["home"]) for g in summary["blocked"][0]["blockedBy"]] == [("Giants", "Rams")]
    assert summary["assumedDone"] == []


def test_listed_game_stuck_in_progress_past_grace_unblocks():
    game = dict(W3_BILLS, status="scheduled", listedConditionId=BILLS_CID)
    kickoff = game["kickoff_unix"]
    assert week_complete([game], {BILLS_CID: "in_progress"}, now=kickoff + DEFAULT_STALE_GRACE_SECONDS + 1) is True
    assert week_complete([game], {BILLS_CID: "in_progress"}, now=kickoff + 60) is False
    assert week_complete([game], {BILLS_CID: "in_progress"}) is False


def test_stale_grace_env(monkeypatch):
    assert stale_grace_seconds() == 8 * 3600
    monkeypatch.setenv("OU_LISTING_STALE_GRACE_SECONDS", "0")
    assert stale_grace_seconds() == 0
    for bad in ("-1", "soon"):
        monkeypatch.setenv("OU_LISTING_STALE_GRACE_SECONDS", bad)
        with pytest.raises(RuntimeError, match="OU_LISTING_STALE_GRACE_SECONDS"):
            stale_grace_seconds()


# --- squatted condition (409) -------------------------------------------------------


def test_create_conflict_is_squatted_not_stage_error(monkeypatch, capsys):
    _operator_env(monkeypatch)
    chiefs_q = winner_question("Broncos", "Chiefs")
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS, W4_BILLS], conflict_questions={chiefs_q})
    summary = run(http_get=http_get, http_post=http_post, now=NOW)
    assert summary["ok"] is True and summary["errors"] == []
    assert summary["squatted"] == [
        {
            "question": chiefs_q,
            "questionId": question_id(2026, 4, "Chiefs", "Broncos", W4_CHIEFS["kickoff_unix"]),
            "detail": "condition prepared outside this factory",
        }
    ]
    assert [row["question"] for row in summary["created"]] == [winner_question("Jets", "Bills")]
    assert "listing create squatted" in capsys.readouterr().err


def test_question_id_hmac_with_operator_key(monkeypatch):
    legacy = question_id(2026, 4, "Chiefs", "Broncos", 1)
    monkeypatch.setenv("OU_QUESTION_ID_KEY", "operator-secret-key-0123456789")
    keyed = question_id(2026, 4, "Chiefs", "Broncos", 1)
    assert keyed != legacy and keyed.startswith("0x") and len(keyed) == 66
    assert keyed == question_id(2026, 4, "Chiefs", "Broncos", 1)
    assert question_id(2026, 4, "Chiefs", "Broncos", 1, key="other") != keyed
    assert question_id(2026, 4, "Chiefs", "Broncos", 1, key="") == legacy


# --- orphaned links (PR #30 review) ------------------------------------------------

ORPHAN_CID = "0x" + "0d" * 32
PAUSED_CID = "0x" + "9a" * 32
LIVE_CID = "0x" + "11" * 32


class _Registry:
    """Fake chain: onchain_close_time per cid (0 = created on another oracle)."""

    def __init__(self, closes, fail=()):
        self.closes = closes
        self.fail = set(fail)
        self.calls = []

    def onchain_close_time(self, cid):
        self.calls.append(cid)
        if cid in self.fail:
            raise RuntimeError("rpc timeout")
        return self.closes.get(cid, 0)


def _schedule_posts(posts):
    return [body for url, body in posts if url.endswith("/markets/schedule")]


def test_orphan_link_is_relisted_and_relinked(monkeypatch):
    _operator_env(monkeypatch)
    chiefs_q = winner_question("Broncos", "Chiefs")
    # The orphan was archived, so the public list no longer shows it.
    http_get, http_post, posts = _fake_api([W3_BILLS, dict(W4_CHIEFS, listedConditionId=ORPHAN_CID)])
    chain = _Registry({})
    summary = run(http_get=http_get, http_post=http_post, now=NOW, chain=chain)
    assert [body["question"] for body in _market_posts(posts)] == [chiefs_q]
    assert summary["orphaned"] == [{"question": chiefs_q, "conditionId": ORPHAN_CID, "season": 2026, "week": 4}]
    assert summary["skipped"] == [] and summary["linkCheck"] == "on"
    ((row,),) = _schedule_posts(posts)
    assert row["listedConditionId"] == "0x" + "ee" * 32  # the new market replaces the orphan link
    assert chain.calls == [ORPHAN_CID]


def test_visible_unregistered_card_match_is_relisted(monkeypatch):
    """A legacy row with no link matched by (question, kickoff) to an unarchived orphan card."""
    _operator_env(monkeypatch)
    chiefs_q = winner_question("Broncos", "Chiefs")
    cards = [{"primary": {"conditionId": ORPHAN_CID, "question": chiefs_q, "marketType": 0, "closeTime": W4_CHIEFS["kickoff_unix"]}, "children": []}]
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS], cards=cards)
    summary = run(http_get=http_get, http_post=http_post, now=NOW, chain=_Registry({}))
    assert [body["question"] for body in _market_posts(posts)] == [chiefs_q]
    assert summary["orphaned"][0]["conditionId"] == ORPHAN_CID
    assert summary["collisions"] == [{"question": chiefs_q, "season": 2026, "week": 4}]


def test_orphan_link_cleared_when_game_cannot_be_relisted(monkeypatch):
    _operator_env(monkeypatch)
    started = dict(W4_CHIEFS, listedConditionId=ORPHAN_CID, kickoff_unix=NOW + 60)
    http_get, http_post, posts = _fake_api([W3_BILLS, started])
    summary = run(http_get=http_get, http_post=http_post, now=NOW, chain=_Registry({}))
    assert _market_posts(posts) == []
    assert summary["tooLate"] == [winner_question("Broncos", "Chiefs")]
    ((row,),) = _schedule_posts(posts)
    assert row["listedConditionId"] == "" and row["away"] == "Chiefs" and row["week"] == 4
    assert summary["ok"] is True


def test_registered_links_are_trusted(monkeypatch):
    _operator_env(monkeypatch)
    chiefs_q, bills_q = winner_question("Broncos", "Chiefs"), winner_question("Jets", "Bills")
    cards = [{"primary": {"conditionId": LIVE_CID, "question": chiefs_q, "marketType": 0, "closeTime": W4_CHIEFS["kickoff_unix"]}, "children": []}]
    schedule = [W3_BILLS, dict(W4_CHIEFS, listedConditionId=LIVE_CID), dict(W4_BILLS, listedConditionId=PAUSED_CID)]
    http_get, http_post, posts = _fake_api(schedule, cards=cards)
    chain = _Registry({LIVE_CID: W4_CHIEFS["kickoff_unix"], PAUSED_CID: W4_BILLS["kickoff_unix"]})
    summary = run(http_get=http_get, http_post=http_post, now=NOW, chain=chain)
    assert posts == []
    assert summary["skipped"] == [chiefs_q, bills_q] and summary["orphaned"] == []
    # Registered but hidden from the public list: paused by an operator, keep the link.
    assert summary["pausedLinks"] == [{"question": bills_q, "conditionId": PAUSED_CID}]


def test_link_check_failure_or_no_chain_config_trusts_links(monkeypatch):
    _operator_env(monkeypatch)
    schedule = [W3_BILLS, dict(W4_CHIEFS, listedConditionId=ORPHAN_CID)]
    http_get, http_post, posts = _fake_api(schedule)
    summary = run(http_get=http_get, http_post=http_post, now=NOW, chain=_Registry({}, fail={ORPHAN_CID}))
    assert posts == [] and summary["linkCheck"] == "errors"
    assert summary["linkCheckErrors"][0]["conditionId"] == ORPHAN_CID
    assert summary["ok"] is True
    http_get, http_post, posts = _fake_api(schedule)
    summary = run(http_get=http_get, http_post=http_post, now=NOW)  # no RPC / oracle env
    assert posts == [] and summary["linkCheck"] == "off"


def test_listing_defers_creates_once_budget_spent(monkeypatch):
    import budget

    _operator_env(monkeypatch)
    http_get, http_post, posts = _fake_api([W3_BILLS, W4_CHIEFS, W4_BILLS])
    budget.start(600)
    budget._deadline[0] = 0
    try:
        summary = run(http_get=http_get, http_post=http_post, now=NOW)
    finally:
        budget.clear()
    assert posts == []
    assert summary["deferred"] == [winner_question("Broncos", "Chiefs"), winner_question("Jets", "Bills")]
    assert summary["ok"] is True
