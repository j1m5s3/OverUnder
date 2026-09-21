"""Test OverUnderPaymaster sponsoring gasless AMM operations."""

import boa
import pytest
from eth_abi import encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak, to_canonical_address

from tests.conftest import deploy_protocol

SELECTOR_EXECUTE = bytes.fromhex("b61d27f6")
SELECTOR_EXECUTE_BATCH = bytes.fromhex("47e1da2a")
SELECTOR_APPROVE = bytes.fromhex("095ea7b3")
SELECTOR_BUY_USDC = bytes.fromhex("a9c98025")
SELECTOR_MATCH_ORDERS = bytes.fromhex("e9f2cd3e")
SELECTOR_SET_APPROVAL = bytes.fromhex("a22cb465")


def _execute(target, data, value=0):
    return (
        SELECTOR_EXECUTE
        + int(target, 16).to_bytes(32, "big")
        + int(value).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(data).to_bytes(32, "big")
        + data
    )


def _approve(spender, amount):
    return SELECTOR_APPROVE + int(spender, 16).to_bytes(32, "big") + int(amount).to_bytes(32, "big")


def _pack_u128(hi, lo):
    return ((hi << 128) | lo).to_bytes(32, "big")


def _operator_digest(
    sender,
    nonce,
    init_code,
    call_data,
    account_gas_limits,
    pre_vg,
    gas_fees,
    valid_until,
    valid_after,
    paymaster,
    chain_id,
):
    return keccak(
        encode(
            [
                "address",
                "uint256",
                "bytes32",
                "bytes32",
                "bytes32",
                "uint256",
                "bytes32",
                "uint256",
                "uint256",
                "address",
                "uint256",
            ],
            [
                sender,
                nonce,
                keccak(init_code),
                keccak(call_data),
                account_gas_limits,
                pre_vg,
                gas_fees,
                valid_until,
                valid_after,
                paymaster,
                chain_id,
            ],
        )
    )


def _stamp(
    proto,
    sender,
    call_data,
    init_code=b"",
    nonce=0,
    account_gas_limits=None,
    pre_vg=21000,
    gas_fees=None,
    valid_until=None,
    valid_after=0,
):
    if account_gas_limits is None:
        account_gas_limits = b"\x00" * 32
    if gas_fees is None:
        gas_fees = b"\x00" * 32
    if valid_until is None:
        valid_until = int(boa.env.timestamp) + 300
    operator = proto["accounts"]["operator"]
    paymaster = proto["paymaster"].address
    digest = _operator_digest(
        sender,
        nonce,
        init_code,
        call_data,
        account_gas_limits,
        pre_vg,
        gas_fees,
        valid_until,
        valid_after,
        paymaster,
        proto["chain_id"],
    )
    sig = bytes(operator.sign_message(encode_defunct(primitive=digest)).signature)
    return (
        to_canonical_address(paymaster)
        + (100000).to_bytes(16, "big")
        + (80000).to_bytes(16, "big")
        + int(valid_until).to_bytes(6, "big")
        + int(valid_after).to_bytes(6, "big")
        + sig
    )


def _build_user_op(
    proto,
    sender,
    call_data,
    init_code=b"",
    nonce=0,
    account_gas_limits=None,
    gas_fees=None,
    pre_vg=21000,
    signature=b"",
):
    if account_gas_limits is None:
        account_gas_limits = b"\x00" * 32
    if gas_fees is None:
        gas_fees = b"\x00" * 32
    return (
        sender,
        nonce,
        init_code,
        call_data,
        account_gas_limits,
        pre_vg,
        gas_fees,
        _stamp(
            proto,
            sender,
            call_data,
            init_code=init_code,
            nonce=nonce,
            account_gas_limits=account_gas_limits,
            pre_vg=pre_vg,
            gas_fees=gas_fees,
        ),
        signature,
    )


def _fund_fee(proto, user):
    operator = proto["accounts"]["operator"].address
    usdc = proto["usdc"]
    paymaster = proto["paymaster"]
    with boa.env.prank(operator):
        usdc.mint(user, 100 * 10**6)
    with boa.env.prank(user):
        usdc.approve(paymaster.address, 2**256 - 1)


@pytest.fixture
def proto():
    return deploy_protocol()


@pytest.fixture
def zero_eth_user():
    return boa.env.generate_address()


