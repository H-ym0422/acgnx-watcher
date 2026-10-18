"""Exercise the actual Actions JS producer against the Python Issue consumer."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml
import issue_commands as ic


@unittest.skipUnless(shutil.which('node'), 'Node.js is required for Actions JS contract tests')
class WorkflowContractTests(unittest.TestCase):
    def test_actions_issue_preserves_every_backfill_mode_and_snapshot(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load((root / '.github/workflows/add-rule.yml').read_text(encoding='utf-8'))
        script = next(step['with']['script'] for step in workflow['jobs']['add']['steps']
                      if step.get('name') == '开一条 Issue 并贴上清单')
        snapshot = {'version': 1, 'term': 'demo', 'season': 2, 'groups': ['Group A', 'Group B']}
        wrapper = '''
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const captured = {};
const github = {rest: {issues: {
  create: async args => {captured.issue = args; return {data: {number: 7, html_url: 'https://example.invalid/7'}};},
  createComment: async args => {captured.comment = args;}
}}};
const summary = {addHeading(){return this}, addRaw(){return this}, addEOL(){return this}, async write(){}};
const core = {summary, notice(){}};
new AsyncFunction('github', 'context', 'core', 'require', SCRIPT)(github,
  {repo: {owner: 'owner', repo: 'test'}}, core, require)
  .then(() => console.log(JSON.stringify(captured)))
  .catch(error => {console.error(error); process.exit(1)});
'''.replace('SCRIPT', json.dumps(script))
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / '.survey-snapshot.json').write_text(json.dumps(snapshot), encoding='utf-8')
            for mode in ['all', 'latest', 'none']:
                with self.subTest(mode=mode):
                    env = dict(os.environ, ANIME='demo', SEA='2', FROM_EP='49', BACKFILL=mode, SURVEY='survey')
                    process = subprocess.run(['node', '-e', wrapper], env=env, cwd=directory,
                                             capture_output=True, text=True, encoding='utf-8', check=True)
                    result = json.loads(process.stdout.splitlines()[-1])
                    self.assertEqual(ic.options(result['issue']['body']), ('', 2, 49, mode))
                    comments = [{'user': {'login': 'github-actions[bot]'}, 'body': result['comment']['body']}]
                    self.assertEqual(ic.read_snapshot(comments, 'demo', 2), snapshot)


if __name__ == '__main__':
    unittest.main()
