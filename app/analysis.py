"""Anonymous evidence and instructions for interpretive score analysis."""
from functools import lru_cache
from pathlib import Path

ANALYSIS_VERSION = 3


@lru_cache(maxsize=3)
def analysis_prompt(scope):
    if scope not in ['overall', 'round', 'player']:
        raise ValueError('invalid analysis scope')
    folder = Path(__file__).parent / 'prompts'
    common = (folder / 'common.md').read_text(encoding='utf-8').strip()
    focus = (folder / (scope + '.md')).read_text(encoding='utf-8').strip()
    return common + '\n\n' + focus


def analysis_evidence(scope, snapshot):
    rule = snapshot['scoring_rule']
    evidence = {'scope': scope, 'games': snapshot['count'], 'scoring_rule': rule, 'players': []}
    outcomes = {}
    for game in snapshot['games']:
        for result in game['results']:
            outcomes.setdefault(result['user_id'], []).append(result)
    for player in snapshot['players']:
        item = {key: value for key, value in player.items() if key not in ['nickname', 'trend']}
        if 'co_players' in item.get('details', {}):
            item['details'] = {**item['details'], 'co_players': [
                {**{key: value for key, value in peer.items() if key != 'nickname'},
                 'player': '[[player:' + str(peer['user_id']) + ']]'}
                for peer in item['details']['co_players']]}
        item['player'] = '[[player:' + str(player['user_id']) + ']]'
        item['recent_trend'] = player['trend'][-20:]
        results = outcomes.get(player['user_id'], [])
        item['rank_outcomes'] = []
        for rank in range(1, 5):
            group = [result for result in results if result['rank'] == rank]
            total = sum(round(result['score'] * 10) for result in group) / 10
            item['rank_outcomes'].append({'rank': rank, 'count': len(group), 'total_pt': total,
                                         'average_pt': round(total / len(group), 2) if group else None})
        item['score_components'] = {
            'points_relative_to_returning_pt': sum((result['points'] - rule['returning']) // 100 for result in results) / 10,
            'uma_pt': sum(rule['uma'][result['rank'] - 1] for result in results),
            'oka_pt': sum(rule['oka'] for result in results if result['rank'] == 1),
        }
        item['negative_end_points_count'] = sum(result['points'] < 0 for result in results)
        evidence['players'].append(item)
    return evidence
