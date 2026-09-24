import pytest

import redact
from redact import MASK, log_error

ALCHEMY = "https://base-sepolia.g.alchemy.com/v2/abcdEFGH1234ijklMNOP"
QUERY_RPC = "https://rpc.example.org/base?apikey=QRY0123456789secret"
CREDS_RPC = "https://rpcuser:passw0rd-longish@rpc.example.org/"
JWT_SECRET = "test-secret-please-use-32b-min!!"
KEY = "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIweGYzOWYifQ.c2lnbmF0dXJlLWJ5dGVzLWhlcmU"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in redact.SECRET_ENV:
        monkeypatch.delenv(name, raising=False)


def test_rpc_path_key_masked_host_kept(monkeypatch):
    monkeypatch.setenv("ANVIL_RPC_URL", ALCHEMY)
    web3_err = (
        "HTTPSConnectionPool(host='base-sepolia.g.alchemy.com', port=443): "
        "Max retries exceeded with url: /v2/abcdEFGH1234ijklMNOP (Caused by ...)"
    )
    requests_err = f"401 Client Error: Unauthorized for url: {ALCHEMY}"
    out = redact.redact(f"{web3_err} | {requests_err}")
    assert "abcdEFGH1234ijklMNOP" not in out
    assert "base-sepolia.g.alchemy.com" in out
    assert f"https://base-sepolia.g.alchemy.com/{MASK}" in out


def test_rpc_query_and_credentials_masked(monkeypatch):
    monkeypatch.setenv("OU_RPC_URL", QUERY_RPC)
    monkeypatch.setenv("ANVIL_RPC_URL", CREDS_RPC)
    out = redact.redact(f"url: /base?apikey=QRY0123456789secret; auth rpcuser:passw0rd-longish; {QUERY_RPC}")
    for secret in ("QRY0123456789secret", "passw0rd-longish"):
        assert secret not in out
    assert "rpc.example.org" in out


def test_bare_local_rpc_url_left_readable(monkeypatch):
    monkeypatch.setenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
    assert redact.redact("RPC http://127.0.0.1:8545 refused") == "RPC http://127.0.0.1:8545 refused"


def test_env_keys_jwt_secret_and_cursor_key_masked(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("AGENT_BETA_KEY", KEY)
    monkeypatch.setenv("CURSOR_API_KEY", "crsr_live_0123456789abcdef")
    out = redact.redact(f"jwt {JWT_SECRET} key {KEY[2:]} cursor crsr_live_0123456789abcdef")
    for secret in (JWT_SECRET, KEY[2:], "crsr_live_0123456789abcdef"):
        assert secret not in out
    assert out.count(MASK) == 3


def test_bearer_and_jwt_tokens_masked_without_env():
    out = redact.redact(f"headers={{'Authorization': 'Bearer {TOKEN}'}} raw {TOKEN}")
    assert TOKEN not in out
    assert "eyJ" not in out
    assert f"Bearer {MASK}" in out


def test_condition_ids_and_tx_hashes_survive(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    cid = "0x" + "ab" * 32
    assert redact.redact(f"resolve failed {cid}: reverted") == f"resolve failed {cid}: reverted"


def test_log_error_prints_redacted_line(monkeypatch, capsys):
    monkeypatch.setenv("ANVIL_RPC_URL", ALCHEMY)
    log_error(f"resolve failed 0xab: Max retries exceeded with url: /v2/abcdEFGH1234ijklMNOP")
    err = capsys.readouterr().err
    assert "abcdEFGH1234ijklMNOP" not in err
    assert err.startswith("resolve failed 0xab:")


def test_resolve_stage_error_line_redacted(monkeypatch, capsys):
    from resolve import run as resolve_run

    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("ANVIL_RPC_URL", ALCHEMY)
    cid = "0x" + "cd" * 32

    class LeakyChain:
        def config_check(self):
            return {"ok": True}

        def is_resolved(self, _cid):
            raise ConnectionError(f"Max retries exceeded with url: /v2/abcdEFGH1234ijklMNOP ({ALCHEMY})")

    payloads = {
        "http://api.test/api/v1/markets": [{"primary": {"conditionId": cid, "question": "Chiefs vs Broncos: Chiefs win?", "marketType": 0}}],
        f"http://api.test/api/v1/markets/{cid}": {"closeTime": 1},
    }
    summary = resolve_run.run(http_get=payloads.__getitem__, chain=LeakyChain(), now=10)
    err = capsys.readouterr().err
    assert f"resolve failed {cid}:" in err
    assert "abcdEFGH1234ijklMNOP" not in err
    assert summary["results"][0]["ok"] is False


def test_listing_stage_error_line_redacted(monkeypatch, capsys):
    from listing import run as listing_run

    monkeypatch.setenv("OU_API_URL", "http://api.test")
    monkeypatch.setenv("JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("OPERATOR_PRIVATE_KEY", KEY)
    schedule = [
        {"season": 2026, "week": 3, "home": "Chiefs", "away": "Broncos", "kickoff_unix": 1, "status": "final"},
        {"season": 2026, "week": 4, "home": "Bills", "away": "Dolphins", "kickoff_unix": 10_000, "status": "scheduled"},
    ]
    payloads = {"http://api.test/api/v1/markets": [], "http://api.test/api/v1/markets/schedule": schedule}

    def leaky_post(url, body, headers=None):
        raise RuntimeError(f"rejected {headers['Authorization']} secret={JWT_SECRET}")

    summary = listing_run.run(http_get=payloads.__getitem__, http_post=leaky_post, now=0)
    err = capsys.readouterr().err
    assert "listing create failed Bills vs Dolphins: Bills win?" in err
    assert JWT_SECRET not in err
    assert "eyJ" not in err
    assert summary["ok"] is False
