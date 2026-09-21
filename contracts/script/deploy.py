import json
import os
from pathlib import Path

import boa
from dotenv import load_dotenv
from eth_account import Account

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
OUT = ROOT / "deployments"
COOLDOWN = 24 * 60 * 60

CANONICAL_ENTRYPOINT_V07 = "0x0000000071727De22E5E9d8BAf0edAc6f37da032"

ANVIL_KEYS = {
    "operator": "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
    "relayer": "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
    "alpha": "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
    "beta": "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
    "gamma": "0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
    "treasury": "0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348eac14685e0ba0",
    "generator": "0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
}


def _load(name: str, *args):
    return boa.load(str(SRC / name), *args)


def deploy(operator: str, treasury: str, generator: str, agents: list[str], cooldown: int = COOLDOWN, chain: int = 31337):
    usdc = _load("MockUSDC.vy")
    ou = _load("RevenueToken.vy", treasury)
    ctf = _load("ConditionalTokens.vy", usdc.address)
    vault = _load("FeeVault.vy", usdc.address, ou.address, cooldown)
    oracle = _load("ConsensusOracle.vy", ctf.address, operator, agents)
    exchange = _load("Exchange.vy", ctf.address, usdc.address, vault.address, operator)
    amm = _load("MarketAMM.vy", ctf.address, usdc.address, vault.address, operator)
    factory = _load(
        "MarketFactory.vy",
        ctf.address,
        oracle.address,
        amm.address,
        usdc.address,
        operator,
        generator,
    )
    if chain == 84532:
        entrypoint_addr = CANONICAL_ENTRYPOINT_V07
        entrypoint = None
    else:
        entrypoint = _load("MockEntryPoint.vy")
        entrypoint_addr = entrypoint.address
    account_impl = _load("SimpleAccount.vy")
    account_factory = _load("SimpleAccountFactory.vy", account_impl.address, entrypoint_addr)
    paymaster = _load(
        "OverUnderPaymaster.vy",
        entrypoint_addr,
        operator,
        usdc.address,
        ctf.address,
        amm.address,
        exchange.address,
        oracle.address,
        vault.address,
    )
    default_deposit = 10**18 if chain == 31337 else 5 * 10**16
    deposit_wei = int(os.getenv("PAYMASTER_DEPOSIT_WEI") or default_deposit)
    with boa.env.prank(operator):
        oracle.setFactory(factory.address)
        amm.setFactory(factory.address)
        paymaster.addFactory(account_factory.address)
        paymaster.setWeiPerUsdc(10**15)
        paymaster.deposit(value=deposit_wei)
    out = {
        "MockUSDC": usdc,
        "RevenueToken": ou,
        "ConditionalTokens": ctf,
        "FeeVault": vault,
        "ConsensusOracle": oracle,
        "Exchange": exchange,
        "MarketAMM": amm,
        "MarketFactory": factory,
        "SimpleAccount": account_impl,
        "SimpleAccountFactory": account_factory,
        "OverUnderPaymaster": paymaster,
    }
    if entrypoint is not None:
        out["MockEntryPoint"] = entrypoint
    else:
        out["EntryPoint"] = entrypoint_addr
    return out


def dump_addresses(contracts: dict, chain: int, extra=None) -> Path:
    OUT.mkdir(exist_ok=True)
    payload = {name: (c if isinstance(c, str) else c.address) for name, c in contracts.items()}
    payload["chainId"] = chain
    if extra:
        payload.update(extra)
    path = OUT / f"{chain}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def main():
    load_dotenv(ROOT.parent / ".env")
    rpc = os.getenv("ANVIL_RPC_URL", "http://127.0.0.1:8545")
    chain = int(os.getenv("CHAIN_ID", "31337"))
    usdc_override = os.getenv("USDC_ADDRESS", "").strip()

    operator = Account.from_key(os.getenv("OPERATOR_PRIVATE_KEY", ANVIL_KEYS["operator"]))
    treasury = Account.from_key(os.getenv("TREASURY_PRIVATE_KEY", ANVIL_KEYS["treasury"]))
    generator = Account.from_key(os.getenv("OPERATOR_PRIVATE_KEY", ANVIL_KEYS["generator"]))
    alpha = Account.from_key(os.getenv("AGENT_ALPHA_KEY", ANVIL_KEYS["alpha"]))
    beta = Account.from_key(os.getenv("AGENT_BETA_KEY", ANVIL_KEYS["beta"]))
    gamma = Account.from_key(os.getenv("AGENT_GAMMA_KEY", ANVIL_KEYS["gamma"]))

    try:
        boa.set_network_env(rpc)
        boa.env.add_account(operator)
        print(f"deploying via {rpc}")
    except Exception as exc:
        print(f"no rpc ({exc}); deploying in-process boa EVM")
        boa.env.set_balance(operator.address, 10**18)

    agents = [alpha.address, beta.address, gamma.address]
    contracts = deploy(operator.address, treasury.address, generator.address, agents, chain=chain)

    extra = {
        "operator": operator.address,
        "treasury": treasury.address,
        "wildcardGenerator": generator.address,
        "agents": agents,
    }
    if usdc_override and chain != 31337:
        extra["MockUSDC"] = usdc_override
        extra["canonicalUSDC"] = usdc_override
    path = dump_addresses(contracts, chain, extra)
    print(f"wrote {path}")
    for k, v in json.loads(path.read_text()).items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
