#!/usr/bin/env bash
# Deploy a Cloud Run service and survive "Resource readiness deadline exceeded".
#
# Usage: infra/gcp/deploy_wait.sh SERVICE [gcloud run deploy flags...]
# Env:   GCP_REGION, GCP_PROJECT_ID (required)
#        READY_TIMEOUT_MINUTES  keep waiting this long after the deadline error (default 45)
#        RETRY_AFTER_MINUTES    redeploy once if still not Ready after this long (default 15)
#        POLL_SECONDS (default 20), PYTHON (default python3)
#        GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, RUNNER_TEMP (set by Actions)
#
# Happy path: one synchronous `gcloud run deploy`, exactly as before.
# If gcloud fails with "Resource readiness deadline exceeded" (Cloud Run gave up before
# the first instance started), keep polling that revision: its instance may still start
# late. If it is not Ready after RETRY_AFTER_MINUTES, redeploy once (--async, unique
# --revision-suffix) and accept whichever revision becomes Ready first. Any other
# Ready=False reason is a real failure and exits at once. Only revision conditions are
# printed, never env values or secrets.
set +e -uo pipefail

svc=${1:?usage: deploy_wait.sh SERVICE [deploy flags...]}
shift
: "${GCP_REGION:?GCP_REGION is required}" "${GCP_PROJECT_ID:?GCP_PROJECT_ID is required}"
timeout_min=${READY_TIMEOUT_MINUTES:-45}
retry_min=${RETRY_AFTER_MINUTES:-15}
poll=${POLL_SECONDS:-20}
log="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/deploy-${svc}.log"
loc=(--region="$GCP_REGION" --project="$GCP_PROJECT_ID")

gcloud run deploy "$svc" "$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
[ "$rc" -eq 0 ] && exit 0
if ! grep -qi 'readiness deadline exceeded' "$log"; then
  echo "ERROR: $svc: gcloud run deploy failed (exit $rc). Fix: see the Diagnose step and infra/gcp/README.md."
  exit "$rc"
fi

first=$(gcloud run services describe "$svc" "${loc[@]}" --format='value(status.latestCreatedRevisionName)' 2>/dev/null)
echo "::warning::$svc: Cloud Run reported 'Resource readiness deadline exceeded' for ${first:-the new revision}. Waiting up to ${timeout_min} min (one redeploy after ${retry_min} min)."

# Prints "Status|Reason|Message" for the revision's Ready condition (Unknown if absent).
ready_line() {
  gcloud run revisions describe "$1" "${loc[@]}" --format=json 2>/dev/null | "${PYTHON:-python3}" -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("Unknown||")
    sys.exit(0)
conds = (d.get("status") or {}).get("conditions") or []
c = next((c for c in conds if c.get("type") == "Ready"), {})
clean = lambda v: " ".join(str(v or "").replace("|", " ").split())
print("%s|%s|%s" % (c.get("status") or "Unknown", clean(c.get("reason")), clean(c.get("message"))))
'
}

redeploy() {
  local suffix="retry-${GITHUB_RUN_ID:-0}-${GITHUB_RUN_ATTEMPT:-1}"
  echo "::warning::$svc: still not Ready after ${retry_min} min; redeploying once as ${svc}-${suffix}."
  gcloud run deploy "$svc" "$@" --revision-suffix="$suffix" --async 2>&1 | tee -a "$log"
  if [ "${PIPESTATUS[0]}" -ne 0 ]; then
    echo "ERROR: $svc: the retry deploy request was rejected. Fix: see the Diagnose step."
    exit 1
  fi
  revs+=("${svc}-${suffix}")
}

revs=()
[ -n "$first" ] && revs+=("$first")
start=$(date +%s)
deadline=$(( start + timeout_min * 60 ))
retry_at=$(( start + retry_min * 60 ))
retried=0
[ "${#revs[@]}" -eq 0 ] && { redeploy "$@"; retried=1; }
last_report=0
while :; do
  terminal=0
  for rev in "${revs[@]}"; do
    IFS='|' read -r st reason msg <<<"$(ready_line "$rev")"
    case "${st:-Unknown}" in
      True)
        echo "READY: $rev"
        if ! gcloud run services update-traffic "$svc" --to-latest "${loc[@]}" --quiet >/dev/null 2>&1; then
          echo "::warning::$svc: update-traffic --to-latest failed; confirm with: gcloud run services describe $svc --region=$GCP_REGION --project=$GCP_PROJECT_ID --format='value(status.traffic)'"
        fi
        exit 0
        ;;
      False)
        if ! grep -qi 'readiness deadline' <<<"$msg"; then
          echo "  $rev: Ready=False ${reason:-} ${msg:-}"
          terminal=$((terminal + 1))
        fi
        ;;
    esac
  done
  if [ "$terminal" -eq "${#revs[@]}" ]; then
    echo "ERROR: $svc: revision failed with a non-deadline error (above). Fix: see the Diagnose step."
    exit 1
  fi
  now=$(date +%s)
  if [ "$now" -ge "$deadline" ]; then
    echo "ERROR: $svc: no revision became Ready within ${timeout_min} min. Fix: see infra/gcp/README.md (Readiness deadline exceeded)."
    exit 1
  fi
  if [ "$retried" -eq 0 ] && [ "$now" -ge "$retry_at" ]; then
    redeploy "$@"
    retried=1
  fi
  if [ $((now - last_report)) -ge 120 ]; then
    echo "waiting for ${revs[*]}: $(( (deadline - now) / 60 )) min left"
    last_report=$now
  fi
  sleep "$poll"
done
