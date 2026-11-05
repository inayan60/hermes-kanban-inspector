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

    def test_input_validation_and_redaction(self):
        with self.assertRaises(Exception):api.tasks('../etc')
        self.assertEqual(api.clip('Bearer sk-super-long-test-token-1234567'), 'Bearer [REDACTED]')


    def test_three_run_audit_uses_correct_run_owners_and_usage(self):
        root = self.h
        b = sqlite3.connect(root / 'kanban.db')
        b.executescript("""
            DELETE FROM task_events;
            DELETE FROM task_runs;
            ALTER TABLE task_runs ADD COLUMN ended_at INTEGER;
            ALTER TABLE task_runs ADD COLUMN outcome TEXT;
            ALTER TABLE task_runs ADD COLUMN error TEXT;
            ALTER TABLE tasks ADD COLUMN completed_at INTEGER;
            CREATE TABLE task_attachments (id INTEGER PRIMARY KEY, task_id TEXT, filename TEXT, stored_path TEXT,
                                           size INTEGER, uploaded_by TEXT, created_at INTEGER, content_type TEXT);
        """)
        b.execute('UPDATE tasks SET created_at=?, started_at=?, completed_at=?, worker_pid=? WHERE id=?',
                  (1793906000, 1793906150, 1793907220, 0, 't_91dcef9e'))
        for rid, prof, st, end, outcome in [(165,'laney-infra',1793906158,1793906520,'blocked'),
                                            (166,'laney-infra',1793906590,1793907193,'completed'),
                                            (167,'norris-orquestrador',1793907205,1793907221,'crashed')]:
            b.execute('INSERT INTO task_runs(id,task_id,profile,status,worker_pid,started_at,ended_at,outcome,error) VALUES (?,?,?,?,?,?,?,?,?)',
                      (rid,'t_91dcef9e',prof,'blocked' if rid==165 else 'failed' if rid==167 else 'done',
                       0 if rid==167 else 33064,st,end,outcome,'review worker failed' if rid==167 else None))
            session_id='local-'+str(rid)
            b.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?)',
                      (rid,'t_91dcef9e',rid,'owner_admitted',json.dumps({'session_id':session_id,
                          'request_id':f'kanban:["default","t_91dcef9e",{rid}]',
                          'db':str(root/'profiles'/prof/'state.db')}),st+2))
        b.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?)',
                  (1001,'t_91dcef9e',167,'worker_lost',json.dumps({'reason':'managed_worker_failed'}),1793907221))
        b.execute('INSERT INTO task_events VALUES (?,?,?,?,?,?)',
                  (1002,'t_91dcef9e',167,'worker_result',json.dumps({'exit_code':1,'last_output':''}),1793907221))
        b.commit();b.close()
        for prof, pairs in [('laney-infra',[(165,100,40,30),(166,240,55,100)]),
                            ('norris-orquestrador',[(167,25,0,0)])]:
            folder=root/'profiles'/prof
            folder.mkdir(parents=True,exist_ok=True)
            con=sqlite3.connect(folder/'state.db')
            con.executescript("""
                CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, source TEXT, model TEXT,
                                      started_at REAL, ended_at REAL, parent_session_id TEXT);
                CREATE TABLE messages(id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                                      tool_calls TEXT, tool_call_id TEXT, tool_name TEXT, timestamp REAL);
                CREATE TABLE session_model_usage(session_id TEXT,model TEXT,billing_provider TEXT,task TEXT,
                                      input_tokens INTEGER,output_tokens INTEGER,cache_read_tokens INTEGER,
                                      cache_write_tokens INTEGER,reasoning_tokens INTEGER,api_call_count INTEGER,
                                      estimated_cost_usd REAL);
                CREATE TABLE worker_executions(execution_id TEXT, session_id TEXT,kind TEXT,status TEXT,last_sequence INTEGER);
                CREATE TABLE worker_receipts(execution_id TEXT, sequence INTEGER,result_json TEXT);
            """)
            for rid, inbound, outbound, cached in pairs:
                sid=f'local-{rid}'
                con.execute('INSERT INTO sessions VALUES(?,?,?,?,?,?,?)',
                            (sid,'Kanban','kanban','gpt-6.1-sol',1793906000,1793907221,None))
                con.execute('INSERT INTO session_model_usage VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                            (sid,'gpt-6.1-sol','openai-codex','agent',inbound,outbound,cached,0,0,2,0.25))
                fn=json.dumps([{'id':f'call-{rid}','function':{'name':'file_read',
                                                       'arguments':json.dumps({'path':'run.md'})}}])
                con.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                            (rid*10,sid,'assistant','reading',fn,None,None,1793906000+rid))
                con.execute('INSERT INTO messages VALUES(?,?,?,?,?,?,?,?)',
                            (rid*10+1,sid,'tool','read complete',None,f'call-{rid}','file_read',1793906001+rid))
                con.execute('INSERT INTO worker_executions VALUES(?,?,?,?,?)',
                            (f'exec-{rid}',sid,'kanban','terminal',14))
                if rid == 167:
                    con.execute('INSERT INTO worker_receipts VALUES(?,?,?)',
                                (f'exec-{rid}',1,json.dumps({'error':'worker interrupted','exit_code':1})))
            con.commit();con.close()
        audit=api.audit_task('default','t_91dcef9e')
        self.assertEqual([r['id'] for r in audit['runs']],[165,166,167])
        self.assertEqual([r['profile'] for r in audit['runs']],
                         ['laney-infra','laney-infra','norris-orquestrador'])
        self.assertEqual([r['sessions'][0]['id'] for r in audit['runs']],
                         ['local-165','local-166','local-167'])
        self.assertEqual([r['messages'] for r in audit['runs']],[2,2,2])
        self.assertEqual(audit['runs'][2]['duration_seconds'],16.0)
        self.assertEqual(audit['runs'][2]['process_state'],'finished (historical, PID absent)')
        self.assertEqual(audit['summary']['card_duration_seconds'],1220)
        self.assertEqual(audit['summary']['usage']['input_tokens'],365)
        self.assertEqual(audit['summary']['usage']['cache_read_tokens'],130)
        self.assertEqual(audit['summary']['usage']['output_tokens'],95)
        self.assertEqual(audit['summary']['sessions'],3)
        self.assertTrue(any(e['kind']=='worker_lost' for e in audit['runs'][2]['errors']))
        self.assertEqual(len([e for e in audit['timeline'] if e['kind']=='read']),3)
        self.assertEqual(len([e for e in audit['timeline'] if e['kind']=='result']),3)

    def test_pid_zero_is_never_active_even_when_win32_reports_system_idle(self):
        con=sqlite3.connect(self.h/'kanban.db')
        con.execute('UPDATE tasks SET worker_pid=0')
        con.execute('UPDATE task_runs SET worker_pid=0')
        con.commit();con.close()
        with patch.object(api, 'processes',return_value=[
            {'ProcessId':0,'ParentProcessId':0,'Name':'System Idle Process','CommandLine':'', 'CreationDate':'old'}]):
            diagnosis=api.diagnose('default','t_91dcef9e')
            self.assertIsNone(diagnosis['worker_alive'])
            self.assertEqual(diagnosis['worker_pid'],0)
            self.assertFalse(any(p['pid']==0 for p in diagnosis['related_processes']))

    def test_artifact_preview_confined_and_sanitized(self):
        root=self.h
        folder=root/'kanban'/'attachments'/'t_91dcef9e'
        folder.mkdir(parents=True)
        good=folder/'inventario.md'
        good.write_text('Relatorio\nAPI_KEY=not-for-export',encoding='utf-8')
        outside=root/'secret.md'
        outside.write_text('private')
        con=sqlite3.connect(root/'kanban.db')
        con.execute('CREATE TABLE task_attachments(id integer, task_id text, filename text, stored_path text, size integer, uploaded_by text, created_at integer)')
        con.execute('INSERT INTO task_attachments VALUES(?,?,?,?,?,?,?)',(1,'t_91dcef9e','inventario.md',str(good),32,'agent',123))
        con.execute('INSERT INTO task_attachments VALUES(?,?,?,?,?,?,?)',(2,'t_91dcef9e','secret.md',str(outside),7,'agent',123))
        con.commit();con.close()
        self.assertIn('[REDACTED]',api.artifact_preview('default','t_91dcef9e',1)['content'])
        with self.assertRaises(Exception):api.artifact_preview('default','t_91dcef9e',2)

if __name__ == '__main__':
    unittest.main()