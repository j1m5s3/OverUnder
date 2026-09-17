"""Stop the OverUnder local stack by port and docker compose (window titles are unreliable)."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "infra" / "docker-compose.yml"

# API, Next.js, Anvil. Postgres is stopped via compose if we started it.
PORTS = (8000, 3000, 8545)

KILLABLE = {
    "python",
    "python.exe",
    "uvicorn",
    "node",
    "node.exe",
    "npm",
    "npm.cmd",
    "anvil",
    "anvil.exe",
    "cmd",
    "cmd.exe",
    "powershell",
    "powershell.exe",
    "pwsh",
    "pwsh.exe",
}

STOP_WALK = {
    "explorer.exe",
    "services.exe",
    "svchost.exe",
    "wininit.exe",
    "dwm.exe",
    "csrss.exe",
    "system",
    "idle",
    "com.docker.backend.exe",
    "docker.exe",
    "dockerd.exe",
}


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, text=True, capture_output=True, **kwargs)


def listening_pids(port: int) -> set[int]:
    proc = _run(["netstat", "-ano"])
    pids: set[int] = set()
    suffix = f":{port}"
    for line in proc.stdout.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[1]
        if local.endswith(suffix):
            try:
                pids.add(int(parts[-1]))
            except ValueError:
                continue
    return pids


def process_name(pid: int) -> str:
    proc = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
    line = proc.stdout.strip().splitlines()
    if not line or "No tasks" in line[0]:
        return ""
    # "python.exe","1234","Console","1","50,000 K"
    name = line[0].split(",")[0].strip().strip('"')
    return name


def parent_pid(pid: int) -> int | None:
    proc = _run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').ParentProcessId",
        ]
    )
    text = (proc.stdout or "").strip()
    try:
        ppid = int(text)
    except ValueError:
        return None
    if ppid <= 0:
        return None
    return ppid


def kill_pid(pid: int) -> None:
    print(f"  taskkill PID {pid} ({process_name(pid) or 'unknown'})")
    subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True)


def kill_chain(pid: int) -> None:
    seen: set[int] = set()
    current: int | None = pid
    while current and current not in seen:
        seen.add(current)
        name = process_name(current).lower()
        if not name or name in STOP_WALK:
            break
        parent = parent_pid(current)
        if name in KILLABLE or current == pid:
            try:
                kill_pid(current)
            except OSError:
                pass
        if name in {"cmd.exe", "cmd", "powershell.exe", "pwsh.exe"}:
            break
        current = parent


def stop_docker() -> None:
    docker = _run(["where", "docker"])
    if docker.returncode != 0:
        return
    ping = _run(["docker", "info"])
    if ping.returncode != 0:
        print("  docker daemon not running — skip compose stop")
        return
    print("  docker compose stop (infra)")
    proc = _run(
        ["docker", "compose", "-f", str(COMPOSE), "stop"],
        cwd=str(ROOT),
    )
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.returncode != 0 and proc.stderr.strip():
        print(proc.stderr.rstrip())


def remaining(port: int) -> set[int]:
    return listening_pids(port)


def main() -> int:
    print("Stopping OverUnder stack...")
    stop_docker()
    time.sleep(0.5)

    killed_any = False
    for port in PORTS:
        pids = listening_pids(port)
        if not pids:
            print(f"  :{port} already free")
            continue
        print(f"  :{port} pids {sorted(pids)}")
        for pid in sorted(pids):
            name = process_name(pid).lower()
            if "docker" in name or name in STOP_WALK:
                print(f"  skip PID {pid} ({name}) — use docker compose")
                continue
            kill_chain(pid)
            killed_any = True

    time.sleep(0.8)
    leftover = {port: remaining(port) for port in PORTS}
    busy = {p: ids for p, ids in leftover.items() if ids}
    if busy:
        print("Still listening:")
        for port, ids in busy.items():
            names = ", ".join(f"{pid}/{process_name(pid) or '?'}" for pid in sorted(ids))
            print(f"  :{port} {names}")
        return 1
    print("Ports 8000, 3000, 8545 are free.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
