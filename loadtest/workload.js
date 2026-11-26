// Shared request mix used by both tests.
// 70% GET due cards (database read + JSON), 30% POST review (CPU-bound grading).
import http from 'k6/http';
import { check } from 'k6';

const N_DECKS = 500;
const N_CARDS = 50000;
const READ_SHARE = 0.7;
const WORDS = ('cloud compute storage network latency throughput region zone replica ' +
  'container kernel hypervisor scaling balancer cache queue function api database ' +
  'index query backup snapshot recovery policy encryption key tenant billing quota ' +
  'instance volume bucket object block file subnet gateway firewall certificate token ' +
  'session request response worker').split(' ');
const PARAMS_GET = { tags: { name: 'cards' }, timeout: '10s' };
const PARAMS_POST = {
  headers: { 'Content-Type': 'application/json' },
  tags: { name: 'review' },
  timeout: '10s',
};

function randInt(n) {
  return 1 + Math.floor(Math.random() * n);
}

function typedAnswer() {
  const w = [];
  for (let i = 0; i < 18; i++) w.push(WORDS[Math.floor(Math.random() * WORDS.length)]);
  return w.join(' ');
}

export function mixedRequest(base) {
  if (Math.random() < READ_SHARE) {
    const res = http.get(`${base}/api/decks/${randInt(N_DECKS)}/cards?limit=20`, PARAMS_GET);
    check(res, { 'cards status 200': (r) => r.status === 200 });
  } else {
    const body = JSON.stringify({ card_id: randInt(N_CARDS), answer: typedAnswer() });
    const res = http.post(`${base}/api/reviews`, body, PARAMS_POST);
    check(res, { 'review status 200': (r) => r.status === 200 });
  }
}

export function summaryLine(data, label) {
  const m = data.metrics;
  const rps = m.http_reqs ? m.http_reqs.values.rate : 0;
  const p95 = m.http_req_duration ? m.http_req_duration.values['p(95)'] : 0;
  const fail = m.http_req_failed ? m.http_req_failed.values.rate : 0;
  return `${label}: ${rps.toFixed(1)} req/s, p95 ${p95.toFixed(1)} ms, failed ${(fail * 100).toFixed(2)}%\n`;
}
