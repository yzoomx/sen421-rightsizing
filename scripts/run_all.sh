#!/usr/bin/env bash
# Benchmark runner - executed ON THE LOAD GENERATOR inside Hetzner's private network.
#
# For every repetition (RUNS, default 3) and every configuration in targets.txt:
#   1. 15 s warm-up (discarded)
#   2. Test 1: saturation, 64 virtual users for 90 s       -> max throughput
#   3. Test 2: stepped load at 20..110 % of that throughput -> capacity under the SLO
# CPU counters (/proc/stat, incl. steal time) are read from every backend server
# and from the load generator before and after each test.
# The order of configurations is rotated in each repetition, so no configuration
# always runs first or last (reduces time-of-day and noisy-neighbour bias).
#
# Usage:  nohup /opt/bench/run_all.sh > /opt/bench/run.log 2>&1 &
#         tail -f /opt/bench/run.log
set -uo pipefail

BENCH=${BENCH:-/opt/bench}
RES=$BENCH/results
TARGETS=$BENCH/targets.txt          # name|server_type|count|entry_url|backend_urls
RUNS=${RUNS:-3}
SAT_VUS=${SAT_VUS:-64}
SAT_DURATION=${SAT_DURATION:-90s}
STEP_SECONDS=${STEP_SECONDS:-15}
GAP_SECONDS=${GAP_SECONDS:-5}
SLO_P95_MS=${SLO_P95_MS:-100}
STEP_FRACTIONS=${STEP_FRACTIONS:-"0.2 0.4 0.6 0.7 0.8 0.9 1.0 1.1"}
COOLDOWN=${COOLDOWN:-30}
K6_IMAGE=${K6_IMAGE:-grafana/k6:latest}

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

k6run() {  # k6run <script> <out-file-inside-results> [ENV=VALUE ...]
  local script=$1 out=$2; shift 2
  local envs=(-e "OUT=/results/$out")
  for kv in "$@"; do envs+=(-e "$kv"); done
  docker run --rm --network host -u 0 \
    -v "$BENCH/loadtest:/scripts:ro" -v "$RES:/results" \
    "${envs[@]}" "$K6_IMAGE" run --quiet "/scripts/$script" || true
}

cpu_snapshot() {  # cpu_snapshot <config-dir> <label> <backend_urls_csv>
  local dir=$1 label=$2 backends=$3 i=0
  for b in ${backends//,/ }; do
    i=$((i + 1))
    curl -sf --max-time 5 "$b/internal/cpu" > "$RES/$dir/cpu_${label}_backend${i}.json" || echo '{}' > "$RES/$dir/cpu_${label}_backend${i}.json"
  done
  awk '/^cpu /{printf "{\"nproc\":%d,\"user\":%s,\"nice\":%s,\"system\":%s,\"idle\":%s,\"iowait\":%s,\"irq\":%s,\"softirq\":%s,\"steal\":%s}\n", '"$(nproc)"', $2,$3,$4,$5,$6,$7,$8,$9}' /proc/stat > "$RES/$dir/cpu_${label}_loadgen.json"
}

wait_healthy() {  # wait_healthy <url>  (max 15 minutes)
  local url=$1
  for _ in $(seq 1 180); do
    curl -sf --max-time 3 "$url/health" > /dev/null && return 0
    sleep 5
  done
  log "ERROR: $url did not become healthy"; return 1
}

mkdir -p "$RES"
mapfile -t LINES < <(grep -v '^\s*$' "$TARGETS")
N=${#LINES[@]}

log "Pulling $K6_IMAGE"
docker pull -q "$K6_IMAGE" > /dev/null
{
  echo "{"
  echo "  \"started_utc\": \"$(date -u +%FT%TZ)\","
  echo "  \"k6_version\": \"$(docker run --rm "$K6_IMAGE" version | head -1 | tr -d '"')\","
  echo "  \"k6_image_digest\": \"$(docker image inspect --format '{{index .RepoDigests 0}}' "$K6_IMAGE")\","
  echo "  \"loadgen_nproc\": $(nproc),"
  echo "  \"loadgen_kernel\": \"$(uname -r)\","
  echo "  \"runs\": $RUNS, \"sat_vus\": $SAT_VUS, \"sat_duration\": \"$SAT_DURATION\","
  echo "  \"step_seconds\": $STEP_SECONDS, \"gap_seconds\": $GAP_SECONDS, \"slo_p95_ms\": $SLO_P95_MS,"
  echo "  \"step_fractions\": \"$STEP_FRACTIONS\", \"cooldown_s\": $COOLDOWN"
  echo "}"
} > "$RES/meta.json"
cp "$TARGETS" "$RES/targets.txt"

log "Waiting for all targets to be healthy"
for line in "${LINES[@]}"; do
  IFS='|' read -r name type count url backends <<< "$line"
  wait_healthy "$url" || exit 1
  for b in ${backends//,/ }; do wait_healthy "$b" || exit 1; done
  mkdir -p "$RES/$name"
  log "  $name ($count x $type) ready at $url"
done

for run in $(seq 1 "$RUNS"); do
  for k in $(seq 0 $((N - 1))); do
    line=${LINES[$(( (k + run - 1) % N ))]}       # rotate the order every run
    IFS='|' read -r name type count url backends <<< "$line"
    log "=== run $run/$RUNS - $name ($count x $type) ==="

    k6run saturation.js "$name/run${run}_warmup.json" "BASE_URL=$url" "VUS=$SAT_VUS" "DURATION=15s" > /dev/null
    sleep 5

    cpu_snapshot "$name" "run${run}_sat_before" "$backends"
    k6run saturation.js "$name/run${run}_sat.json" "BASE_URL=$url" "VUS=$SAT_VUS" "DURATION=$SAT_DURATION"
    cpu_snapshot "$name" "run${run}_sat_after" "$backends"

    rps=$(jq -r '.metrics.http_reqs.values.rate // 0' "$RES/$name/run${run}_sat.json" 2>/dev/null || echo 0)
    if ! awk -v x="$rps" 'BEGIN{exit !(x > 0)}'; then
      log "WARNING: no throughput found in $name/run${run}_sat.json - check that file"
    fi
    steps=$(awk -v x="$rps" -v f="$STEP_FRACTIONS" 'BEGIN{n=split(f,a," "); s=""; for(i=1;i<=n;i++){r=int(x*a[i]+0.5); if(r<1)r=1; s=s (i>1?",":"") r}; print s}')
    sleep "$COOLDOWN"

    cpu_snapshot "$name" "run${run}_step_before" "$backends"
    k6run step.js "$name/run${run}_step.json" "BASE_URL=$url" "STEPS=$steps" \
      "STEP_SECONDS=$STEP_SECONDS" "GAP_SECONDS=$GAP_SECONDS" "SLO_P95_MS=$SLO_P95_MS"
    cpu_snapshot "$name" "run${run}_step_after" "$backends"
    sleep "$COOLDOWN"
  done
done

echo "\"finished_utc\": \"$(date -u +%FT%TZ)\"" > "$RES/finished.txt"
tar czf "$BENCH/results.tgz" -C "$BENCH" results
log "DONE - results in $RES and $BENCH/results.tgz"
