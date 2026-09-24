"""MarketFactory v2: loosely gated user listing (OU-T010), legacy import and factory switch."""

from pathlib import Path

import boa
import pytest
from eth_abi import encode
from eth_utils import keccak

from tests.conftest import deploy_protocol
from tests.eip712 import sign_attestation

V1_FACTORY = str(Path(__file__).resolve().parent / "fixtures" / "MarketFactoryV1.vy")
CRIT = keccak(text="YES if it rains at Logan Airport per NOAA daily summary")
QUESTION = "Will it rain in Boston tomorrow?"
MIN_SEED = 10_000_000
LEAD = 3600
HORIZON = 7_776_000


@pytest.fixture
def proto():
    return deploy_protocol()


def _op(proto):
    return proto["accounts"]["operator"].address


def _fund(usdc, who, amount, spender):
    with boa.env.prank(who):
        usdc.faucet(amount)
        usdc.approve(spender, amount)


def _open(proto, fee=0, cooldown=3600):
    with boa.env.prank(_op(proto)):
        proto["factory"].setListingConfig(MIN_SEED, fee, proto["vault"].address, LEAD, HORIZON, cooldown)
        proto["factory"].setPermissionless(True)


def _list(proto, who, salt=b"\x01" * 32, close=None, question=QUESTION, crit=CRIT, seed=MIN_SEED, fund=True):
    factory = proto["factory"]
    if close is None:
        close = boa.env.timestamp + 2 * LEAD
    if fund:
        _fund(proto["usdc"], who, seed + factory.listingFeeUsdc(), factory.address)
    with boa.env.prank(who):
        return factory.createPermissionlessMarket(salt, close, question, crit, seed)


def _event(contract, name):
    return [e for e in contract.get_logs() if type(e).__name__ == name][-1]


def _load_isolated(path, *args):
    # Fresh sender: keeps the default deployer nonce (and every protocol address) stable across tests.
    with boa.env.prank(boa.env.generate_address()):
        return boa.load(path, *args)


def _v1_factory(proto):
    p = proto
    return _load_isolated(
        V1_FACTORY, p["ctf"].address, p["oracle"].address, p["amm"].address, p["usdc"].address, _op(p), p["accounts"]["generator"].address
    )


def _switch(proto, factory_addr):
    with boa.env.prank(_op(proto)):
        proto["oracle"].setFactory(factory_addr)
        proto["amm"].setFactory(factory_addr)


def test_defaults_closed_by_default(proto):
    factory = proto["factory"]
    user = proto["accounts"]["trader_a"].address
    assert factory.permissionless() is False
    assert factory.listerAllowed(user) is False
    assert factory.minSeedUsdc() == MIN_SEED
    assert factory.listingFeeUsdc() == 0
    assert factory.minLeadTime() == LEAD
    assert factory.maxHorizon() == HORIZON
    assert factory.listingCooldown() == 3600
    assert factory.MARKET_TYPE_USER() == 2
    with boa.reverts("listing closed"):
        _list(proto, user)


def test_allowlisted_lister_can_list(proto):
    factory = proto["factory"]
    user = proto["accounts"]["trader_b"].address
    with boa.env.prank(_op(proto)):
        factory.setLister(user, True)
    assert _event(factory, "ListerSet").allowed is True
    cid = _list(proto, user)
    assert factory.marketExists(cid)
    with boa.env.prank(_op(proto)):
        factory.setLister(user, False)
    boa.env.time_travel(seconds=3601)
    with boa.reverts("listing closed"):
        _list(proto, user, salt=b"\x02" * 32)


def test_permissionless_flag_opens_listing(proto):
    factory = proto["factory"]
    user = proto["accounts"]["trader_a"].address
    with boa.env.prank(_op(proto)):
        factory.setPermissionless(True)
    assert _event(factory, "PermissionlessSet").enabled is True
    assert factory.marketExists(_list(proto, user))
    with boa.env.prank(_op(proto)):
        factory.setPermissionless(False)
    boa.env.time_travel(seconds=3601)
    with boa.reverts("listing closed"):
        _list(proto, user, salt=b"\x03" * 32)


def test_seed_below_min(proto):
    _open(proto)
    with boa.reverts("seed below min"):
        _list(proto, proto["accounts"]["trader_a"].address, seed=MIN_SEED - 1)


