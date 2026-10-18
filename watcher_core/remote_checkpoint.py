"""Persist outbox/receipts remotely before proceeding to the next send."""
import os
from pathlib import Path
import subprocess
import time


def git(args, deadline=None):
    remaining = 25 if deadline is None else min(25, deadline - time.monotonic())
    if remaining <= 0:
        raise RuntimeError('远端检查点超时，已停止后续发送')
    result = subprocess.run(['git', *args], capture_output=True, text=True, timeout=remaining)
    if result.returncode:
        # Never echo credential-bearing remotes or raw transport errors.
        raise RuntimeError('远端状态检查点失败，已停止后续发送；请检查仓库写入与网络状态')
    return result.stdout.strip()


def publish():
    if os.environ.get('ACGNX_REMOTE_CHECKPOINT') != '1':
        return
    deadline = time.monotonic() + 45
    def run(args):
        return git(args, deadline)
    if os.environ.get('GITHUB_ACTIONS') != 'true' or run(['branch', '--show-current']) != 'main':
        raise RuntimeError('远端检查点仅允许在 Actions 的 main 检出中执行')
    paths = [p for p in ('config.json', 'state.json', 'state.backup.json') if Path(p).is_file()]
    run(['config', 'user.name', 'acgnx-bot'])
    run(['config', 'user.email', 'acgnx-bot@users.noreply.github.com'])
    if run(['status', '--porcelain', '--', *paths]):
        run(['add', '--', *paths])
        run(['commit', '-m', '保存通知检查点 [skip ci]', '--', *paths])
    # A prior failed push may have left a clean worktree but unpushed commits.
    run(['fetch', 'origin', 'main', '-q'])
    if run(['rev-parse', 'HEAD']) == run(['rev-parse', 'origin/main']):
        return
    if run(['rev-list', '--count', 'HEAD..origin/main']) != '0':
        run(['pull', '--rebase', '--autostash', 'origin', 'main'])
    run(['push', 'origin', 'HEAD:main'])
