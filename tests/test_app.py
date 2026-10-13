import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import bcrypt
import httpx
from fastapi.testclient import TestClient
from app import db as storage
from app import main
from app.stats import settle
from tests.fixtures import seed


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hashed=bcrypt.hashpw(b'Test-password-2026',bcrypt.gensalt(rounds=4)).decode()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/'app.db'
        seed(Path(self.tmp.name)/'mahjong.db')
        self.patches=[patch.object(storage,'DB_PATH',self.path),patch.object(main,'DB_PATH',self.path)]
        for p in self.patches:p.start()
        self.client=TestClient(main.app).__enter__()
        with storage.db() as c:c.execute('UPDATE users SET password_hash=?',(self.hashed,))
        self.login('linjia')

    def tearDown(self):
        self.client.__exit__(None,None,None)
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def login(self,name):
        r=self.client.post('/api/login',json={'username':name,'password':'Test-password-2026'})
        self.assertEqual(r.status_code,200,r.text)
        self.csrf=r.json()['csrf'];self.client.headers['X-CSRF-Token']=self.csrf
        return r.json()

    def game(self,**changes):
        return {'user_ids':[1,2,3,4],'points':[40000,30000,20000,10000],'ended_at':'2026-10-05T20:00:00','note':'测试','game_type':'hanchan',**changes}

    def create(self,**changes):
        return self.client.post('/api/records',json=self.game(**changes),headers={'Idempotency-Key':'test-submission-key-123'})

    def test_migration_preserves_data_and_pinyin(self):
        with storage.db() as c:
            names=[r[0] for r in c.execute('SELECT username FROM users ORDER BY id')]
            self.assertEqual(names,['linjia','linyi','linbing','linding','linwu'])
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],11)
        rounds=self.client.get('/api/rounds').json()
        self.assertEqual([(r['number'],r['count']) for r in rounds],[(2,5),(1,6)])

    def test_requires_login_and_keeps_passwords_private(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get('/api/stats').status_code,401)
        self.login('LINJIA')
        self.assertNotIn('password_hash',self.client.get('/api/users').text)

    def test_csrf_required(self):
        self.client.headers.pop('X-CSRF-Token')
        self.assertEqual(self.create().status_code,403)

    def test_default_settlement_and_zero_sum(self):
        r=self.client.post('/api/records/preview',json=self.game()).json()
        self.assertEqual([p['score'] for p in r['results']],[45,5,-15,-35])
        self.assertEqual(sum(p['score'] for p in r['results']),0)

    def test_ties_use_initial_seat_and_negative_points(self):
        r=self.client.post('/api/records/preview',json=self.game(points=[50900,21700,5700,21700])).json()
        self.assertEqual([p['rank'] for p in r['results']],[1,2,4,3])
        r=self.client.post('/api/records/preview',json=self.game(points=[48900,-2200,20800,32500]))
        self.assertEqual(r.status_code,200)
        self.assertAlmostEqual(sum(p['score'] for p in r.json()['results']),0)

    def test_bad_totals_duplicate_players_and_fractional_points(self):
        for changes in [{'points':[25000]*3+[24000]},{'user_ids':[1,1,3,4]},{'points':[40001,29999,20000,10000]}]:
            self.assertEqual(self.client.post('/api/records/preview',json=self.game(**changes)).status_code,422)

    def test_idempotent_mobile_submission(self):
        first=self.create();second=self.create()
        self.assertEqual(first.status_code,200)
        self.assertEqual(first.json()['id'],second.json()['id'])
        self.assertEqual(self.client.get('/api/stats').json()['count'],12)
        self.assertEqual(self.create(note='不同内容').status_code,409)

    def test_regular_user_cannot_record_or_read_admin_config(self):
        self.login('linyi')
        self.assertEqual(self.create().status_code,403)
        for path in ['/api/users','/api/admin/llm','/api/admin/access','/api/admin/audit']:
            self.assertEqual(self.client.get(path).status_code,403)
        self.assertEqual(self.client.post('/api/analysis',json={'scope':'overall'}).status_code,403)

    def test_recorder_only_edits_own_and_admin_voids(self):
        with storage.db() as c:c.execute('UPDATE users SET can_record=1 WHERE id=2')
        self.login('linyi');new=self.create().json()['id']
        self.assertEqual(self.client.put('/api/records/'+str(new),json=self.game(note='更正')).status_code,200)
        self.assertEqual(self.client.put('/api/records/1',json=self.game()).status_code,403)
        self.assertEqual(self.client.delete('/api/records/'+str(new)).status_code,403)
        self.login('linjia');self.assertEqual(self.client.delete('/api/records/'+str(new)).status_code,200)
        self.assertEqual(self.client.get('/api/stats').json()['count'],11)

    def test_rule_snapshot_survives_default_change(self):
        rule={'starting':25000,'returning':25000,'uma':[15,5,-5,-15]}
        self.assertEqual(self.client.put('/api/admin/rule',json=rule).status_code,200)
        historical=self.client.get('/api/records/1').json()
        self.assertEqual(json.loads(historical['rules_json'])['returning'],30000)
        self.assertEqual(self.client.post('/api/records/preview',json=self.game(record_id=1)).json()['rules']['returning'],25000)

    def test_default_entry_save_time_and_edit_preserves_time(self):
        payload={'user_ids':[1,2,3,4],'points':[40000,30000,20000,10000],'note':'简化录入'}
        with patch('app.main.now',return_value='2026-10-12 18:00:01'):
            result=self.client.post('/api/records',json=payload,headers={'Idempotency-Key':'default-save-time-test'})
        self.assertEqual(result.status_code,200,result.text)
        rid=result.json()['id']
        before=self.client.get('/api/records/'+str(rid)).json()
        self.assertEqual(before['created_at'],'2026-10-12 18:00:01')
        self.assertEqual(before['game_type'],'hanchan')
        payload['note']='编辑保留时间'
        self.assertEqual(self.client.put('/api/records/'+str(rid),json=payload).status_code,200)
        self.assertEqual(self.client.get('/api/records/'+str(rid)).json()['created_at'],before['created_at'])

    def test_group_boundary_and_midnight(self):
        groups=storage.group_rows([{'id':1,'created_at':'2026-01-01 23:00:00'},{'id':2,'created_at':'2026-01-02 00:59:59'},{'id':3,'created_at':'2026-01-02 02:59:59'}])
        self.assertEqual(groups,[[1,2],[3]])

    def test_statistics_invariants_and_personal_scope(self):
        d=self.client.get('/api/stats').json()
        self.assertAlmostEqual(sum(p['total'] for p in d['players']),0)
        self.assertEqual(sum(p['count'] for p in d['players']),44)
        for p in d['players']:self.assertEqual(sum(p['ranks']),p['count'])
        personal=self.client.get('/api/stats?user_id=5').json()
        self.assertEqual(len(personal['players']),1);self.assertEqual(personal['count'],5)

    def test_nickname_changes_history_and_analysis(self):
        with storage.db() as c:
            s=main.analysis_scope(c,'player',1)
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at) VALUES('player',1,?,'[[player:1]] 有进步','test',?)",(s['data_hash'],storage.now()))
        self.assertEqual(self.client.put('/api/profile',json={'nickname':'新的昵称'}).status_code,200)
        self.assertEqual('你 有进步',self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['text'])
        self.login('linyi')
        self.assertIn('新的昵称',self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['text'])
        self.assertIn('新的昵称',self.client.get('/api/stats').text)

    def test_personal_analysis_uses_you_only_for_authenticated_viewer(self):
        with storage.db() as c:
            for scope,target in [('player',1),('overall',0)]:
                snapshot=main.analysis_scope(c,scope,target)
                c.execute('INSERT INTO analyses(scope,target,data_hash,payload,model,created_at) VALUES(?,?,?,?,?,?)',
                          (scope,target,snapshot['data_hash'],'[[player:1]] 与 [[player:2]] 比较','fake',storage.now()))
        text=self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['text']
        self.assertEqual(text,'你 与 虚构玩家乙 比较')
        public=self.client.get('/api/analysis').json()['analysis']['text']
        self.assertEqual(public,'虚构玩家甲 与 虚构玩家乙 比较')
        self.login('linyi')
        text=self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['text']
        self.assertEqual(text,'虚构玩家甲 与 你 比较')

    def test_last_admin_and_user_deletion(self):
        u=self.client.get('/api/users').json()[0]
        self.assertEqual(self.client.put('/api/users/1',json={**u,'role':'user'}).status_code,409)
        self.assertEqual(self.client.delete('/api/users/1').status_code,409)
        r=self.client.delete('/api/users/5');self.assertTrue(r.json()['disabled'])
        self.assertEqual(self.client.get('/api/stats').json()['count'],11)

    def test_llm_key_is_write_only(self):
        cfg={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'very-private-key'}
        self.assertEqual(self.client.put('/api/admin/llm',json=cfg).status_code,200)
        self.assertNotIn('very-private-key',self.client.get('/api/admin/llm').text)
        self.assertNotIn('very-private-key',self.client.get('/api/admin/audit').text)
        self.assertEqual(self.client.put('/api/admin/llm',json={**cfg,'api_key':''}).status_code,200)
        self.assertTrue(self.client.get('/api/admin/llm').json()['has_key'])

    def test_analysis_staleness_and_duplicate_job(self):
        with storage.db() as c:
            snap=main.analysis_scope(c,'overall',0)
            storage.set_setting(c,'llm',{'base_url':'https://example.com/v1','model':'test','api_key':'private'})
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at,analysis_version) VALUES('overall',0,?,'统计总结','test',?,3)",(snap['data_hash'],storage.now()))
            c.execute("INSERT INTO jobs(id,status,created_at) VALUES('busy','running',?)",(storage.now(),))
        self.assertFalse(self.client.get('/api/analysis').json()['analysis']['stale'])
        self.assertEqual(self.client.post('/api/analysis',json={'scope':'overall'}).status_code,409)
        self.create();self.assertTrue(self.client.get('/api/analysis').json()['analysis']['stale'])

    def test_model_pipeline_with_fake_provider(self):
        with storage.db() as c:
            snap=main.analysis_scope(c,'player',1)
            c.execute("INSERT INTO jobs(id,status,created_at) VALUES('fake','running',?)",(storage.now(),))
        response=type('ProviderResponse',(),{'raise_for_status':lambda self:None,'json':lambda self:{'choices':[{'message':{'content':'[[player:1]]：样本较少。'}}]}})()
        with patch('app.main.httpx.Client') as fake:
            fake.return_value.__enter__.return_value.post.return_value=response
            main.run_analysis('fake','player',1,{'base_url':'https://example.com/v1','model':'fake','api_key':'secret'},snap)
            payload=fake.return_value.__enter__.return_value.post.call_args.kwargs['json']
            prompt=payload['messages'][1]['content']
            self.assertIn('co_players',prompt)
            for nickname in ['虚构玩家甲','虚构玩家乙','虚构玩家丙','虚构玩家丁','虚构玩家戊']: self.assertNotIn(nickname,prompt)
            self.assertIn('[[player:2]]',prompt)
        self.assertEqual(self.client.get('/api/jobs/fake').json()['status'],'done')

    def test_analysis_evidence_decomposes_actual_scores_without_names(self):
        with storage.db() as c:snapshot=main.analysis_scope(c,'overall',0)
        evidence=main.analysis_evidence('overall',snapshot)
        for player in evidence['players']:
            self.assertAlmostEqual(sum(player['score_components'].values()),player['total'])
            self.assertAlmostEqual(sum(row['total_pt'] for row in player['rank_outcomes']),player['total'])
            self.assertEqual([row['count'] for row in player['rank_outcomes']],player['ranks'])
            self.assertLessEqual(len(player['recent_trend']),20)
        encoded=json.dumps(evidence,ensure_ascii=False)
        for player in snapshot['players']:self.assertNotIn(player['nickname'],encoded)
        prompts=[main.analysis_prompt(scope) for scope in ['overall','round','player']]
        self.assertEqual(len(set(prompts)),3)

    def test_point_ranking_metrics_match_history_and_exclude_scoring_rewards(self):
        before=self.client.get('/api/stats').json()
        for player in before['players']:
            trend=player['trend']
            self.assertEqual(player['net_total'],sum(row['net'] for row in trend))
            self.assertEqual(player['net_total'],player['details']['net_total'])
            self.assertEqual(player['net_average'],player['details']['net_average'])
            for key in ['net_total','net_average','average_points','best_points','worst_points']:
                self.assertEqual(trend[-1][key],player[key])
            self.assertEqual(player['best_points'],max(row['points'] for row in trend))
            self.assertEqual(player['worst_points'],min(row['points'] for row in trend))
        self.client.put('/api/admin/rule',json={'name':'不同结算','starting':25000,'returning':30000,'uma':[25,5,-5,-25]})
        after=self.client.get('/api/stats').json()
        by_id={player['user_id']:player for player in after['players']}
        for player in before['players']:
            for key in ['net_total','net_average','average_points','best_points','worst_points']:
                self.assertEqual(player[key],by_id[player['user_id']][key])

    def test_legacy_analysis_is_marked_for_new_interpretation(self):
        with storage.db() as c:
            snapshot=main.analysis_scope(c,'overall',0)
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at) VALUES('overall',0,?,'旧解读','test',?)",(snapshot['data_hash'],storage.now()))
        analysis=self.client.get('/api/analysis').json()['analysis']
        self.assertTrue(analysis['style_outdated'])
        self.assertTrue(analysis['stale'])

    def test_approved_prompts_are_packaged_and_scope_specific(self):
        common=main.analysis_prompt('overall')
        self.assertIn('scoring_rule.starting',common)
        self.assertIn('累计 Oka',common)
        self.assertIn('整体分析',common)
        self.assertIn('个人范围 `players` 中只有目标玩家',main.analysis_prompt('player'))
        self.assertIn('只描述本届',main.analysis_prompt('round'))
        for scope in ['overall','round','player']:
            self.assertNotIn('{{',main.analysis_prompt(scope))
        with self.assertRaises(ValueError):main.analysis_prompt('../common')

    def test_admin_prompt_edit_conflict_validation_and_reset(self):
        original=self.client.get('/api/admin/prompts').json()
        values={**original['prompts'],'revision':original['revision']}
        changed={**values,'overall':values['overall']+'\n重点解释低顺位损失。'}
        saved=self.client.put('/api/admin/prompts',json=changed)
        self.assertEqual(saved.status_code,200)
        self.assertEqual(saved.json()['changed'],['overall'])
        current=self.client.get('/api/admin/prompts').json()
        self.assertEqual(current['prompts']['overall'],changed['overall'])
        self.assertEqual(current['customized'],['overall'])
        self.assertEqual(self.client.put('/api/admin/prompts',json=values).status_code,409)
        invalid={**current['prompts'],'revision':current['revision'],'common':'   '}
        self.assertEqual(self.client.put('/api/admin/prompts',json=invalid).status_code,422)
        reset={**original['defaults'],'revision':current['revision']}
        self.assertEqual(self.client.put('/api/admin/prompts',json=reset).status_code,200)
        self.assertEqual(self.client.get('/api/admin/prompts').json()['customized'],[])
        self.login('linyi')
        self.assertEqual(self.client.get('/api/admin/prompts').status_code,403)
        self.assertEqual(self.client.put('/api/admin/prompts',json=reset).status_code,403)

    def test_prompt_changes_stale_only_relevant_scope_and_job_uses_snapshot(self):
        with storage.db() as c:
            overall=main.analysis_scope(c,'overall',0)
            player=main.analysis_scope(c,'player',1)
            for scope,target,snapshot in [('overall',0,overall),('player',1,player)]:
                c.execute('INSERT INTO analyses(scope,target,data_hash,payload,model,created_at,analysis_version,prompt_hash) VALUES(?,?,?,?,?,?,?,?)',
                          (scope,target,snapshot['data_hash'],'旧正文','fake',storage.now(),main.ANALYSIS_VERSION,main.prompt_fingerprint(main.analysis_prompt(scope))))
            c.execute("INSERT INTO jobs(id,status,created_at) VALUES('snapshot','running',?)",(storage.now(),))
        original=self.client.get('/api/admin/prompts').json()
        changed={**original['prompts'],'revision':0,'overall':'新的综合指令'}
        self.client.put('/api/admin/prompts',json=changed)
        self.assertTrue(self.client.get('/api/analysis').json()['analysis']['style_outdated'])
        self.assertFalse(self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['style_outdated'])
        cfg={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'secret',
             '_analysis_prompt':main.analysis_prompt('overall')}
        with patch('app.main.httpx.Client') as fake:
            provider=fake.return_value.__enter__.return_value
            provider.post.return_value.json.return_value={'choices':[{'message':{'content':'快照生成的正文'}}]}
            main.run_analysis('snapshot','overall',0,cfg,overall)
            self.assertEqual(provider.post.call_args.kwargs['json']['messages'][0]['content'],cfg['_analysis_prompt'])
        self.assertTrue(self.client.get('/api/analysis').json()['analysis']['style_outdated'])
        current=self.client.get('/api/admin/prompts').json()
        self.client.put('/api/admin/prompts',json={**current['prompts'],'revision':current['revision'],'common':'新的通用指令'})
        self.assertTrue(self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['style_outdated'])

    def test_deepseek_test_uses_unsaved_form_without_saving_key(self):
        saved={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'saved-secret'}
        self.client.put('/api/admin/llm',json=saved)
        with patch('app.main.httpx.Client') as fake:
            provider=fake.return_value.__enter__.return_value
            provider.post.return_value.json.return_value={'choices':[{'message':{'content':'OK'}}]}
            result=self.client.post('/api/admin/llm/test',json={**saved,'model':'deepseek-v4-pro','api_key':''})
            self.assertEqual(result.status_code,200)
            call=provider.post.call_args
            self.assertEqual(call.args[0],'https://api.deepseek.com/chat/completions')
            self.assertEqual(call.kwargs['headers']['Authorization'],'Bearer saved-secret')
            self.assertEqual(call.kwargs['json']['thinking'],{'type':'disabled'})
            self.assertEqual(call.kwargs['json']['model'],'deepseek-v4-pro')
        self.assertEqual(self.client.get('/api/admin/llm').json()['model'],'deepseek-flash')
        with storage.db() as c:self.assertEqual(storage.setting(c,'llm')['api_key'],'saved-secret')

    def test_deepseek_validation_and_test_error_do_not_expose_secrets(self):
        cfg={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'sensitive-secret'}
        for invalid in [{**cfg,'base_url':'https://example.com'},{**cfg,'model':'unknown'}]:
            self.assertEqual(self.client.put('/api/admin/llm',json=invalid).status_code,422)
        self.assertEqual(self.client.post('/api/admin/llm/test',json={**cfg,'api_key':''}).status_code,422)
        with patch('app.main.httpx.Client') as fake:
            request=httpx.Request('POST','https://api.deepseek.com/chat/completions',headers={'Authorization':'Bearer sensitive-secret'})
            response=httpx.Response(401,request=request,text='sensitive-secret')
            fake.return_value.__enter__.return_value.post.side_effect=httpx.HTTPStatusError('sensitive-secret',request=request,response=response)
            result=self.client.post('/api/admin/llm/test',json=cfg)
            self.assertEqual(result.status_code,502)
            self.assertIn('401',result.text)
            self.assertNotIn('sensitive-secret',result.text)

    def test_deepseek_analysis_disables_thinking_and_explains_empty_output(self):
        with storage.db() as c:
            snap=main.analysis_scope(c,'player',1)
            c.execute("INSERT INTO jobs(id,status,created_at) VALUES('empty','running',?)",(storage.now(),))
        with patch('app.main.httpx.Client') as fake:
            provider=fake.return_value.__enter__.return_value
            provider.post.return_value.json.return_value={'choices':[{'finish_reason':'length','message':{'content':''}}]}
            main.run_analysis('empty','player',1,{'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'secret'},snap)
            self.assertEqual(provider.post.call_args.kwargs['json']['thinking'],{'type':'disabled'})
        job=self.client.get('/api/jobs/empty').json()
        self.assertEqual(job['status'],'failed')
        self.assertIn('token',job['error'])

    def test_llm_network_errors_and_explicit_proxy(self):
        for status,hint in [(401,'密钥'),(402,'余额'),(404,'模型'),(429,'限流'),(503,'服务异常')]:
            with self.subTest(status=status):
                request=httpx.Request('POST','https://api.deepseek.com/chat/completions')
                error=httpx.HTTPStatusError('secret',request=request,response=httpx.Response(status,request=request))
                self.assertIn(hint,main.llm_failure_message(error))
                self.assertNotIn('secret',main.llm_failure_message(error))
        self.assertIn('超时',main.llm_failure_message(httpx.ReadTimeout('secret')))
        self.assertIn('网络',main.llm_failure_message(httpx.ConnectError('secret')))
        with patch.dict(main.os.environ,{'LLM_PROXY_URL':'http://192.168.1.10:7890'}),patch('app.main.httpx.Client') as fake:
            main.llm_client()
            self.assertEqual(fake.call_args.kwargs['proxy'],'http://192.168.1.10:7890')
            self.assertFalse(fake.call_args.kwargs['trust_env'])

    def test_deepseek_thinking_configuration_drives_analysis(self):
        cfg={'base_url':'https://api.deepseek.com','model':'deepseek-flash','api_key':'secret',
             'thinking':True,'reasoning_effort':'low'}
        self.assertEqual(self.client.put('/api/admin/llm',json=cfg).status_code,200)
        saved=self.client.get('/api/admin/llm').json()
        self.assertTrue(saved['thinking'])
        self.assertEqual(saved['reasoning_effort'],'low')
        self.assertEqual(self.client.put('/api/admin/llm',json={**cfg,'reasoning_effort':'invalid'}).status_code,422)
        with storage.db() as c:
            snap=main.analysis_scope(c,'player',1)
            c.execute("INSERT INTO jobs(id,status,created_at) VALUES('thinking','running',?)",(storage.now(),))
        with patch('app.main.httpx.Client') as fake:
            fake.return_value.__enter__.return_value.post.return_value.json.return_value={'choices':[{'message':{'content':'样本有限'}}]}
            main.run_analysis('thinking','player',1,cfg,snap)
            payload=fake.return_value.__enter__.return_value.post.call_args.kwargs['json']
            self.assertEqual(payload['thinking'],{'type':'enabled'})
            self.assertEqual(payload['reasoning_effort'],'low')
            self.assertEqual(payload['max_tokens'],16384)
        self.assertEqual(self.client.get('/api/jobs/thinking').json()['status'],'done')

    def test_version_and_assets_are_not_cached(self):
        health=self.client.get('/health')
        self.assertEqual(health.json()['version'],'1.0.9')
        for path in ['/','/health','/static/app.js']:
            response=self.client.get(path)
            self.assertEqual(response.headers['cache-control'],'no-store')
            self.assertEqual(response.headers['x-app-version'],'1.0.9')
        self.assertIn('/static/app.js?v=1.0.9',self.client.get('/').text)

    def test_public_ip_fallback_rejects_private_and_ipv6(self):
        def response(value):
            return type('IPResponse',(),{'text':value,'raise_for_status':lambda self:None})()
        with patch('app.main.httpx.Client') as fake:
            fake.return_value.__enter__.return_value.get.side_effect=[response('192.168.1.1'),response('2606:4700:4700::1111'),response('8.8.8.8')]
            result=self.client.post('/api/admin/public-ip')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['ip'],'8.8.8.8')
            self.assertFalse(fake.call_args.kwargs['trust_env'])
        with patch('app.main.httpx.Client') as fake:
            fake.return_value.__enter__.return_value.get.side_effect=httpx.ConnectTimeout('secret')
            result=self.client.post('/api/admin/public-ip')
            self.assertEqual(result.status_code,502)
            self.assertIn('超时',result.json()['detail'])
            self.assertNotIn('secret',result.text)

    def test_public_ip_parses_json_jsonp_and_nested_json_safely(self):
        samples=['8.8.8.8\n',json.dumps({'ip':'8.8.8.8','loc':'not stored'}),
                 'if(window.IPCallBack) {IPCallBack({"ip":"8.8.8.8","err":""});}',
                 json.dumps({'ret':200,'data':{'ip':'8.8.8.8','city':'not stored'}})]
        for text in samples:
            with self.subTest(text=text):self.assertEqual(main.parse_public_ipv4(text),'8.8.8.8')
        invalid=['192.168.1.1','2606:4700:4700::1111','<html>8.8.8.8</html>',
                 '{"ret":500,"data":{"ip":"8.8.8.8"}}','{"ip":123}',
                 'IPCallBack({"ip":"8.8.8.8"}); malicious();','{"error":"not authorized"}']
        for text in invalid:
            with self.subTest(text=text),self.assertRaises((ValueError,TypeError)):main.parse_public_ipv4(text)

    def test_public_ip_domestic_fallback_only_returns_ip_and_source(self):
        def response(value):
            return type('IPResponse',(),{'text':value,'raise_for_status':lambda self:None})()
        with patch('app.main.httpx.Client') as fake:
            provider=fake.return_value.__enter__.return_value
            provider.get.side_effect=[httpx.ConnectTimeout('secret'),response('{"ret":200,"data":{"ip":"8.8.8.8","loc":"private-location"}}')]
            result=self.client.post('/api/admin/public-ip')
            self.assertEqual(result.status_code,200)
            self.assertEqual(result.json()['source'],'ip9.com.cn')
            self.assertEqual(result.json()['ip'],'8.8.8.8')
            self.assertNotIn('private-location',result.text)
            self.assertEqual(provider.get.call_args_list[0].args[0],'https://whois.pconline.com.cn/ipJson.jsp')
        with storage.db() as c:self.assertNotIn('loc',storage.setting(c,'last_ip'))

    def test_import_preview_atomic_and_duplicate(self):
        text='ended_at,east_user_id,south_user_id,west_user_id,north_user_id,east_score,south_score,west_score,north_score\n2026-10-06 20:00:00,1,2,3,4,40000,30000,20000,10000\n'
        preview=self.client.post('/api/admin/import-preview',json={'csv':text}).json()
        self.assertEqual(preview['errors'],[])
        data={'csv':text,'token':preview['token']}
        self.assertEqual(self.client.post('/api/admin/import',json=data).status_code,200)
        self.assertEqual(self.client.post('/api/admin/import',json=data).status_code,409)
        self.assertEqual(self.client.get('/api/stats').json()['count'],12)

    def test_backup_restorable_and_removes_sessions(self):
        result=self.client.post('/api/admin/backup')
        self.assertEqual(result.status_code,200)
        backup=Path(self.tmp.name)/'restore.db';backup.write_bytes(result.content)
        with closing(sqlite3.connect(backup)) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(c.execute('SELECT count(*) FROM records').fetchone()[0],11)
            self.assertEqual(c.execute('SELECT count(*) FROM sessions').fetchone()[0],0)

    def test_public_ip_and_qr_use_configured_external_port(self):
        self.client.put('/api/admin/access',json={'protocol':'https','external_port':8443,'public_host':''})
        fake_response=type('IPResponse',(),{'text':'8.8.8.8','raise_for_status':lambda self:None})()
        with patch('app.main.httpx.Client') as fake:
            fake.return_value.__enter__.return_value.get.return_value=fake_response
            result=self.client.post('/api/admin/public-ip').json()
        self.assertEqual(result['url'],'https://8.8.8.8:8443')
        self.assertFalse(result['verified'])
        qr=self.client.get('/api/admin/access-qr');self.assertEqual(qr.status_code,200);self.assertIn('<svg',qr.text)

    def test_regroup_preview_rejects_stale(self):
        preview=self.client.get('/api/admin/regroup').json();self.create()
        self.assertEqual(self.client.post('/api/admin/regroup',json={'token':preview['token']}).status_code,409)

    def test_admin_can_split_and_merge_rounds(self):
        r=self.client.post('/api/admin/rounds/assign',json={'record_ids':[6]})
        self.assertEqual(r.status_code,200)
        target=r.json()['round_id']
        self.assertEqual(self.client.get('/api/stats?round_id='+str(target)).json()['count'],1)
        original=self.client.get('/api/records/1').json()['round_id']
        self.assertEqual(self.client.post('/api/admin/rounds/assign',json={'record_ids':[6],'round_id':original}).status_code,200)
        self.assertEqual(self.client.get('/api/stats?round_id='+str(original)).json()['count'],6)

    def test_trend_global_indices_survive_player_and_round_filters(self):
        data=self.client.get('/api/stats').json()
        late=next(p for p in data['players'] if p['user_id']==5)
        self.assertEqual([t['global_index'] for t in late['trend']],[7,8,9,10,11])
        personal=self.client.get('/api/stats?user_id=5').json()
        self.assertEqual([t['global_index'] for t in personal['timeline']],list(range(1,12)))
        self.assertEqual(personal['count'],5)
        second=next(r for r in self.client.get('/api/rounds').json() if r['number']==2)
        round_data=self.client.get('/api/stats?round_id='+str(second['id'])).json()
        self.assertEqual([t['global_index'] for t in round_data['timeline']],[7,8,9,10,11])

    def test_personal_details_use_actual_participation(self):
        p=self.client.get('/api/stats?user_id=5').json()['players'][0]
        self.assertEqual(p['details']['recent']['count'],5)
        self.assertEqual(p['details']['previous']['count'],0)
        self.assertEqual(p['details']['net_total'],-75000)
        self.assertEqual([s['count'] for s in p['details']['seat_stats']],[0,0,0,5])
        self.assertEqual(p['details']['best_game']['global_index'],7)

    def test_global_renumbering_marks_related_analysis_stale(self):
        second=next(r for r in self.client.get('/api/rounds').json() if r['number']==2)
        with storage.db() as c:
            snapshot=main.analysis_scope(c,'round',second['id'])
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at) VALUES('round',?,?,?,'test',?)",(second['id'],snapshot['data_hash'],'历史场次分析',storage.now()))
        self.assertEqual(self.create(ended_at='2025-12-31T20:00:00').status_code,200)
        self.assertTrue(self.client.get('/api/analysis?scope=round&target='+str(second['id'])).json()['analysis']['stale'])

    def test_rule_rename_preserves_scores_and_snapshots(self):
        before=self.client.get('/api/stats').json()
        snapshot=self.client.get('/api/records/1').json()['rules_json']
        self.assertEqual(self.client.put('/api/admin/rules/1/name',json={'name':'测试四麻规则'}).status_code,200)
        after=self.client.get('/api/stats').json()
        self.assertEqual([p['total'] for p in before['players']],[p['total'] for p in after['players']])
        self.assertEqual(before['data_hash'],after['data_hash'])
        self.assertEqual(snapshot,self.client.get('/api/records/1').json()['rules_json'])
        self.assertEqual(after['rule_breakdown'][0]['name'],'测试四麻规则')
        self.login('linyi')
        self.assertEqual(self.client.put('/api/admin/rules/1/name',json={'name':'不能修改'}).status_code,403)

    def test_rules_recalculate_same_raw_games(self):
        self.client.put('/api/admin/rule',json={'name':'无头名奖励','starting':25000,'returning':25000,'uma':[15,5,-5,-15]})
        self.assertEqual(self.create().status_code,200)
        data=self.client.get('/api/stats').json()
        self.assertFalse(data['mixed_rules'])
        self.assertEqual(data['scoring_rule']['name'],'无头名奖励')
        only=self.client.get('/api/stats?rule_id=2').json()
        self.assertEqual(self.client.get('/api/stats?rule_id=1').json()['scoring_rule']['version'],2)
        self.client.post('/api/admin/rules/1/default',json={})
        original=self.client.get('/api/stats').json()
        self.assertEqual(only['count'],12);self.assertEqual(original['count'],12)
        self.assertEqual(only['games'][0]['results'][0]['score'],30)
        self.assertEqual(original['games'][0]['results'][0]['score'],45)
        by_id={p['user_id']:p for p in original['players']}
        for p in only['players']:
            for key in ['count','ranks','average_rank','average_points','first_rate','last_rate','top2_rate']:
                self.assertEqual(p[key],by_id[p['user_id']][key])
            self.assertEqual(p['details']['net_total'],by_id[p['user_id']]['details']['net_total'])
        self.assertNotEqual(only['data_hash'],original['data_hash'])
        self.assertEqual(self.client.get('/api/stats?rule_id=999').json()['scoring_rule']['version'],1)

    def test_rule_crud_default_guard_and_stable_ids(self):
        data={'name':'备用口径','starting':25000,'returning':25000,'uma':[15,5,-5,-15]}
        self.assertEqual(self.client.delete('/api/admin/rules/1').status_code,409)
        new=self.client.post('/api/admin/rules',json=data).json()
        self.assertEqual(new['version'],2)
        self.assertEqual(self.client.get('/api/stats').json()['scoring_rule']['version'],1)
        before=self.client.get('/api/stats').json()
        data.update(name='更新口径',returning=25000)
        self.assertEqual(self.client.put('/api/admin/rules/1',json=data).status_code,200)
        after=self.client.get('/api/stats').json()
        self.assertEqual(after['count'],before['count'])
        self.assertNotEqual(after['players'][0]['total'],before['players'][0]['total'])
        self.assertEqual(self.client.post('/api/admin/rules/2/default',json={}).status_code,200)
        self.assertEqual(self.client.delete('/api/admin/rules/2').json()['default'],1)
        self.assertEqual(self.client.delete('/api/admin/rules/1').status_code,409)
        data['name']='第三口径'
        self.assertEqual(self.client.post('/api/admin/rules',json=data).json()['version'],3)
        with storage.db() as c:
            storage.init_db()
            self.assertEqual(sum(r['current'] for r in storage.rule_catalog(c)),1)
        self.login('linyi')
        self.assertEqual(self.client.post('/api/admin/rules',json=data).status_code,403)
        self.assertEqual(self.client.put('/api/admin/rules/1',json=data).status_code,403)
        self.assertEqual(self.client.delete('/api/admin/rules/1').status_code,403)
        self.assertEqual(self.client.post('/api/admin/rules/1/default',json={}).status_code,403)

    def test_analysis_is_bound_to_rule_and_parameters(self):
        with storage.db() as c:
            snapshot=main.analysis_scope(c,'overall',0)
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at,rule_id,rule_json,analysis_version) VALUES('overall',0,?,'测试解读','test',?,1,?,3)",(snapshot['data_hash'],storage.now(),json.dumps(snapshot['scoring_rule'])))
        self.assertFalse(self.client.get('/api/analysis?rule_id=1').json()['analysis']['stale'])
        other={'name':'另一个口径','starting':25000,'returning':25000,'uma':[15,5,-5,-15]}
        self.client.post('/api/admin/rules',json=other)
        self.assertEqual(self.client.get('/api/analysis?rule_id=2').json()['analysis']['rule_id'],1)
        self.client.post('/api/admin/rules/2/default',json={})
        self.assertIsNone(self.client.get('/api/analysis').json()['analysis'])
        self.client.post('/api/admin/rules/1/default',json={})
        other['name']='原口径改名'
        self.client.put('/api/admin/rules/1',json=other)
        self.assertTrue(self.client.get('/api/analysis?rule_id=1').json()['analysis']['stale'])
        self.assertIn('原口径改名',self.client.get('/api/export?rule_id=1').text)

    def test_coplayer_comparison_and_no_baseline(self):
        data=self.client.get('/api/stats?user_id=1').json()
        peers={p['user_id']:p for p in data['players'][0]['details']['co_players']}
        self.assertNotIn(1,peers)
        self.assertEqual(peers[4]['together']['count'],6)
        self.assertEqual(peers[4]['apart']['count'],5)
        self.assertEqual(peers[2]['together']['count'],11)
        self.assertEqual(peers[2]['apart']['count'],0)
        self.assertTrue(all(v is None for v in peers[2]['difference'].values()))
        self.assertIsNone(peers[2]['apart']['average'])
        self.assertEqual(peers[4]['ahead_rate'],100)
        self.assertFalse(peers[4]['small_sample'])
        together=[];apart=[]
        for game in data['games']:
            own=next(p for p in game['results'] if p['user_id']==1)
            (together if any(p['user_id']==4 for p in game['results']) else apart).append(own)
        self.assertEqual(peers[4]['together']['average'],round(sum(p['score'] for p in together)/6,2))
        self.assertEqual(peers[4]['difference']['average'],round(peers[4]['together']['average']-peers[4]['apart']['average'],2))
        self.assertEqual(peers[4]['points_gap'],20000)
        other=self.client.get('/api/stats?user_id=5').json()['players'][0]['details']['co_players']
        self.assertEqual(len(other),3)
        self.assertTrue(all(p['together']['count']==5 and p['apart']['count']==0 for p in other))
        filtered=self.client.get('/api/stats?user_id=1&game_type=tonpuu').json()
        self.assertEqual(filtered['count'],0)
        self.assertEqual(filtered['players'],[])

    def test_all_scopes_and_export_use_admin_default(self):
        new=self.client.post('/api/admin/rules',json={'name':'统一新口径','starting':25000,'returning':25000,'uma':[15,5,-5,-15]}).json()
        self.client.post('/api/admin/rules/2/default',json={})
        for query in ['','?user_id=1','?round_id=1','?rule_id=1&rule_version=1']:
            data=self.client.get('/api/stats'+query).json()
            self.assertEqual(data['scoring_rule']['version'],2)
        self.assertEqual(self.client.get('/api/records/1?rule_id=1').json()['scoring_rule']['version'],2)
        self.assertIn('统一新口径',self.client.get('/api/export?rule_id=1').text)
        self.assertEqual(self.client.post('/api/records/preview',json=self.game()).json()['rules']['version'],2)
        before=self.client.get('/api/stats?user_id=1').json()['players'][0]['details']['co_players']
        self.client.post('/api/admin/rules/1/default',json={})
        after=self.client.get('/api/stats?user_id=1').json()['players'][0]['details']['co_players']
        self.assertNotEqual(before[0]['together']['average'],after[0]['together']['average'])
        self.assertEqual(before[0]['together']['average_rank'],after[0]['together']['average_rank'])
        self.assertEqual(before[0]['points_gap'],after[0]['points_gap'])

    def test_unused_named_rules_remain_available(self):
        for name in ['测试规则乙','测试规则丙']:
            self.assertEqual(self.client.put('/api/admin/rule',json={'name':name,'starting':25000,'returning':30000,'uma':[15,5,-5,-15]}).status_code,200)
        self.assertEqual([r['version'] for r in self.client.get('/api/rule-versions').json()],[1,2,3])
        self.assertEqual(self.client.put('/api/admin/rules/2/name',json={'name':'测试规则丙'}).status_code,409)


if __name__=='__main__':unittest.main()
