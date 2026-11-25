import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { wake } from './worker.js';
import { recordAttempt, credentialType } from './scheduler-health.js';

const start = Date.parse('2026-11-25T05:00:00Z');
const step = 5 * 60 * 1000;
const bad = { ok: false, status: 401, body: 'Bad credentials' };
const good = { ok: true, status: 204, body: '' };

function fixture() {
  const values = new Map();
  const messages = [];
  const env = {
    REPO: 'owner/repo', WORKFLOW: 'watch.yml', GH_TOKEN: 'ghp_fake_test_only',
    FEISHU_WEBHOOK: 'https://notify.invalid/hook',
    SCHEDULER_HEALTH: {
      async get(key) { return values.has(key) ? JSON.parse(values.get(key)) : null; },
      async put(key, value) { values.set(key, value); },
    },
  };
  const notify = async (_env, text) => { messages.push(text); return true; };
  return { env, values, messages, notify, state: () => JSON.parse([...values.values()][0]) };
}

function runs(items) {
  return Response.json({ workflow_runs: items });
}

function fakeClock(t) {
  t.mock.method(Date, 'now', () => start);
}

async function three(f, notify = f.notify) {
  for (let i = 0; i < 3; i++) await recordAttempt(f.env, bad, start + i * step, notify);
}

test('classic and fine-grained token types are distinguished without exposing the value', () => {
  assert.equal(credentialType('  ghp_test  '), 'classic');
  assert.equal(credentialType('github_pat_test'), 'fine-grained');
  assert.equal(credentialType(''), 'unknown');
});

test('the 07:00 one-off failure followed by success stays silent across reloads', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', () => { throw new Error('Should not inspect runs before threshold'); });
  const f = fixture();
  await recordAttempt(f.env, bad, start, f.notify);
  assert.equal(f.state().failures, 1);
  await recordAttempt({ ...f.env }, good, start + step, f.notify);
  assert.equal(f.state().failures, 0);
  assert.equal(f.messages.length, 0);
});

test('three consecutive failures alert once, persistent failure stays silent, recovery alerts once', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', async () => runs([]));
  const f = fixture();
  await three(f);
  await recordAttempt(f.env, bad, start + 3 * step, f.notify);
  assert.equal(f.messages.length, 1);
  assert.match(f.messages[0], /连续 3 次/);
  assert.match(f.messages[0], /每 5 分钟继续尝试/);
  assert.doesNotMatch(f.messages[0], /不会再按|Actions: Read and write/);
  await recordAttempt(f.env, good, start + 4 * step, f.notify);
  await recordAttempt(f.env, good, start + 5 * step, f.notify);
  assert.equal(f.messages.length, 2);
  assert.match(f.messages[1], /已恢复/);
});

test('recent queued, running and successful work each prevent a false stop warning', async t => {
  fakeClock(t);
  for (const run of [
    { status: 'queued', conclusion: null },
    { status: 'in_progress', conclusion: null },
    { status: 'completed', conclusion: 'success' },
  ]) {
    const f = fixture();
    t.mock.method(globalThis, 'fetch', async () => runs([{ ...run, created_at: new Date(start + step).toISOString() }]));
    await three(f);
    assert.equal(f.messages.length, 0);
    assert.equal(f.state().verification, 'active');
  }
});

test('an expired credential can still verify the public repository without authorization', async t => {
  fakeClock(t);
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    calls.push(options.headers);
    return options.headers.Authorization ? new Response('', { status: 401 })
      : runs([{ status: 'queued', created_at: new Date(start + step).toISOString() }]);
  });
  const f = fixture();
  await three(f);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].Authorization, undefined);
  assert.equal(f.messages.length, 0);
});

test('inaccessible run history is reported as unverified rather than as stopped', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', async () => { throw new Error('network'); });
  const f = fixture();
  await three(f);
  assert.equal(f.messages.length, 1);
  assert.match(f.messages[0], /无法核实/);
  assert.match(f.messages[0], /不能据此确认监听已停止/);
});

test('old or failed workflow runs do not conceal a real persistent trigger failure', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', async () => runs([
    { status: 'completed', conclusion: 'success', created_at: new Date(start - step).toISOString() },
    { status: 'completed', conclusion: 'failure', created_at: new Date(start + step).toISOString() },
  ]));
  const f = fixture();
  await three(f);
  assert.equal(f.messages.length, 1);
  assert.equal(f.state().verification, 'missing');
});

