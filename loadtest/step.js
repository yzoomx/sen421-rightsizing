// Test 2 - stepped load (open model): requests arrive at a fixed rate that
// rises in steps, independent of how fast the server answers. For every step
// we record p95 latency, errors and dropped requests, so we can find the
// highest rate that still meets the service level objective (SLO).
//
// env: BASE_URL (required), STEPS (comma-separated req/s, e.g. "100,200,300"),
//      STEP_SECONDS (default 15), GAP_SECONDS (default 5),
//      SLO_P95_MS (default 100), OUT
import { mixedRequest, summaryLine } from './workload.js';

const BASE = __ENV.BASE_URL;
const STEPS = (__ENV.STEPS || '50,100').split(',').map((s) => parseInt(s, 10));
const STEP_S = parseInt(__ENV.STEP_SECONDS || '15', 10);
const GAP_S = parseInt(__ENV.GAP_SECONDS || '5', 10);
const SLO = parseInt(__ENV.SLO_P95_MS || '100', 10);

const scenarios = {};
const thresholds = {};
STEPS.forEach((rate, i) => {
  const name = `step_${i + 1}`;
  scenarios[name] = {
    executor: 'constant-arrival-rate',
    exec: 'run',
    rate: rate,
    timeUnit: '1s',
    duration: `${STEP_S}s`,
    startTime: `${i * (STEP_S + GAP_S)}s`,
    preAllocatedVUs: 40,
    maxVUs: 400,
    gracefulStop: `${GAP_S}s`,
    tags: { step_rate: String(rate) },
  };
  // SLO thresholds per step (a failed threshold does not stop the test).
  thresholds[`http_req_duration{scenario:${name}}`] = [`p(95)<${SLO}`];
  thresholds[`http_req_failed{scenario:${name}}`] = ['rate<0.01'];
  // Always-true thresholds so the per-step counters appear in the summary.
  thresholds[`http_reqs{scenario:${name}}`] = ['count>=0'];
  thresholds[`dropped_iterations{scenario:${name}}`] = ['count>=0'];
});

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: scenarios,
  thresholds: thresholds,
};

export function run() {
  mixedRequest(BASE);
}

export function handleSummary(data) {
  data.step_config = { steps: STEPS, step_seconds: STEP_S, gap_seconds: GAP_S, slo_p95_ms: SLO };
  return {
    [__ENV.OUT || '/results/step.json']: JSON.stringify(data, null, 1),
    stdout: summaryLine(data, `step test (${STEPS.join(',')} req/s)`),
  };
}