def test_close_too_soon_and_lead_boundary(proto):
    _open(proto, cooldown=0)
    user = proto["accounts"]["trader_a"].address
    with boa.reverts("close too soon"):
        _list(proto, user, close=boa.env.timestamp + LEAD - 1)
    cid = _list(proto, user, close=boa.env.timestamp + LEAD)
    assert proto["factory"].markets(cid)[2] == boa.env.timestamp + LEAD


def test_close_too_far_and_horizon_boundary(proto):
    _open(proto, cooldown=0)
    user = proto["accounts"]["trader_a"].address
    with boa.reverts("close too far"):
        _list(proto, user, close=boa.env.timestamp + HORIZON + 1)
    assert proto["factory"].marketExists(_list(proto, user, close=boa.env.timestamp + HORIZON))


def test_question_too_short(proto):
    _open(proto, cooldown=0)
    user = proto["accounts"]["trader_a"].address
    with boa.reverts("question too short"):
        _list(proto, user, question="Rain BOS?")
    assert proto["factory"].marketExists(_list(proto, user, question="Rain BOS??"))


def test_criteria_required(proto):
    _open(proto)
    with boa.reverts("criteria required"):
        _list(proto, proto["accounts"]["trader_a"].address, crit=b"\x00" * 32)


def test_cooldown_per_creator(proto):
    _open(proto, cooldown=3600)
    a = proto["accounts"]["trader_a"].address
    b = proto["accounts"]["trader_b"].address
    _list(proto, a, salt=b"\x10" * 32)
    listed_at = boa.env.timestamp
    assert proto["factory"].lastListedAt(a) == listed_at
    with boa.reverts("cooldown"):
        _list(proto, a, salt=b"\x11" * 32)
    # Another creator is not throttled by a's listing.
    _list(proto, b, salt=b"\x10" * 32)
    boa.env.time_travel(seconds=3599)
    with boa.reverts("cooldown"):
        _list(proto, a, salt=b"\x11" * 32)
    boa.env.time_travel(seconds=1)
    assert proto["factory"].marketExists(_list(proto, a, salt=b"\x11" * 32))


def test_user_cid_matches_view_and_offchain_formula(proto):
    factory, oracle, ctf = proto["factory"], proto["oracle"], proto["ctf"]
    _open(proto)
    user = proto["accounts"]["trader_a"].address
    salt = keccak(text="salt-1")
    qid = keccak(encode(["address", "bytes32"], [user, salt]))
    expected = keccak(encode(["address", "bytes32"], [oracle.address, qid]))
    assert factory.userQuestionId(user, salt) == qid
    assert factory.userConditionId(user, salt) == expected
    cid = _list(proto, user, salt=salt)
    assert cid == expected
    assert ctf.outcomeSlots(cid) == 2


def test_user_qid_is_namespaced_by_creator(proto):
    factory = proto["factory"]
    _open(proto)
    a = proto["accounts"]["trader_a"].address
    b = proto["accounts"]["trader_b"].address
    salt = b"\x71" * 32
    # An operator primary on questionId == salt does not collide with a user listing on the same salt.
    with boa.env.prank(_op(proto)):
        proto["usdc"].faucet(MIN_SEED)
        proto["usdc"].approve(factory.address, MIN_SEED)
        primary = factory.createPrimaryMarket(salt, boa.env.timestamp + 2 * LEAD, "Operator primary", MIN_SEED)
    cid_a = _list(proto, a, salt=salt)
    cid_b = _list(proto, b, salt=salt)
    assert len({primary, cid_a, cid_b}) == 3
    boa.env.time_travel(seconds=3600)
    with boa.reverts("already prepared"):
        _list(proto, a, salt=salt)


