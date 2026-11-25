export const FAILURE_THRESHOLD = 3;
export const RECENT_RUN_MS = 10 * 60 * 1000;
const STATE_KEY = 'scheduler-health:v1';

export function credentialType(value = '') {
  const token = value.trim();
  if (token.startsWith('ghp_')) return 'classic';
  if (token.startsWith('github_pat_')) return 'fine-grained';
  return 'unknown';
}

function headers(env, authenticated) {
  return {
    Accept: 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'acgnx-cron-health',
    ...(authenticated ? { Authorization: 'Bearer ' + (env.GH_TOKEN || '').trim() } : {}),
  };
}

export async function recentRun(env, since, now) {
  const workflow = encodeURIComponent(env.WORKFLOW || 'watch.yml');
  const url = 'https://api.github.com/repos/' + env.REPO + '/actions/workflows/' + workflow + '/runs?branch=main&per_page=20';
  for (const authenticated of [true, false]) {
    try {
      const response = await fetch(url, {
        headers: headers(env, authenticated),
        signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) continue;
      const value = await response.json();
      if (!Array.isArray(value.workflow_runs)) continue;
      const cutoff = Math.max(since, now - RECENT_RUN_MS);
      const active = value.workflow_runs.find(run => {
        const created = Date.parse(run.created_at);
        return created >= cutoff && created <= now &&
          (run.conclusion === 'success' || ['queued', 'in_progress', 'waiting', 'pending', 'requested'].includes(run.status));
      });
      return active ? { status: 'active', time: active.created_at } : { status: 'missing' };
    } catch {
      continue;
    }
  }
  return { status: 'unknown' };
}

function displayTime(value) {
  if (!value) return '暂无记录';
  return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) + '（北京时间）';
}

function failureMessage(state, result, verification) {
  const reason = result.status === 401 ? 'GitHub 未通过当前令牌认证，请检查令牌有效期及触发器的 GH_TOKEN 配置。'
    : result.status === 403 ? 'GitHub 拒绝请求，请核对令牌权限及接口限流情况。'
    : result.status === 404 ? '请核对 REPO、WORKFLOW 和目标工作流是否可访问。'
    : result.status === 0 ? '请核对 GH_TOKEN、REPO 和 WORKFLOW 配置。'
    : '请查看触发器日志中的网络或接口错误。';
  return [
    '【运行提醒】新番监听定时触发连续异常',
    '连续 ' + state.failures + ' 次定时触发请求失败。',
    '最近成功触发：' + displayTime(state.last_success),
    '本次结果：' + (result.status < 0 ? '网络请求未完成' : 'HTTP ' + result.status),
    verification.status === 'missing' ? '近 10 分钟未查到新的排队、执行中或成功运行记录。'
      : '暂时无法核实 GitHub 最近运行记录，不能据此确认监听已停止。',
    reason,
    '定时器仍会每 5 分钟继续尝试；本次故障不重复提醒，恢复后通知一次。',
  ].join('\n');
}

export async function recordAttempt(env, result, scheduledTime, notify) {
  if (!env.SCHEDULER_HEALTH) {
    console.error('Scheduler health storage is not configured; no unverified alert was sent.');
    return;
  }
  const now = Number(scheduledTime);
  if (!Number.isFinite(now)) throw new Error('Invalid scheduled time');
  const key = STATE_KEY + ':' + env.REPO + ':' + (env.WORKFLOW || 'watch.yml');
  const stored = await env.SCHEDULER_HEALTH.get(key, { type: 'json', cacheTtl: 30 });
  const state = stored && typeof stored === 'object' ? stored : {};
  if (Number(state.last_attempt || 0) >= now) return;
  state.last_attempt = now;
  state.credential_type = credentialType(env.GH_TOKEN);
  state.last_status = result.status;
  const persist = () => env.SCHEDULER_HEALTH.put(key, JSON.stringify(state));

  if (result.ok) {
    state.failures = 0;
    state.first_failure = null;
    state.last_success = now;
    state.verification = null;
    await persist();
    if (state.alerted && await notify(env, [
      '【运行提醒】新番监听定时触发已恢复',
      '成功触发：' + displayTime(now),
      'GitHub 已接受本次触发请求，定时器继续每 5 分钟检查。',
    ].join('\n'))) {
      state.alerted = false;
      await persist();
    }
    return;
  }

  state.failures = Math.max(0, Number(state.failures) || 0) + 1;
  state.first_failure ||= now;
  if (state.failures < FAILURE_THRESHOLD || state.alerted) {
    await persist();
    return;
  }
  const verification = await recentRun(env, Number(state.first_failure), Math.max(now, Date.now()));
  state.verification = verification.status;
  await persist();
  if (verification.status === 'active') {
    console.log('Trigger request failed, but a recent GitHub run exists; alert deferred.');
    return;
  }
  if (await notify(env, failureMessage(state, result, verification))) {
    state.alerted = true;
    state.last_alert = now;
    await persist();
  }
}
