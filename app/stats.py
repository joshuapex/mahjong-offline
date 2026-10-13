import hashlib
import json
import math
from .db import rule_catalog, scoring_rule

SEATS = ['east', 'south', 'west', 'north']


def settle(record, rule=None):
    rule = rule or json.loads(record['rules_json'])
    scores = [record[f'{s}_score'] for s in SEATS]
    order = sorted(range(4), key=lambda i: (-scores[i], i))
    results = []
    for i, seat in enumerate(SEATS):
        rank = order.index(i) + 1
        # Tenths of a score point: integer arithmetic keeps historical totals exact.
        tenths = (scores[i] - rule['returning']) // 100 + rule['uma'][rank - 1] * 10 + (rule['oka'] * 10 if rank == 1 else 0)
        results.append({'user_id': record[f'{seat}_user_id'], 'seat': seat, 'points': scores[i], 'rank': rank, 'score': tenths / 10})
    return results


def records(c, round_id=None, user_id=None, start=None, end=None, game_type=None, rule_version=None):
    sql = "SELECT r.*,d.number round_number FROM records r LEFT JOIN rounds d ON d.id=r.round_id WHERE r.status='active'"
    params = []
    if round_id:
        sql += ' AND r.round_id=?'; params.append(round_id)
    if user_id:
        sql += ' AND ? IN (r.east_user_id,r.south_user_id,r.west_user_id,r.north_user_id)'; params.append(user_id)
    if start:
        sql += ' AND r.created_at>=?'; params.append(start + ' 00:00:00')
    if end:
        sql += ' AND r.created_at<=?'; params.append(end + ' 23:59:59')
    if game_type:
        sql += ' AND r.game_type=?'; params.append(game_type)
    sql += ' ORDER BY r.created_at,r.id'
    return [dict(r) for r in c.execute(sql, params)]


def coplayer_stats(uid, games, performances):
    by_game={p['record_id']:p for p in performances}
    peers={}
    for game in games:
        own=by_game[game['id']]
        for opponent in game['results']:
            if opponent['user_id']==uid: continue
            peer=peers.setdefault(opponent['user_id'],{'user_id':opponent['user_id'],'nickname':opponent['nickname'],'pairs':[]})
            peer['pairs'].append((own,opponent))
    def metrics(items):
        n=len(items)
        return {'count':n,'average':round(sum(p['score'] for p in items)/n,2) if n else None,
                'average_rank':round(sum(p['rank'] for p in items)/n,2) if n else None,
                'top2_rate':round(sum(p['rank']<=2 for p in items)/n*100,2) if n else None,
                'first_rate':round(sum(p['rank']==1 for p in items)/n*100,2) if n else None,
                'last_rate':round(sum(p['rank']==4 for p in items)/n*100,2) if n else None,
                'net_average':round(sum(p['net'] for p in items)/n,1) if n else None}
    result=[]
    for peer in peers.values():
        pairs=peer.pop('pairs');together=[p for p,o in pairs];ids={p['record_id'] for p in together}
        apart=[p for p in performances if p['record_id'] not in ids]
        with_peer=metrics(together);without_peer=metrics(apart)
        delta={k:round(with_peer[k]-without_peer[k],2) if apart else None for k in with_peer if k!='count'}
        result.append({**peer,'together':with_peer,'apart':without_peer,'difference':delta,
                       'ahead_rate':round(sum(p['rank']<o['rank'] for p,o in pairs)/len(pairs)*100,2),
                       'points_gap':round(sum(p['points']-o['points'] for p,o in pairs)/len(pairs),1),
                       'small_sample':len(pairs)<5 or len(apart)<5})
    return sorted(result,key=lambda p:(-p['together']['count'],p['user_id']))


