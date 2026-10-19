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
    def test_issue_reply_archives_completed_operations_but_keeps_surveys_and_errors(self):
        root = Path(__file__).resolve().parents[1]
        workflow = yaml.safe_load((root / '.github/workflows/issue-rule.yml').read_text(encoding='utf-8'))
        script = next(step['with']['script'] for step in workflow['jobs']['handle']['steps']
                      if step.get('name') == '回复 Issue')
        wrapper = '''
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const captured = {comments: [], updates: [], graphql: []};
const result = RESULT;
const github = {graphql: async (query, args) => {
  captured.graphql.push({query, args});
  if (FAIL_DELETE) throw new Error('permission denied');
  return {deleteIssue: {clientMutationId: null}};
}, rest: {issues: {
  createComment: async args => {captured.comments.push(args);},
  update: async args => {captured.updates.push(args);}
}}};
const fakeRequire = name => ({existsSync: () => true, readFileSync: () => JSON.stringify(result)});
new AsyncFunction('github', 'context', 'require', 'process', SCRIPT)(github,
  {repo: {owner: 'owner', repo: 'repo'}, issue: {number: 7}, payload: {issue: {node_id: 'I_7'}}}, fakeRequire,
  {env: {PERSIST_OK: SAVED, ISSUE_DELETE_TOKEN: OWNER_SECRET_VALUE}})
  .then(() => console.log(JSON.stringify(captured)))
  .catch(error => {console.error(error); process.exit(1)});
'''.replace('SCRIPT', json.dumps(script))
        cases = [('deleted', True, True, '', False, True, False),
                 ('deleted', True, True, 'test-only', False, False, True),
                 ('deleted', True, True, 'test-only', True, True, True),
                 ('deleted', False, True, 'test-only', False, False, False),
                 ('deleted', True, False, 'test-only', False, False, False),
                 ('done', True, True, 'test-only', False, True, False),
                 ('query', True, True, '', False, True, False),
                 ('survey', True, True, '', False, False, False),
                 ('error', False, True, '', False, False, False)]
        for step, ok, saved, token, fail_delete, archive, call_delete in cases:
            with self.subTest(step=step, saved=saved, fail_delete=fail_delete):
                code = wrapper.replace('RESULT', json.dumps({'step': step, 'ok': ok, 'text': 'output'})).replace('SAVED', json.dumps(str(saved).lower())).replace('OWNER_SECRET_VALUE', json.dumps(token)).replace('FAIL_DELETE', json.dumps(fail_delete))
                process = subprocess.run(['node', '-e', code], capture_output=True, text=True, encoding='utf8', check=True)
                captured = json.loads(process.stdout)
                self.assertEqual(len(captured['comments']), 0 if step == 'deleted' and ok and saved else 1)
                self.assertEqual(len(captured['graphql']), int(call_delete))
                if call_delete:
                    self.assertEqual(captured['graphql'][0]['args']['issueId'], 'I_7')
                    self.assertEqual(captured['graphql'][0]['args']['headers']['authorization'], 'token test-only')
                self.assertEqual(len(captured['updates']), int(archive))
                if archive:
                    self.assertEqual(captured['updates'][0]['state'], 'closed')
                    self.assertEqual(captured['updates'][0]['issue_number'], 7)

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
