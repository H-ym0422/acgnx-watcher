"""Issue commands with fixed selection snapshots and explicit outcomes."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

import add_rule as ar

MARKER = re.compile(r"<!-- acgnx-survey:v1:([A-Za-z0-9_=-]+) -->")
RESULT_PATH = Path(".issue-result.json")


def encode_snapshot(snapshot: dict) -> str:
    data = base64.urlsafe_b64encode(json.dumps(snapshot, ensure_ascii=False).encode()).decode()
    return f"<!-- acgnx-survey:v1:{data} -->"


def read_snapshot(comments: list[dict], term: str, season: int) -> dict | None:
    for comment in reversed(comments):
        if comment.get("user", {}).get("login") != "github-actions[bot]":
            continue
        match = MARKER.search(comment.get("body") or "")
        if not match:
            continue
        try:
            data = match.group(1)
            snapshot = json.loads(base64.urlsafe_b64decode(data + '=' * (-len(data) % 4)))
            ar.resolve_snapshot(snapshot, term, [1], season)
        except (ValueError, TypeError, KeyError):
            continue
        return snapshot
    return None


def issue_body(season: str, from_episode: str, backfill: str) -> str:
    return ("请在下方清单回复序号或字幕组名。\n\n"
            f"### 第几季\n{season}\n\n### 只要第几集之后\n{from_episode}\n\n"
            f"### 推哪些\n{backfill}\n")


def fields(body: str) -> dict[str, str]:
    result = {}
    key = None
    for line in body.splitlines():
        match = re.match(r"^#{1,6}\s+(.+)", line)
        if match:
            key = match.group(1).strip()
            result[key] = ""
        elif key and line.strip() and not result[key]:
            value = line.strip()
            if value not in ("_No response_", "无回应", "无响应"):
                result[key] = value
    return result


def options(body: str) -> tuple[str, int, int, str]:
    data = fields(body)

    def field(prefix: str) -> str:
        return next((v for k, v in data.items() if k.startswith(prefix)), "")

    group = field("字幕组")
    season = field("第几季")
    floor = field("只要第几集之后")
    if not data:
        plain = [s.strip() for s in body.splitlines() if s.strip() and not s.startswith("<!--")]
        group = next((s for s in plain if not s.isdigit()), "")
        number = next((s for s in plain if s.isdigit()), "")
        if number:
            if int(number) < 10:
                season = number
            else:
                floor = number
    for value in (season, floor):
        if value and not re.fullmatch(r"\d{1,4}", value):
            raise ValueError("季号和起始集数必须是 0 到 9999 的整数，未修改规则")
    backfill = field("推哪些") or "all"
    if backfill not in ("all", "latest", "none"):
        raise ValueError("推哪些只支持 all/latest/none，未修改规则")
    return group, int(season or 0), int(floor or 0), backfill


def command(title: str) -> tuple[str, str]:
    title = title.strip()
    for mode, prefixes in (("add", ("加番", "添加", "新增", "追番")),
                           ("del", ("删番", "删除", "移除", "取消")),
                           ("pause", ("暂停",)), ("resume", ("恢复",)), ("edit", ("修改",)),
                           ("list", ("列表", "清单", "我的番"))):
        for prefix in prefixes:
            if title.startswith(prefix):
                return mode, title[len(prefix):].strip()
    return "add", title


def survey(term: str, season: int) -> dict:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "survey.json"
        text = ar.survey_text(term, season=season, snapshot_path=str(path))
        if not path.exists():
            return {"ok": False, "step": "survey", "text": text or "没搜到资源，请更换关键词后重新获取清单。"}
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    return {"ok": True, "step": "survey", "text": text, "snapshot": snapshot}


def comments_for_issue(number: int) -> list[dict]:
    comments = []
    for page in range(1, 11):
        url = (f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}"
               f"/issues/{number}/comments?per_page=100&page={page}")
        request = urllib.request.Request(url, headers={
            "Authorization": "Bearer " + os.environ["GH_TOKEN"],
            "Accept": "application/vnd.github+json", "User-Agent": "acgnx-issue-handler",
        })
        with urllib.request.urlopen(request, timeout=20) as response:
            batch = json.load(response)
        comments.extend(batch)
        if len(batch) < 100:
            return comments
    raise ValueError("评论过多，无法安全确定原清单，请另开 Issue")


def run_cli(args: list[str]) -> dict:
    process = subprocess.run([sys.executable, "add_rule.py", *args],
                             capture_output=True, text=True, encoding="utf-8")
    text = (process.stdout + process.stderr)[-12000:]
    return {"ok": process.returncode == 0, "step": "done", "text": text,
            "exit_code": process.returncode}


def handle(event: dict, event_name: str) -> dict:
    issue = event["issue"]
    owner = event["repository"]["owner"]["login"]
    if event.get("sender", {}).get("login") != owner or issue.get("pull_request"):
        raise ValueError("只有仓库所有者可以管理规则，且不处理 PR 评论")
    mode, term = command(issue.get("title") or "")
    group, season, floor, backfill = options(issue.get("body") or "")
    if mode == "list":
        return run_cli(["--render", "--no-push"])
    if not term:
        raise ValueError("请填写番名，未修改规则")
    if mode == "del":
        if event_name == 'issue_comment':
            return {'ok': True, 'step': 'done', 'text': '删除指令仅在新 Issue 执行，未重复删除。'}
        return run_cli(["--del-name", term, "--no-push"])
    if mode in ("pause", "resume"):
        return run_cli(["--" + mode, term, "--no-push"])
    if mode == "edit":
        args = ['--edit', term, '--no-push']
        data = fields(issue.get('body') or '')
        if group:
            if group.lower() in ('任意', 'any', '*'):
                args.append('--any-group')
            else:
                for value in re.split(r'[,，、]', group):
                    if value.strip():
                        args.extend(['--group', value.strip()])
        if any(k.startswith('第几季') and v for k, v in data.items()):
            args.extend(['--season', str(season)])
        if any(k.startswith('只要第几集之后') and v for k, v in data.items()):
            args.extend(['--from-episode', str(floor)])
        return run_cli(args)
    if event_name == "issues" and not group:
        return survey(term, season)
    if event_name == "issue_comment":
        text = (event.get("comment", {}).get("body") or "").strip()
        selection = text.splitlines()[0].strip().strip('`') if text else ""
        if selection in ("刷新", "重新获取清单"):
            return survey(term, season)
        snapshot = read_snapshot(comments_for_issue(issue["number"]), term, season)
        if snapshot is None:
            result = survey(term, season)
            result["text"] = "旧清单没有可验证快照，未添加；请按下面新清单重新回复。\n\n" + result["text"]
            return result
        if re.fullmatch(r"\d+(?:[,，、\s]+\d+)*", selection):
            picks = [int(n) for n in re.split(r"[,，、\s]+", selection)]
            groups = ar.resolve_snapshot(snapshot, term, picks, season)
        elif selection.lower() in ("任意", "any", "*"):
            groups = []
        else:
            groups = [g.strip() for g in re.split(r"[,，、]", selection) if g.strip()]
            if not groups or any(g not in snapshot["groups"] for g in groups):
                raise ValueError("请选择原清单中的序号或完整组名；回复“刷新”可重新获取清单。未添加规则。")
    else:
        groups = [] if group.lower() in ("任意", "any", "*") else [g.strip() for g in re.split(r"[,，、]", group) if g.strip()]
    args = [term, "--yes", "--no-push", "--notify", "--backfill", backfill,
            "--season", str(season), "--from-episode", str(floor)]
    for group in groups:
        args.extend(["--group", group])
    if not groups:
        args.append("--any-group")
    return run_cli(args)


def main() -> int:
    ar.aw.setup_stdout()
    try:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        result = handle(event, os.environ["GITHUB_EVENT_NAME"])
    except Exception as error:
        result = {"ok": False, "step": "error", "text": f"处理未完成：{type(error).__name__}: {error}"}
    if result.get("snapshot"):
        result["marker"] = encode_snapshot(result.pop("snapshot"))
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    print(result["text"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
