"""Single-flight guard for the oracle Cloud Run Job.

The job runs */15 with a task timeout of OU_TICK_BUDGET_SECONDS + 60s (840s by
default; deploy-gcp.yml), which ends a tick before the next one is scheduled. An
execution can still overlap (a manual `gcloud run jobs execute`, a scheduler
retry, or an older deploy with a longer timeout), and both would send from the
operator EOA (nonce collisions, duplicate consensus/attestation txs). Before any
stage, this lists the job's executions through the Cloud Run Admin API and
reports "held" when an OLDER execution is still running; run_tick then exits 0
with {"skipped": "running"}.

Only active on Cloud Run (CLOUD_RUN_JOB and CLOUD_RUN_EXECUTION set) unless
OU_TICK_SINGLE_FLIGHT=0. The job's service account needs run.executions.list /
run.executions.get (roles/run.viewer on the job). Any API error raises, and
run_tick fails open (runs the tick), which is the pre-guard behaviour.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import httpx

METADATA = "http://metadata.google.internal/computeMetadata/v1"
RUN_API = "https://run.googleapis.com/v2"
MAX_PAGES = 10


def enabled() -> bool:
    if os.getenv("OU_TICK_SINGLE_FLIGHT", "1").strip() == "0":
        return False
    return bool(os.getenv("CLOUD_RUN_JOB", "").strip() and os.getenv("CLOUD_RUN_EXECUTION", "").strip())


def _metadata(http: httpx.Client, path: str) -> httpx.Response:
    response = http.get(f"{METADATA}/{path}", headers={"Metadata-Flavor": "Google"})
    response.raise_for_status()
    return response


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    # RFC 3339 may carry nanoseconds; fromisoformat takes at most microseconds.
    if "." in text:
        head, tail = text.split(".", 1)
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits):]
        text = f"{head}.{digits[:6]}{zone}"
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def older_running(executions: list[dict], mine: dict) -> list[str]:
    """Names of executions created before `mine` that have not completed."""
    my_name = mine.get("name")
    my_created = _ts(mine.get("createTime"))
    out = []
    for execution in executions:
        if execution.get("name") == my_name or execution.get("completionTime"):
            continue
        created = _ts(execution.get("createTime"))
        if created is not None and my_created is not None and created < my_created:
            out.append(str(execution.get("name") or ""))
    return out


def cloud_run_lease(client: httpx.Client | None = None) -> dict:
    """{"acquired": bool, "holder": [...]} for this execution; raises on API errors."""
    if not enabled():
        return {"acquired": True, "mode": "off"}
    job = os.environ["CLOUD_RUN_JOB"].strip()
    execution = os.environ["CLOUD_RUN_EXECUTION"].strip()
    own = client is None
    http = client or httpx.Client(timeout=15)
    try:
        project = _metadata(http, "project/project-id").text.strip()
        region = _metadata(http, "instance/region").text.strip().rsplit("/", 1)[-1]
        token = _metadata(http, "instance/service-account/token").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        parent = f"{RUN_API}/projects/{project}/locations/{region}/jobs/{job}/executions"
        response = http.get(f"{parent}/{execution}", headers=headers)
        response.raise_for_status()
        mine = response.json()
        executions: list[dict] = []
        page = None
        for _ in range(MAX_PAGES):
            params = {"pageSize": 100, **({"pageToken": page} if page else {})}
            response = http.get(parent, headers=headers, params=params)
            response.raise_for_status()
            body = response.json()
            executions.extend(body.get("executions") or [])
            page = body.get("nextPageToken")
            if not page:
                break
        holders = older_running(executions, mine)
        return {"acquired": not holders, "mode": "cloud-run", "holder": holders}
    finally:
        if own:
            http.close()
