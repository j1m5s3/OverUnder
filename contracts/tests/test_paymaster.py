"""Test OverUnderPaymaster sponsoring gasless AMM operations."""

import boa
import pytest

from tests.conftest import deploy_protocol


def _build_user_op(sender, call_data):
    """Build a PackedUserOperation tuple for testing"""
    return (
        sender,  # sender
        0,  # nonce
        b"",  # initCode
        call_data,  # callData
        b"\x00" * 32,  # accountGasLimits
        21000,  # preVerificationGas
        b"\x00" * 32,  # gasFees
        b"",  # paymasterAndData
        b"",  # signature
    )


@pytest.fixture
def proto():
    return deploy_protocol()


@pytest.fixture
def zero_eth_user():
    """User account with 0 ETH balance (AA smart account)"""
    return boa.env.generate_address()


@pytest.fixture
def market(proto):
    """Create a seeded primary market"""
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
    """Verify paymaster has ETH deposited on entrypoint"""
    entrypoint = proto["entrypoint"]
    paymaster = proto["paymaster"]
    
    deposit, staked, stake, delay, withdraw = entrypoint.getDepositInfo(paymaster.address)
    assert deposit == 10**18  # 1 ETH deposited in fixture


def test_paymaster_allows_amm_buy(proto, market, zero_eth_user):
    """
    0-eth account with USDC completes AMM buy via sponsored UserOp.
    
    Simulates:
    1. User has 0 ETH but has USDC
    2. User calls execute(amm.buyWithUSDC) via AA wallet
    3. Paymaster validates and sponsors the operation
    4. User receives outcome tokens
    """
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    amm = proto["amm"]
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    operator = proto["accounts"]["operator"].address
    
    # User starts with 0 ETH
    assert boa.env.get_balance(zero_eth_user) == 0
    
    # Give user USDC (they bought it via ramp, no gas needed)
    with boa.env.prank(operator):
        usdc.mint(zero_eth_user, 100 * 10**6)
    
    # User approves AMM to spend USDC
    with boa.env.prank(zero_eth_user):
        usdc.approve(amm.address, 100 * 10**6)
    
    # Construct AMM buy calldata: buyWithUSDC(bytes32,bool,uint256,uint256)
    buy_yes = True  # Buy YES tokens
    usdc_in = 50 * 10**6
    min_out = 0  # no slippage check for test
    
    # In reality, this would be wrapped in AA execute(address,uint256,bytes)
    # For this test, we directly call to verify the paymaster allowlist logic
    
    # Build calldata for AMM buy
    buy_calldata = (
        bytes.fromhex("8b7a7fb9")  # buyWithUSDC selector
        + market  # conditionId is already bytes32
        + (1 if buy_yes else 0).to_bytes(32, "big")  # bool as uint256
        + usdc_in.to_bytes(32, "big")
        + min_out.to_bytes(32, "big")
    )
    
    # Build execute(amm.buyWithUSDC) calldata
    execute_calldata = (
        bytes.fromhex("b61d27f6")  # execute selector
        + int(amm.address, 16).to_bytes(32, "big")  # target
        + (0).to_bytes(32, "big")  # value
        + (96).to_bytes(32, "big")  # data offset (3*32 = 96)
        + len(buy_calldata).to_bytes(32, "big")  # data length
        + buy_calldata  # inner calldata
    )
    
    user_op = _build_user_op(zero_eth_user, execute_calldata)
    user_op_hash = b"\x00" * 32
    max_cost = 10**15
    
    # Paymaster validates the UserOp (this is what we're testing)
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(
            user_op, user_op_hash, max_cost
        )
    
    # Paymaster approved the operation
    assert validation_data == 0
    
    # Verify user still has 0 ETH (gasless operation would work)
    assert boa.env.get_balance(zero_eth_user) == 0


def test_paymaster_blocks_match_orders(proto):
    """Paymaster rejects matchOrders (relayer-only operation)"""
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    exchange = proto["exchange"]
    operator = proto["accounts"]["operator"].address
    
    # Build calldata for matchOrders
    match_calldata = bytes.fromhex("8a920150") + b"\x00" * 1000  # matchOrders + garbage
    
    # Wrap in execute
    execute_calldata = (
        bytes.fromhex("b61d27f6")  # execute
        + int(exchange.address, 16).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(match_calldata).to_bytes(32, "big")
        + match_calldata
    )
    
    user_op = _build_user_op(operator, execute_calldata)
    user_op_hash = b"\x00" * 32
    max_cost = 10**15
    
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, user_op_hash, max_cost)


def test_paymaster_blocks_unknown_selector(proto):
    """Paymaster rejects unknown function selectors"""
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    amm = proto["amm"]
    operator = proto["accounts"]["operator"].address
    
    # Unknown selector
    unknown_calldata = bytes.fromhex("deadbeef") + b"\x00" * 100
    
    execute_calldata = (
        bytes.fromhex("b61d27f6")  # execute
        + int(amm.address, 16).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(unknown_calldata).to_bytes(32, "big")
        + unknown_calldata
    )
    
    user_op = _build_user_op(operator, execute_calldata)
    user_op_hash = b"\x00" * 32
    max_cost = 10**15
    
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, user_op_hash, max_cost)


def test_paymaster_allows_usdc_approve_to_ctf(proto, zero_eth_user):
    """Paymaster allows USDC approve to CTF"""
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    ctf = proto["ctf"]
    
    # Build approve calldata: approve(address,uint256)
    approve_calldata = (
        bytes.fromhex("095ea7b3")  # approve selector
        + int(ctf.address, 16).to_bytes(32, "big")  # spender
        + (1000 * 10**6).to_bytes(32, "big")  # amount
    )
    
    execute_calldata = (
        bytes.fromhex("b61d27f6")  # execute
        + int(usdc.address, 16).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(approve_calldata).to_bytes(32, "big")
        + approve_calldata
    )
    
    user_op = _build_user_op(zero_eth_user, execute_calldata)
    user_op_hash = b"\x00" * 32
    max_cost = 10**15
    
    with boa.env.prank(entrypoint.address):
        context, validation_data = paymaster.validatePaymasterUserOp(
            user_op, user_op_hash, max_cost
        )
    
    assert validation_data == 0  # valid


def test_paymaster_blocks_usdc_approve_to_unknown(proto, zero_eth_user):
    """Paymaster blocks USDC approve to addresses not in allowlist"""
    paymaster = proto["paymaster"]
    entrypoint = proto["entrypoint"]
    usdc = proto["usdc"]
    
    random_addr = boa.env.generate_address()
    
    # Build approve calldata to random address
    approve_calldata = (
        bytes.fromhex("095ea7b3")
        + int(random_addr, 16).to_bytes(32, "big")
        + (1000 * 10**6).to_bytes(32, "big")
    )
    
    execute_calldata = (
        bytes.fromhex("b61d27f6")
        + int(usdc.address, 16).to_bytes(32, "big")
        + (0).to_bytes(32, "big")
        + (96).to_bytes(32, "big")
        + len(approve_calldata).to_bytes(32, "big")
        + approve_calldata
    )
    
    user_op = _build_user_op(zero_eth_user, execute_calldata)
    user_op_hash = b"\x00" * 32
    max_cost = 10**15
    
    with boa.env.prank(entrypoint.address):
        with boa.reverts("paymaster: operation not allowed"):
            paymaster.validatePaymasterUserOp(user_op, user_op_hash, max_cost)