def test_user_market_type_2_registered_and_seeded(proto):
    factory, oracle, amm, usdc = proto["factory"], proto["oracle"], proto["amm"], proto["usdc"]
    _open(proto)
    user = proto["accounts"]["trader_a"].address
    seed = 25_000_000
    close = boa.env.timestamp + 3 * LEAD
    salt = b"\x21" * 32
    cid = _list(proto, user, salt=salt, close=close, seed=seed)
    listed = _event(factory, "UserMarketListed")
    created = _event(factory, "MarketCreated")
    assert listed.conditionId == cid
    assert listed.creator == user
    assert listed.questionId == factory.userQuestionId(user, salt)
    assert listed.criteriaHash == CRIT
    assert listed.seedUsdc == seed
    assert listed.listingFee == 0
    assert created.creator == user and created.marketType == 2
    m = factory.markets(cid)
    assert m == (cid, b"\x00" * 32, close, 2, False, QUESTION)
    assert oracle.closeTime(cid) == close
    pool = amm.pools(cid)
    assert pool[:4] == (seed, seed, seed, True)
    assert pool[6] == close
    assert amm.lpBalance(cid, user) == seed
    assert amm.lpBalance(cid, factory.address) == 0
    assert factory.creatorOf(cid) == user
    assert factory.seedOf(cid) == seed
    assert factory.criteriaHashOf(cid) == CRIT
    assert usdc.balanceOf(factory.address) == 0
    # The creator owns the LP but the seed is locked until closeTime: listing costs real capital.
    assert amm.seedLocked(cid, user) == seed
    assert amm.lpLocked(cid, user) == seed
    with boa.env.prank(user):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed)
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, 1)
    # After closeTime everything but MIN_LP comes back (pro rata, never trade-gated).
    boa.env.time_travel(seconds=close - boa.env.timestamp)
    min_lp = amm.MIN_LP()
    assert amm.lpLocked(cid, user) == min_lp
    with boa.env.prank(user):
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, seed)
        paid = amm.removeLiquidity(cid, seed - min_lp)
    assert paid == seed - min_lp
    assert usdc.balanceOf(user) == seed - min_lp
    assert amm.pools(cid)[2] == min_lp


def test_listing_seed_cannot_be_recycled_into_dead_markets(proto):
    """List-then-withdraw in one block used to return the whole seed and leave a Pool(0,0,0,exists) market."""
    factory, amm, usdc = proto["factory"], proto["amm"], proto["usdc"]
    _open(proto, cooldown=0)
    user = proto["accounts"]["trader_a"].address
    cid = _list(proto, user, salt=b"\x61" * 32)
    with boa.env.prank(user):
        for amount in (MIN_SEED, MIN_SEED - 1, 1):
            with boa.reverts("seed locked"):
                amm.removeLiquidity(cid, amount)
    assert usdc.balanceOf(user) == 0
    # The seed stays in the pool, so a second listing needs fresh capital.
    with boa.env.prank(user):
        with boa.reverts():
            factory.createPermissionlessMarket(b"\x62" * 32, boa.env.timestamp + 2 * LEAD, QUESTION, CRIT, MIN_SEED)
    pool = amm.pools(cid)
    assert pool[2] == MIN_SEED and pool[4] > 0
    assert 0 < amm.priceYes(cid) < 10**18


def test_listing_lp_added_later_stays_withdrawable(proto):
    amm, usdc = proto["amm"], proto["usdc"]
    _open(proto)
    user = proto["accounts"]["trader_a"].address
    cid = _list(proto, user, salt=b"\x63" * 32)
    _fund(usdc, user, 5_000_000, amm.address)
    with boa.env.prank(user):
        minted = amm.addLiquidity(cid, 5_000_000)
        assert amm.lpLocked(cid, user) == MIN_SEED
        with boa.reverts("seed locked"):
            amm.removeLiquidity(cid, minted + 1)
        paid = amm.removeLiquidity(cid, minted)
    assert 5_000_000 - 2 <= paid <= 5_000_000
    assert amm.lpBalance(cid, user) == MIN_SEED


def test_listing_fee_to_fee_vault(proto):
    factory, usdc, vault = proto["factory"], proto["usdc"], proto["vault"]
    _open(proto, fee=1_500_000)
    user = proto["accounts"]["trader_a"].address
    before = usdc.balanceOf(vault.address)
    cid = _list(proto, user)
    assert usdc.balanceOf(vault.address) - before == 1_500_000
    assert usdc.balanceOf(user) == 0
    assert usdc.balanceOf(factory.address) == 0
    assert _event(factory, "UserMarketListed").listingFee == 1_500_000
    assert proto["amm"].lpBalance(cid, user) == MIN_SEED


