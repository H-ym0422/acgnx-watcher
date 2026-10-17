// Store GH_TOKEN with `wrangler secret put GH_TOKEN`; never commit it.
async function dispatch(env) {
  if (!/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(env.REPO || '')) {
    throw new Error('REPO must be owner/repository');
  }
  const response = await fetch(`https://api.github.com/repos/${env.REPO}/actions/workflows/watch.yml/dispatches`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${env.GH_TOKEN}`,
      Accept: 'application/vnd.github+json',
      'Content-Type': 'application/json',
      'User-Agent': 'acgnx-watcher-cron',
    },
    body: JSON.stringify({ ref: 'main' }),
  });
  if (!response.ok) throw new Error(`GitHub dispatch failed: HTTP ${response.status}`);
}

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  async fetch() {
    // No public endpoint that can trigger workflows or expose credentials.
    return new Response('ACGNX scheduler: use the cron trigger.', { status: 200 });
  },
};
