"""Copy deployed contract addresses from deployments JSON into .env."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHAIN = sys.argv[1] if len(sys.argv) > 1 else "31337"
DEPLOY = ROOT / "contracts" / "deployments" / f"{CHAIN}.json"
ENV = ROOT / ".env"

KEYS = {
    "USDC_ADDRESS": "MockUSDC",
    "CTF_ADDRESS": "ConditionalTokens",
    "FACTORY_ADDRESS": "MarketFactory",
    "EXCHANGE_ADDRESS": "Exchange",
    "AMM_ADDRESS": "MarketAMM",
    "ORACLE_ADDRESS": "ConsensusOracle",
    "FEE_VAULT_ADDRESS": "FeeVault",
    "OU_TOKEN_ADDRESS": "RevenueToken",
}


def upsert(text: str, key: str, value: str) -> str:
    pattern = rf"^{re.escape(key)}=.*$"
    line = f"{key}={value}"
    if re.search(pattern, text, flags=re.M):
        return re.sub(pattern, line, text, flags=re.M)
    if not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def main() -> None:
    if not DEPLOY.exists():
        raise SystemExit(f"missing {DEPLOY} — deploy first")
    data = json.loads(DEPLOY.read_text(encoding="utf-8"))
    env_text = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    for env_key, json_key in KEYS.items():
        value = data.get(json_key)
        if value:
            env_text = upsert(env_text, env_key, value)
    ENV.write_text(env_text, encoding="utf-8")
    print(f"updated {ENV} from {DEPLOY}")


if __name__ == "__main__":
    main()