def test_listing_requires_seed_plus_fee_allowance(proto):
    factory, usdc = proto["factory"], proto["usdc"]
    _open(proto, fee=1_000_000)
    user = proto["accounts"]["trader_a"].address
    _fund(usdc, user, MIN_SEED, factory.address)
    with boa.env.prank(user):
        with boa.reverts():
            factory.createPermissionlessMarket(b"\x31" * 32, boa.env.timestamp + 2 * LEAD, QUESTION, CRIT, MIN_SEED)
    assert factory.lastListedAt(user) == 0


def test_set_listing_config_bounds_and_operator_only(proto):
    factory, vault = proto["factory"], proto["vault"].address
    op = _op(proto)
    trader = proto["accounts"]["trader_a"].address
    with boa.env.prank(trader):
        with boa.reverts("not operator"):
            factory.setListingConfig(MIN_SEED, 0, vault, LEAD, HORIZON, 0)
        with boa.reverts("not operator"):
            factory.setPermissionless(True)
        with boa.reverts("not operator"):
            factory.setLister(trader, True)
    with boa.env.prank(op):
        with boa.reverts("min seed"):
            factory.setListingConfig(0, 0, vault, LEAD, HORIZON, 0)
        with boa.reverts("fee recipient"):
            factory.setListingConfig(MIN_SEED, 1, boa.eval("empty(address)"), LEAD, HORIZON, 0)
        with boa.reverts("lead floor"):
            factory.setListingConfig(MIN_SEED, 0, vault, 599, HORIZON, 0)
        with boa.reverts("horizon"):
            factory.setListingConfig(MIN_SEED, 0, vault, LEAD, LEAD, 0)
        with boa.reverts("horizon"):
            factory.setListingConfig(MIN_SEED, 0, vault, LEAD, 31_622_401, 0)
        with boa.reverts("zero account"):
            factory.setLister(boa.eval("empty(address)"), True)
        factory.setListingConfig(5_000_000, 250_000, vault, 600, 31_622_400, 60)
        cfg = _event(factory, "ListingConfigSet")
        # Zero fee with no recipient is allowed.
        factory.setListingConfig(MIN_SEED, 0, boa.eval("empty(address)"), LEAD, HORIZON, 0)
    assert (cfg.minSeedUsdc, cfg.listingFeeUsdc, cfg.feeRecipient, cfg.minLeadTime, cfg.maxHorizon, cfg.listingCooldown) == (
        5_000_000,
        250_000,
        vault,
        600,
        31_622_400,
        60,
    )
    assert factory.listingCooldown() == 0
    assert factory.feeRecipient() == boa.eval("empty(address)")


def test_operator_can_pause_user_market(proto):
    factory = proto["factory"]
    _open(proto)
    cid = _list(proto, proto["accounts"]["trader_a"].address)
    with boa.env.prank(proto["accounts"]["trader_a"].address):
        with boa.reverts("not operator"):
            factory.setPaused(cid, True)
    with boa.env.prank(_op(proto)):
        factory.setPaused(cid, True)
    assert factory.markets(cid)[4] is True


def _resolve(proto, cid, outcome):
    oracle = proto["oracle"]
    evidence = b"\xee" * 32
    deadline = boa.env.timestamp + 1000
    sigs = [
        sign_attestation(proto["accounts"][n].key, oracle.address, proto["chain_id"], cid, outcome, evidence, deadline)
        for n in ("alpha", "beta", "gamma")
    ]
    oracle.submitConsensus(cid, outcome, evidence, deadline, sigs)


def test_user_market_resolves_via_submit_consensus(proto):
    ctf, usdc, amm = proto["ctf"], proto["usdc"], proto["amm"]
    _open(proto)
    creator = proto["accounts"]["trader_a"].address
    buyer = proto["accounts"]["trader_b"].address
    close = boa.env.timestamp + 2 * LEAD
    cid = _list(proto, creator, close=close)
    _fund(usdc, buyer, 2_000_000, amm.address)
    with boa.env.prank(buyer):
        got = amm.buyWithUSDC(cid, True, 2_000_000, 0)
    boa.env.time_travel(seconds=close - boa.env.timestamp)
    _resolve(proto, cid, 0)
    assert ctf.isResolved(cid)
    with boa.env.prank(buyer):
        ctf.redeemPositions(cid, 0, got)
    assert usdc.balanceOf(buyer) == got
    # Creator withdraws the remaining LP after resolution (tokens plus accrued LP fees), all but MIN_LP.
    lp = amm.lpBalance(cid, creator)
    min_lp = amm.MIN_LP()
    assert amm.lpLocked(cid, creator) == min_lp
    with boa.env.prank(creator):
        amm.removeLiquidity(cid, lp - min_lp)
    assert amm.lpBalance(cid, creator) == min_lp
    assert amm.pools(cid)[2] == min_lp


