import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import bcrypt
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
        self.assertIn('新的昵称',self.client.get('/api/analysis?scope=player&target=1').json()['analysis']['text'])
        self.assertIn('新的昵称',self.client.get('/api/stats').text)

    def test_last_admin_and_user_deletion(self):
        u=self.client.get('/api/users').json()[0]
        self.assertEqual(self.client.put('/api/users/1',json={**u,'role':'user'}).status_code,409)
        self.assertEqual(self.client.delete('/api/users/1').status_code,409)
        r=self.client.delete('/api/users/5');self.assertTrue(r.json()['disabled'])
        self.assertEqual(self.client.get('/api/stats').json()['count'],11)

    def test_llm_key_is_write_only(self):
        cfg={'base_url':'https://example.com/v1','model':'test-model','api_key':'very-private-key'}
        self.assertEqual(self.client.put('/api/admin/llm',json=cfg).status_code,200)
        self.assertNotIn('very-private-key',self.client.get('/api/admin/llm').text)
        self.assertNotIn('very-private-key',self.client.get('/api/admin/audit').text)
        self.assertEqual(self.client.put('/api/admin/llm',json={**cfg,'api_key':''}).status_code,200)
        self.assertTrue(self.client.get('/api/admin/llm').json()['has_key'])

    def test_analysis_staleness_and_duplicate_job(self):
        with storage.db() as c:
            snap=main.analysis_scope(c,'overall',0)
            storage.set_setting(c,'llm',{'base_url':'https://example.com/v1','model':'test','api_key':'private'})
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at) VALUES('overall',0,?,'统计总结','test',?)",(snap['data_hash'],storage.now()))
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
            c.execute("INSERT INTO analyses(scope,target,data_hash,payload,model,created_at,rule_id,rule_json) VALUES('overall',0,?,'测试解读','test',?,1,?)",(snapshot['data_hash'],storage.now(),json.dumps(snapshot['scoring_rule'])))
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
