import boa
from eth_account import Account

COOLDOWN = 24 * 60 * 60


def chain_id() -> int:
    env = boa.env
    for attr in ("chain_id",):
        if hasattr(env, attr):
            return int(getattr(env, attr))
    evm = getattr(env, "evm", None)
    if evm is not None:
        patch = getattr(evm, "patch", None)
        if patch is not None and hasattr(patch, "chain_id"):
            return int(patch.chain_id)
        chain = getattr(evm, "chain", None)
        if chain is not None and hasattr(chain, "chain_id"):
            return int(chain.chain_id)
    return 1


def deploy_protocol():
    operator_acct = Account.create()
    treasury_acct = Account.create()
    generator_acct = Account.create()
    alpha = Account.create()
    beta = Account.create()
    gamma = Account.create()
    trader_a = Account.create()
    trader_b = Account.create()

    accounts = {
        "operator": operator_acct,
        "treasury": treasury_acct,
        "generator": generator_acct,
        "alpha": alpha,
        "beta": beta,
        "gamma": gamma,
        "trader_a": trader_a,
        "trader_b": trader_b,
    }
    for acct in accounts.values():
        boa.env.set_balance(acct.address, 10**18)

    operator = operator_acct.address
    treasury = treasury_acct.address
    generator = generator_acct.address
    agents = [alpha.address, beta.address, gamma.address]

    usdc = boa.load("src/MockUSDC.vy")
    ou = boa.load("src/RevenueToken.vy", treasury)
    ctf = boa.load("src/ConditionalTokens.vy", usdc.address)
    vault = boa.load("src/FeeVault.vy", usdc.address, ou.address, COOLDOWN)
    oracle = boa.load("src/ConsensusOracle.vy", ctf.address, operator, agents)
    exchange = boa.load("src/Exchange.vy", ctf.address, usdc.address, vault.address, operator)
    amm = boa.load("src/MarketAMM.vy", ctf.address, usdc.address, vault.address, operator)
    factory = boa.load(
        "src/MarketFactory.vy",
        ctf.address,
        oracle.address,
        amm.address,
        usdc.address,
        operator,
        generator,
    )
    entrypoint = boa.load("src/MockEntryPoint.vy")
    paymaster = boa.load(
        "src/OverUnderPaymaster.vy",
        entrypoint.address,
        operator,
        usdc.address,
        ctf.address,
        amm.address,
        exchange.address,
        oracle.address,
        vault.address,
    )
    with boa.env.prank(operator):
        oracle.setFactory(factory.address)
        amm.setFactory(factory.address)
        # Fund paymaster with 1 ETH for testing
        entrypoint.depositTo(paymaster.address, value=10**18)
        # Add test accounts to paymaster's allowed senders
        # In production, would use factory-deployed AA accounts
        for acct in [trader_a, trader_b]:
            paymaster.addSender(acct.address)
    
    return {
        "usdc": usdc,
        "ou": ou,
        "ctf": ctf,
        "vault": vault,
        "oracle": oracle,
        "exchange": exchange,
        "amm": amm,
        "factory": factory,
        "entrypoint": entrypoint,
        "paymaster": paymaster,
        "accounts": accounts,
        "chain_id": chain_id(),
    }
