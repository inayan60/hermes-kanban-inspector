import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / 'dashboard' / 'plugin_api.py'
spec = importlib.util.spec_from_file_location('hermes_inspector_api', MODULE)
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = Path(self.tmp.name)
        (self.h / 'profiles' / 'lenora-rh').mkdir(parents=True)
        board = sqlite3.connect(self.h / 'kanban.db')
        board.executescript('''
            CREATE TABLE tasks (id text primary key, title text, status text, assignee text, created_at integer, started_at integer, worker_pid integer);
            CREATE TABLE task_runs (id integer primary key, task_id text, profile text, status text, worker_pid integer, started_at integer, last_heartbeat_at integer);
            CREATE TABLE task_events (id integer primary key, task_id text, run_id integer, kind text, payload text, created_at integer);
            CREATE TABLE task_comments (id integer primary key, task_id text, author text, body text, created_at integer);
        ''')
        board.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?,?)',('t_91dcef9e','Inventariar bots','running','lenora-rh',1793896400,1793896420,9704))
        board.execute('INSERT INTO task_runs VALUES (?,?,?,?,?,?,?)',(163,'t_91dcef9e','lenora-rh','running',9704,1793896420,None))
        board.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?)',(1,'t_91dcef9e',163,'owner_admitted',json.dumps({'session_id':'local-foo','pid':3604}),1793896430))
        board.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?)',(2,'t_91dcef9e',163,'worker_bound',json.dumps({'pid':7068}),1793896440))
        board.commit();board.close()
        state = sqlite3.connect(self.h/'profiles'/'lenora-rh'/'state.db')
        state.executescript('''
          CREATE TABLE sessions (id text primary key, title text, model text, source text, updated_at integer);
          CREATE TABLE messages (id integer primary key, session_id text, role text, content text, tool_calls text, created_at integer, tool_name text);
          CREATE TABLE worker_executions (session_id text, status text, kind text, last_sequence integer, execution_id text);
        ''')
        state.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',('local-foo','Kanban','gpt-6.1-sol','kanban',1793896440))
        state.execute('INSERT INTO worker_executions VALUES (?,?,?,?,?)',('local-foo','running','compute',0,'admission-worker:abc'))
        state.commit();state.close()
        self.patcher = patch.dict(os.environ, {'HERMES_HOME':str(self.h)})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop();self.tmp.cleanup()

    def test_inventory_board_task_session(self):
        self.assertEqual(api.boards_profiles()['boards'],['default'])
        self.assertEqual(api.tasks('default')[0]['id'],'t_91dcef9e')
        self.assertEqual(api.task_info('default','t_91dcef9e')['session_id'],'local-foo')
        self.assertEqual(api.task_info('default','t_91dcef9e')['profile'],'lenora-rh')
        result = api.timeline_profile('lenora-rh','local-foo')
        self.assertEqual(result['total'],0)
        self.assertEqual(result['executions'][0]['last_sequence'],0)

    def test_timeline_function_calls_and_redaction(self):
        state = sqlite3.connect(self.h/'profiles'/'lenora-rh'/'state.db')
        state.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',(1,'local-foo','assistant','Vou ler o arquivo',json.dumps([{'id':'call1','function':{'name':'file_read','arguments':json.dumps({'path':'doc.md','api_key':'sk-'+'A'*40})}}]),1793896500,None))
        state.execute('INSERT INTO messages VALUES (?,?,?,?,?,?,?)',(2,'local-foo','tool','conteúdo lido',None,1793896501,'file_read'))
        state.commit();state.close()
        result=api.timeline_profile('lenora-rh','local-foo')
        self.assertEqual(result['total'],2)
        self.assertEqual([a['kind'] for a in result['timeline']],['read','assistant','result'])
        self.assertNotIn('sk-'+'A'*40,str(result))

    def test_unrelated_git_never_eligible(self):
        fake=[{'ProcessId':9704,'ParentProcessId':3604,'Name':'python.exe','CommandLine':'python.exe -m hermes_cli.kanban_worker_client','CreationDate':'one'},
              {'ProcessId':7068,'ParentProcessId':3604,'Name':'python.exe','CommandLine':'python.exe -m agent.managed_worker','CreationDate':'two'},
              {'ProcessId':6188,'ParentProcessId':4356,'Name':'git.exe','CommandLine':'git.exe rev-parse HEAD','CreationDate':'three'}]
        with patch.object(api,'processes',return_value=fake):
            diag=api.diagnose('default','t_91dcef9e')
            self.assertEqual(diag['managed_pid'],7068)
            self.assertEqual(diag['unrelated_git_count'],1)
            self.assertEqual(diag['verified_git_descendants'],[])
            preview=api.recovery_preview('default','t_91dcef9e')
            self.assertFalse(preview['available'])

    def test_verified_git_requires_preview_and_exact_confirm(self):
        fake=[{'ProcessId':9704,'ParentProcessId':3604,'Name':'python.exe','CommandLine':'worker submit','CreationDate':'one'},
              {'ProcessId':7068,'ParentProcessId':3604,'Name':'python.exe','CommandLine':'agent.managed_worker','CreationDate':'two'},
              {'ProcessId':8001,'ParentProcessId':7068,'Name':'git.exe','CommandLine':'git.exe rev-parse HEAD','CreationDate':'three'}]
        with patch.object(api,'processes',return_value=fake), patch.object(api.os,'kill') as killer, patch.object(api,'_IS_WINDOWS',True):
            preview=api.recovery_preview('default','t_91dcef9e')
            self.assertTrue(preview['available'])
            with self.assertRaises(Exception):api.recovery_apply(preview['token'],'yes')
            self.assertFalse(killer.called)
            self.assertTrue(api.recovery_apply(preview['token'],'ENCERRAR GIT')['ok'])
            killer.assert_called_once()

    def test_agent_entrypoint_registers_no_tools_or_hooks(self):
        entry = MODULE.parents[1] / '__init__.py'
        self.assertTrue(entry.is_file())
        namespace = {}
        exec(compile(entry.read_text(encoding='utf-8'), str(entry), 'exec'), namespace)

        class NoAgentCalls:
            def __getattr__(self, name):
                raise AssertionError(f'Unexpected Agent plugin method: {name}')

        self.assertIsNone(namespace['register'](NoAgentCalls()))

    def test_named_profile_backend_resolves_shared_installation(self):
        # Hermes Desktop may launch serve with HERMES_HOME=<root>/profiles/X.
        # The plugin must still resolve the shared board and peer state DBs.
        with patch.dict(os.environ, {'HERMES_HOME': str(self.h / 'profiles' / 'lenora-rh')}):
            self.assertEqual(api.home(), self.h)
            self.assertEqual(api.board_db('default'), self.h / 'kanban.db')
            self.assertEqual(api.state_db('lenora-rh'), self.h / 'profiles' / 'lenora-rh' / 'state.db')
            self.assertIn('lenora-rh', api.boards_profiles()['profiles'])

    def test_input_validation_and_redaction(self):
        with self.assertRaises(Exception):api.tasks('../etc')
        self.assertEqual(api.clip('Bearer sk-super-long-test-token-1234567'), 'Bearer [REDACTED]')


if __name__ == '__main__':
    unittest.main()