@pytest.fixture
def market(proto):
    factory = proto["factory"]
    usdc = proto["usdc"]
    operator = proto["accounts"]["operator"].address
    question_id = b"\x41" * 32
    question = "Will BTC hit 100k in 2026?"
    seed_usdc = 1000 * 10**6
    close_time = boa.env.timestamp + 7 * 86400
    with boa.env.prank(operator):
        usdc.faucet(seed_usdc)
        usdc.approve(factory.address, seed_usdc)
        condition_id = factory.createPrimaryMarket(question_id, close_time, question, seed_usdc)
    return condition_id


def test_paymaster_deposit_info(proto):
    entrypoint = proto["entrypoint"]
    paymaster = proto["paymaster"]
    deposit, staked, stake, delay, withdraw = entrypoint.getDepositInfo(paymaster.address)
    assert deposit == 10**18


def test_paymaster_rejects_non_allowed_sender(proto):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    amm = proto["amm"]
    random_sender = boa.env.generate_address()
    buy_calldata = (
        SELECTOR_BUY_USDC
        + b"\x00" * 32
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    )
    execute_calldata = _execute(amm.address, buy_calldata)
    user_op = _build_user_op(proto, random_sender, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: sender not from allowed factory"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_paymaster_allows_amm_buy(proto, market, zero_eth_user):
    usdc = proto["usdc"]
    amm = proto["amm"]
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    assert boa.env.get_balance(zero_eth_user) == 0
    _fund_fee(proto, zero_eth_user)
    with boa.env.prank(zero_eth_user):
        usdc.approve(amm.address, 100 * 10**6)
    buy_calldata = (
        SELECTOR_BUY_USDC
        + market
        + (1).to_bytes(32, "big")
        + (50 * 10**6).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
    )
    execute_calldata = _execute(amm.address, buy_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
    assert validation_data & ((1 << 160) - 1) == 0
    assert boa.env.get_balance(zero_eth_user) == 0
    assert len(context) >= 52


def test_paymaster_blocks_match_orders(proto):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    exchange = proto["exchange"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(operator)
    match_calldata = SELECTOR_MATCH_ORDERS + b"\x00" * 1000
    execute_calldata = _execute(exchange.address, match_calldata)
    user_op = _build_user_op(proto, operator, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_paymaster_blocks_unknown_selector(proto):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(operator)
    unknown_calldata = bytes.fromhex("deadbeef") + b"\x00" * 100
    execute_calldata = _execute(amm.address, unknown_calldata)
    user_op = _build_user_op(proto, operator, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_paymaster_allows_usdc_approve_to_ctf(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    _fund_fee(proto, zero_eth_user)
    approve_calldata = _approve(ctf.address, 1000 * 10**6)
    execute_calldata = _execute(usdc.address, approve_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
    assert validation_data & ((1 << 160) - 1) == 0


def test_paymaster_blocks_usdc_approve_to_unknown(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    random_addr = boa.env.generate_address()
    approve_calldata = _approve(random_addr, 1000 * 10**6)
    execute_calldata = _execute(usdc.address, approve_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_paymaster_blocks_ctf_set_approval_bad_operator(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    ctf = proto["ctf"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    random_operator = boa.env.generate_address()
    set_approval_calldata = (
        SELECTOR_SET_APPROVAL + int(random_operator, 16).to_bytes(32, "big") + (1).to_bytes(32, "big")
    )
    execute_calldata = _execute(ctf.address, set_approval_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_paymaster_allows_ctf_set_approval_allowed_operator(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    ctf = proto["ctf"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    _fund_fee(proto, zero_eth_user)
    set_approval_calldata = (
        SELECTOR_SET_APPROVAL + int(amm.address, 16).to_bytes(32, "big") + (1).to_bytes(32, "big")
    )
    execute_calldata = _execute(ctf.address, set_approval_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
    assert validation_data & ((1 << 160) - 1) == 0


def test_paymaster_blocks_crafted_execute_offset_hiding_match_orders(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    exchange = proto["exchange"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    match_orders_calldata = SELECTOR_MATCH_ORDERS + b"\x00" * 200
    crafted_offset = 200
    execute_calldata = (
        SELECTOR_EXECUTE
        + int(exchange.address, 16).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + crafted_offset.to_bytes(32, "big")
        + b"\x00" * (crafted_offset - 96)
        + len(match_orders_calldata).to_bytes(32, "big")
        + match_orders_calldata
    )
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_factory_initcode_allowed(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    factory = proto["account_factory"]
    owner = proto["accounts"]["trader_a"].address
    salt = b"\x00" * 32
    predicted = factory.getAddress(owner, salt)
    created = factory.createAccount(owner, salt)
    assert created == predicted
    create_cd = factory.createAccount.prepare_calldata(owner, salt)
    init_code = to_canonical_address(factory.address) + bytes(create_cd)
    sender = factory.getAddress(owner, b"\x01" + b"\x00" * 31)
    with boa.env.prank(proto["accounts"]["operator"].address):
        usdc.mint(sender, 100 * 10**6)
    approve_calldata = _approve(paymaster.address, 2**256 - 1)
    execute_calldata = _execute(usdc.address, approve_calldata)
    user_op = _build_user_op(proto, sender, execute_calldata, init_code=init_code)
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
    assert validation_data & ((1 << 160) - 1) == 0
    assert paymaster.allowedSenders(sender)


def test_unknown_factory_rejected(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
    bad_factory = boa.env.generate_address()
    init_code = to_canonical_address(bad_factory) + b"\x00" * 4
    buy_calldata = SELECTOR_BUY_USDC + b"\x00" * 128
    execute_calldata = _execute(amm.address, buy_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata, init_code=init_code)
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: factory not allowed"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)


def test_handle_ops_zero_eth_buy_yes(proto, market):
    owner = Account.create()
    boa.env.set_balance(owner.address, 10**18)
    factory = proto["account_factory"]
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    amm = proto["amm"]
    ctf = proto["ctf"]
    operator_addr = proto["accounts"]["operator"].address
    salt = b"\x00" * 32
    account_addr = factory.createAccount(owner.address, salt)
    account = boa.load_partial("src/SimpleAccount.vy").at(account_addr)
    boa.env.set_balance(account_addr, 0)
    with boa.env.prank(operator_addr):
        paymaster.addSender(account_addr)
        usdc.mint(account_addr, 100 * 10**6)
    approve_pm = _approve(paymaster.address, 2**256 - 1)
    approve_amm = _approve(amm.address, 2**256 - 1)
    with boa.env.prank(owner.address):
        account.execute(usdc.address, 0, approve_pm)
        account.execute(usdc.address, 0, approve_amm)
    buy_calldata = bytes(amm.buyWithUSDC.prepare_calldata(market, True, 50 * 10**6, 0))
    execute_calldata = bytes(account.execute.prepare_calldata(amm.address, 0, buy_calldata))
    gas_limits = _pack_u128(100000, 200000)
    gas_fees = _pack_u128(1, 1)
    user_op = _build_user_op(
        proto,
        account_addr,
        execute_calldata,
        account_gas_limits=gas_limits,
        gas_fees=gas_fees,
        pre_vg=50000,
    )
    user_op_hash = entrypoint.getUserOpHash(user_op)
    sig = bytes(owner.sign_message(encode_defunct(primitive=bytes(user_op_hash))).signature)
    user_op = user_op[:-1] + (sig,)
    deposit_before = entrypoint.getDepositInfo(paymaster.address)[0]
    fee_before = usdc.balanceOf(operator_addr)
    entrypoint.handleOps([user_op], operator_addr)
    deposit_after = entrypoint.getDepositInfo(paymaster.address)[0]
    assert deposit_after < deposit_before
    yes_id = ctf.positionId(market, 0)
    assert ctf.balanceOf(account_addr, yes_id) > 0
    assert usdc.balanceOf(operator_addr) > fee_before
    assert boa.env.get_balance(account_addr) == 0


def test_daily_cap_exceeded_reverts(proto, zero_eth_user):
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"].address
    with boa.env.prank(operator):
        paymaster.addSender(zero_eth_user)
        paymaster.setDailyCapUsdc(10**6)
    _fund_fee(proto, zero_eth_user)
    buy_calldata = SELECTOR_BUY_USDC + b"\x00" * 128
    execute_calldata = _execute(amm.address, buy_calldata)
    user_op = _build_user_op(proto, zero_eth_user, execute_calldata)
    with boa.env.prank(entrypoint.address):
        paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
        with boa.reverts("paymaster: daily cap"):
            paymaster.validatePaymasterUserOp(user_op, b"\x00" * 32, 10**15)
