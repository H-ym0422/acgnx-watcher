#!/usr/bin/env python3
"""提交前自检：确认所有 GitHub 工作流的 YAML 语法正确。

为什么需要这个：
  2026-10-16 我在 issue-rule.yml 里写了个跨行的 shell 字符串，
  续行顶格了，把 `run: |` 的缩进块截断 —— 整个工作流变成语法错误。
  表现很隐蔽：Actions 里工作流名字会显示成文件路径，
  触发时产生一条**没有任何 job** 的失败运行，日志里什么都看不到。
  光看代码很难发现，必须真的解析一遍 YAML 才能抓到。

用法（在 acgnx-cloud 目录下）：
    python check_workflows.py
有问题会打印具体位置并以非 0 退出。
"""
import glob
import os
import re
import sys

try:
    import yaml
except ImportError:
    print("需要 pyyaml：pip install pyyaml")
    sys.exit(2)

WF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      ".github", "workflows")

# 合法的 YAML 顶层键长这样：name: / on: / jobs: / "on":  / 'on':
KEY_RE = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_.-]*|[\"'][^\"']+[\"'])\s*:")


def main() -> int:
    files = sorted(glob.glob(os.path.join(WF_DIR, "*.yml")) +
                   glob.glob(os.path.join(WF_DIR, "*.yaml")))
    if not files:
        print("没找到工作流文件，确认路径：", WF_DIR)
        return 2

    bad = 0
    for p in files:
        name = os.path.basename(p)
        try:
            text = open(p, encoding="utf-8").read()
        except OSError as e:
            print(f"  ✗ 读不了 {name}: {e}")
            bad += 1
            continue

        # YAML 解析（最要紧的一关）
        try:
            doc = yaml.safe_load(text)
        except Exception as e:
            bad += 1
            print(f"  ✗ {name}  YAML 语法错误")
            for ln in str(e).splitlines()[:8]:
                print(f"      {ln}")
            # 顺手把「不像 YAML 键」的顶格行揪出来 —— 这类错误九成是它干的
            for i, ln in enumerate(text.splitlines(), 1):
                if (ln and not ln.startswith((" ", "#", "---", "\t"))
                        and not KEY_RE.match(ln)):
                    print(f"      ⚠ 第 {i} 行顶格且不像 YAML 键，"
                          f"多半是它截断了缩进块：{ln[:70]}")
            continue

        if not isinstance(doc, dict):
            bad += 1
            print(f"  ✗ {name}  顶层不是映射")
            continue

        # GitHub 的 on: 在 YAML 1.1 里会被解析成布尔 True
        trig = doc.get("on", doc.get(True))
        if trig is None:
            print(f"  ⚠ {name}  没有 on: 段（不会触发）")
        if not doc.get("name"):
            print(f"  ⚠ {name}  没有 name:（Actions 页面会显示成文件路径）")

        # 顶格行本该只有 YAML 顶层键（name: / on: / jobs: …）。
        # 出现「不像键」的顶格行，基本就是 shell 续行把缩进块截断了 —— 头号杀手。
        suspects = [i for i, ln in enumerate(text.splitlines(), 1)
                    if (ln and not ln.startswith((" ", "#", "---", "\t"))
                        and not KEY_RE.match(ln))]
        if suspects:
            bad += 1
            print(f"  ✗ {name}  有不像 YAML 键的顶格行 {suspects[:6]}，"
                  f"几乎肯定是 shell 续行没缩进")
            for i in suspects[:3]:
                print(f"      第 {i} 行：{text.splitlines()[i-1][:80]}")

        t = list(trig.keys()) if isinstance(trig, dict) else trig

        # GitHub 的 if: 表达式不是 YAML，查表达式语法错误要单独扫。
        # 踩过的坑：用正则删条件时留下「&& && &&)」，YAML 照样合法，
        # 但 GitHub 解析表达式失败 → 整个 job 静默不跑，只有 job 列表是空的。
        for job in (doc.get("jobs") or {}).values():
            expr = str((job or {}).get("if", ""))
            if "&& &&" in expr or "|| ||" in expr or "&& )" in expr or "( (" in expr:
                print(f"  ✗ {name}  {job.get('name')} 的 if 表达式疑似断裂：{expr[:80]}")
                bad += 1
            elif expr.count("(") != expr.count(")"):
                print(f"  ✗ {name}  {job.get('name')} 的 if 表达式括号不配对：{expr[:80]}")
                bad += 1

        print(f"  ✓ {name:<22} name={doc.get('name')!r}  触发={t}")

    print()
    if bad:
        print(f"{bad} 个文件有问题，别急着推。")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