def test_import_legacy_markets_from_v1(proto):
    usdc, factory = proto["usdc"], proto["factory"]
    op = _op(proto)
    v1 = _v1_factory(proto)
    _switch(proto, v1.address)
    close = boa.env.timestamp + 10_000
    _fund(usdc, op, 60_000_000, v1.address)
    with boa.env.prank(op):
        live = v1.createPrimaryMarket(b"\x55" * 32, close, "Bills vs Dolphins: Bills win?", 50_000_000)
        paused = v1.createPrimaryMarket(b"\x56" * 32, close, "Jets vs Pats: Jets win?", 10_000_000)
        v1.setPaused(paused, True)
    _switch(proto, factory.address)
    trader = proto["accounts"]["trader_a"].address
    with boa.env.prank(trader):
        with boa.reverts("not operator"):
            factory.importLegacyMarkets(v1.address, [live])
    with boa.env.prank(op):
        with boa.reverts("not in legacy"):
            factory.importLegacyMarkets(v1.address, [b"\x99" * 32])
        with boa.reverts("bad legacy"):
            factory.importLegacyMarkets(factory.address, [live])
        factory.importLegacyMarkets(v1.address, [live, paused])
        imported = [e for e in factory.get_logs() if type(e).__name__ == "LegacyMarketImported"]
        # Re-import is a no-op (no event, no revert).
        factory.importLegacyMarkets(v1.address, [live])
        again = [e for e in factory.get_logs() if type(e).__name__ == "LegacyMarketImported"]
    assert [e.conditionId for e in imported] == [live, paused]
    assert all(e.legacyFactory == v1.address for e in imported)
    assert again == []
    assert factory.markets(live) == (live, b"\x00" * 32, close, 0, False, "Bills vs Dolphins: Bills win?")
    assert factory.markets(paused)[4] is True
    with boa.env.prank(op):
        factory.setPaused(live, True)
    assert factory.markets(live)[4] is True


def test_switch_factory_old_factory_cannot_create(proto):
    usdc, factory = proto["usdc"], proto["factory"]
    op = _op(proto)
    v1 = _v1_factory(proto)
    _switch(proto, v1.address)
    close = boa.env.timestamp + 10_000
    _fund(usdc, op, 50_000_000, v1.address)
    with boa.env.prank(op):
        old = v1.createPrimaryMarket(b"\x71" * 32, close, "Legacy primary", 50_000_000)
    _switch(proto, factory.address)
    with boa.env.prank(op):
        factory.importLegacyMarkets(v1.address, [old])
    _fund(usdc, op, 20_000_000, v1.address)
    with boa.env.prank(op):
        with boa.reverts():
            v1.createPrimaryMarket(b"\x72" * 32, close, "Old factory now dead", 10_000_000)
        with boa.reverts():
            v1.createWildcardMarket(b"\x73" * 32, old, close, "Old wildcard now dead", 0)
        usdc.approve(factory.address, 20_000_000)
        with boa.reverts("already prepared"):
            factory.createPrimaryMarket(b"\x71" * 32, close, "Legacy primary", 10_000_000)
        new = factory.createPrimaryMarket(b"\x74" * 32, close, "New primary on v2", 10_000_000)
        child = factory.createWildcardMarket(b"\x75" * 32, old, close, "Wildcard under imported parent", 0)
    assert factory.marketExists(new)
    assert factory.markets(child)[1] == old


def test_condition_id_matches_offchain_formula(proto):
    """Vector for the backend condition_id_for(oracle, questionId)."""
    factory, oracle = proto["factory"], proto["oracle"]
    qid = bytes.fromhex("aa" * 32)
    op = _op(proto)
    _fund(proto["usdc"], op, MIN_SEED, factory.address)
    with boa.env.prank(op):
        cid = factory.createPrimaryMarket(qid, boa.env.timestamp + 10_000, "Vector primary", MIN_SEED)
    assert cid == keccak(encode(["address", "bytes32"], [oracle.address, qid]))