def summary(c, rows, user_id=None, rule=None):
    rule = rule or scoring_rule(c)
    users = {r['id']: dict(r) for r in c.execute('SELECT id,nickname,username,active FROM users')}
    aggregate = {}
    games = []
    history = {r['id']:i for i,r in enumerate(c.execute("SELECT id FROM records WHERE status='active' ORDER BY created_at,id"),1)}
    timeline = [{'record_id':r['id'],'global_index':history[r['id']],'time':r['created_at']} for r in rows]
    for row in rows:
        result = settle(row,rule)
        game = {k: row[k] for k in ['id','created_at','created_by','round_id','round_number','note','game_type']}
        game['results'] = [{**p, 'nickname': users[p['user_id']]['nickname'] or users[p['user_id']]['username']} for p in result]
        game['rules'] = rule
        game['global_index'] = history[row['id']]
        if not user_id or any(p['user_id']==user_id for p in result):
            games.append(game)
        for p in result:
            uid = p['user_id']
            if user_id and uid != user_id:
                continue
            a = aggregate.setdefault(uid, {'user_id': uid, 'nickname': users[uid]['nickname'] or users[uid]['username'], 'count': 0, 'total_tenths': 0, 'rank_total': 0, 'points_total': 0, 'ranks': [0]*4, 'best_points': None, 'worst_points': None, 'trend': [], 'performances': []})
            a['count'] += 1
            a['total_tenths'] += round(p['score'] * 10)
            a['rank_total'] += p['rank']
            a['points_total'] += p['points']
            a['ranks'][p['rank']-1] += 1
            a['best_points'] = max(p['points'], a['best_points'] if a['best_points'] is not None else p['points'])
            a['worst_points'] = min(p['points'], a['worst_points'] if a['worst_points'] is not None else p['points'])
            n = a['count']
            net = p['points'] - json.loads(row['rules_json'])['starting']
            a['net_total'] = a.get('net_total',0) + net
            a['performances'].append({**p, 'record_id':row['id'], 'global_index':history[row['id']], 'time':row['created_at'], 'net':net, 'round_number':row['round_number']})
            a['trend'].append({'record_id': row['id'], 'global_index': history[row['id']], 'time': row['created_at'], 'score':p['score'],'points':p['points'],'net':net,'game_rank':p['rank'],'total': a['total_tenths']/10, 'average': round(a['total_tenths']/10/n, 2), 'rank': round(a['rank_total']/n, 2), 'first_rate': round(a['ranks'][0]/n*100, 2), 'last_rate': round(a['ranks'][3]/n*100, 2), 'top2_rate': round(sum(a['ranks'][:2])/n*100, 2)})
            a['trend'][-1].update(net_total=a['net_total'],net_average=round(a['net_total']/n,1),
                                  average_points=round(a['points_total']/n,1),best_points=a['best_points'],worst_points=a['worst_points'])
    for a in aggregate.values():
        n = a['count']
        a.update(total=a['total_tenths']/10, average=round(a['total_tenths']/10/n, 2), average_rank=round(a['rank_total']/n, 2), average_points=round(a['points_total']/n, 1), first_rate=round(a['ranks'][0]/n*100, 2), last_rate=round(a['ranks'][3]/n*100, 2), top2_rate=round(sum(a['ranks'][:2])/n*100, 2))
        a['net_average']=round(a['net_total']/n,1)
        del a['total_tenths']
        perf=a.pop('performances')
        def window(items):
            size=len(items)
            return {'count':size,'average':round(sum(p['score'] for p in items)/size,2) if size else None,'average_rank':round(sum(p['rank'] for p in items)/size,2) if size else None,'top2_rate':round(sum(p['rank']<=2 for p in items)/size*100,2) if size else None}
        longest_first=longest_top2=current_first=current_top2=0
        for p in perf:
            current_first=current_first+1 if p['rank']==1 else 0
            current_top2=current_top2+1 if p['rank']<=2 else 0
            longest_first=max(longest_first,current_first);longest_top2=max(longest_top2,current_top2)
        a['details']={
            'recent':window(perf[-10:]),'previous':window(perf[-20:-10]),
            'net_total':sum(p['net'] for p in perf),'net_average':round(sum(p['net'] for p in perf)/n,1),
            'positive_count':sum(p['score']>0 for p in perf),'negative_count':sum(p['score']<0 for p in perf),'zero_count':sum(p['score']==0 for p in perf),
            'score_stddev':round(math.sqrt(sum((p['score']-a['total']/n)**2 for p in perf)/n),2),
            'best_game':max(perf,key=lambda p:p['score']),'worst_game':min(perf,key=lambda p:p['score']),
            'longest_first':longest_first,'longest_top2':longest_top2,
            'seat_stats':[{'seat':seat,'count':len(items),'average_rank':round(sum(p['rank'] for p in items)/len(items),2) if items else None,'average':round(sum(p['score'] for p in items)/len(items),2) if items else None} for seat in SEATS for items in [[p for p in perf if p['seat']==seat]]],
        }
        if user_id:
            a['details']['co_players']=coplayer_stats(a['user_id'],games,perf)
    fingerprint_rows = [{**{k:r[k] for k in ['id','created_at','game_type','round_id'] + [f'{s}_{x}' for s in SEATS for x in ['user_id','score']]},'global_index':history[r['id']]} for r in rows]
    digest = hashlib.sha256(json.dumps({'records':fingerprint_rows,'rule':{k:rule[k] for k in ['version','starting','returning','uma','oka','tie']}}, sort_keys=True).encode()).hexdigest()
    counts={}
    for g in games:counts[g['rules']['version']]=counts.get(g['rules']['version'],0)+1
    catalog={r['version']:r['name'] for r in rule_catalog(c)}
    breakdown=[{'version':version,'name':catalog.get(version,'历史规则'),'count':count} for version,count in sorted(counts.items())]
    for g in games:g['rule_name']=catalog.get(g['rules']['version'],'历史规则')
    return {'players': sorted(aggregate.values(), key=lambda x: -x['total']), 'games': list(reversed(games)), 'count': len(games), 'timeline':timeline,'data_hash': digest, 'mixed_rules': False,'rule_breakdown':breakdown,'scoring_rule':rule}