test('recent fallback coverage postpones an alert but does not hide a subsequent outage', async t => {
  fakeClock(t);
  let active = true;
  t.mock.method(globalThis, 'fetch', async () => runs(active
    ? [{ status: 'queued', created_at: new Date(start + step).toISOString() }] : []));
  const f = fixture();
  await three(f);
  assert.equal(f.messages.length, 0);
  active = false;
  await recordAttempt(f.env, bad, start + 3 * step, f.notify);
  assert.equal(f.messages.length, 1);
});

test('replayed or out-of-order scheduled events do not increase failures', async t => {
  fakeClock(t);
  const f = fixture();
  await recordAttempt(f.env, bad, start, f.notify);
  await recordAttempt(f.env, bad, start, f.notify);
  await recordAttempt(f.env, bad, start - step, f.notify);
  assert.equal(f.state().failures, 1);
});

test('only token type is stored, never credentials or response contents', async t => {
  fakeClock(t);
  const f = fixture();
  await recordAttempt(f.env, { ...bad, body: f.env.GH_TOKEN }, start, f.notify);
  const stored = [...f.values.values()].join('');
  assert.match(stored, /"credential_type":"classic"/);
  assert.ok(!stored.includes(f.env.GH_TOKEN));
  assert.ok(!stored.includes(f.env.FEISHU_WEBHOOK));
});

test('a failed failure notification is retried instead of being marked as delivered', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', async () => runs([]));
  const f = fixture();
  await three(f, async () => false);
  assert.ok(!f.state().alerted);
  await recordAttempt(f.env, bad, start + 3 * step, f.notify);
  assert.equal(f.messages.length, 1);
  assert.equal(f.state().alerted, true);
});

test('a failed recovery notification is retried on the next successful dispatch', async t => {
  fakeClock(t);
  t.mock.method(globalThis, 'fetch', async () => runs([]));
  const f = fixture();
  await three(f);
  await recordAttempt(f.env, good, start + 3 * step, async () => false);
  assert.equal(f.state().alerted, true);
  await recordAttempt(f.env, good, start + 4 * step, f.notify);
  assert.equal(f.state().alerted, false);
  assert.equal(f.messages.length, 2);
});

test('an empty 204 response is a successful dispatch and copied whitespace is trimmed', async t => {
  const f = fixture();
  f.env.GH_TOKEN = ' ghp_fake_test_only\n';
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    assert.equal(options.headers.Authorization, 'Bearer ghp_fake_test_only');
    assert.ok(options.signal);
    return new Response(null, { status: 204 });
  });
  assert.equal((await wake(f.env)).ok, true);
});

test('manual trigger failures cannot create scheduled incident counters or alerts', async t => {
  const f = fixture();
  f.env.SECRET = 'test-gate';
  t.mock.method(globalThis, 'fetch', async () => new Response('', { status: 401 }));
  assert.equal((await worker.fetch(new Request('https://worker.invalid/?k=test-gate'), f.env)).status, 200);
  assert.equal(f.values.size, 0);
  assert.equal(f.messages.length, 0);
});

test('ping, callback verification, and protected manual access retain their behavior', async t => {
  const f = fixture();
  f.env.SECRET = 'test-gate';
  t.mock.method(globalThis, 'fetch', () => { throw new Error('Unexpected outbound call'); });
  assert.equal((await worker.fetch(new Request('https://worker.invalid/ping'), f.env)).status, 200);
  assert.equal((await worker.fetch(new Request('https://worker.invalid/'), f.env)).status, 403);
  const response = await worker.fetch(new Request('https://worker.invalid/feishu', {
    method: 'POST', body: JSON.stringify({ challenge: 'test-challenge' }),
  }), f.env);
  assert.deepEqual(await response.json(), { challenge: 'test-challenge' });
});

test('the deployed scheduled handler uses persistent state and checks runs before notifying', async t => {
  fakeClock(t);
  const f = fixture();
  let notifications = 0;
  t.mock.method(globalThis, 'fetch', async (url, options) => {
    if (String(url).includes('/dispatches')) return new Response('', { status: 401 });
    if (String(url).includes('/runs?')) return runs([]);
    if (url === f.env.FEISHU_WEBHOOK) {
      notifications++;
      assert.match(JSON.parse(options.body).content.text, /连续 3 次/);
      return Response.json({ code: 0 });
    }
    throw new Error('Unexpected URL');
  });
  for (let i = 0; i < 4; i++) await worker.scheduled({ cron: '*/5 * * * *', scheduledTime: start + i * step }, f.env);
  assert.equal(notifications, 1);
});
