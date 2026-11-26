// Test 1 - saturation (closed model): a fixed number of virtual users send
// requests back to back for a fixed time. The resulting throughput is the
// maximum the configuration can serve; it is used for cost per 1,000 requests.
//
// env: BASE_URL (required), VUS (default 64), DURATION (default 90s), OUT
import { mixedRequest, summaryLine } from './workload.js';

const BASE = __ENV.BASE_URL;

export const options = {
  discardResponseBodies: true,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  scenarios: {
    saturation: {
      executor: 'constant-vus',
      vus: parseInt(__ENV.VUS || '64', 10),
      duration: __ENV.DURATION || '90s',
    },
  },
  // Always-true thresholds: they only make k6 report per-endpoint statistics.
  thresholds: {
    'http_req_duration{name:cards}': ['max>=0'],
    'http_req_duration{name:review}': ['max>=0'],
  },
};

export default function () {
  mixedRequest(BASE);
}

export function handleSummary(data) {
  return {
    [__ENV.OUT || '/results/saturation.json']: JSON.stringify(data, null, 1),
    stdout: summaryLine(data, 'saturation'),
  };
}
