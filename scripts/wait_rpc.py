"""Wait until Anvil JSON-RPC answers on 127.0.0.1:8545."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8545"
BODY = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []}).encode()


def ready() -> bool:
    req = urllib.request.Request(URL, data=BODY, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main() -> None:
    for _ in range(60):
        if ready():
            print(f"rpc ready: {URL}")
            return
        time.sleep(1)
    raise SystemExit(f"timeout waiting for {URL}")


if __name__ == "__main__":
    main()
