'use strict';

const personalMetrics = {...metrics, score:['单场结算成绩','score'],points:['终局点棒','points'],net:['单场净点棒','net'],game_rank:['单场顺位','game_rank']};
const rankColors=['#337d67','#91ae79','#c6a568','#bc5b52'];

function ruleName(version) {
  return state.ruleVersions.find(r=>String(r.version)===String(version))?.name||'历史规则';
}

async function rulePanel() {
  const rules=await api('/rules');
  $('#settings-content').innerHTML=`<div class="card-head"><h2>结算规则管理</h2><button class="button primary small" id="add-rule">＋ 新增规则</button></div><p class="notice good">规则是统计口径：同一批终局点棒可按不同规则计算成绩。编辑参数会更新全部统计，原始记录不变；至少保留一套默认规则。管理员设为默认后，全部统计页面统一使用该规则。</p><div class="rule-list">${rules.map(r=>`<section class="rule-card"><div class="card-head"><h3>${esc(r.name)}</h3>${r.current?'<span class="tag">默认</span>':''}</div><p class="subtitle">起始 ${r.starting} · 返还 ${r.returning}<br>Uma ${r.uma.join(' / ')} · Oka ${r.oka}</p><div class="actions"><button class="button small" data-rule-edit="${r.version}">编辑</button>${r.current?'':`<button class="button small" data-rule-default="${r.version}">设为默认</button>`}<button class="button small danger" data-rule-delete="${r.version}" ${rules.length===1?'disabled title="至少保留一套规则"':''}>删除</button></div></section>`).join('')}</div>`;
  $('#add-rule').onclick=()=>ruleModal(null,rules.find(r=>r.current));
  listen('[data-rule-edit]','click',e=>ruleModal(rules.find(r=>r.version===Number(e.currentTarget.dataset.ruleEdit))));
  listen('[data-rule-default]','click',e=>busy(e.currentTarget,async()=>{const id=e.currentTarget.dataset.ruleDefault;await post('/admin/rules/'+id+'/default',{});state.ruleVersion=String(id);await loadBasics();await rulePanel();toast('默认统计口径已更新');}));
  listen('[data-rule-delete]','click',e=>{const r=rules.find(r=>r.version===Number(e.currentTarget.dataset.ruleDelete));openModal('删除结算规则',`<p>删除「${esc(r.name)}」后，原始对局保留。${r.current?'默认项将自动转移至「'+esc(rules.find(x=>x.version!==r.version).name)+'」。':''}</p><div class="form-footer"><button class="button danger" id="confirm-rule-delete">确认删除</button></div>`);$('#confirm-rule-delete').onclick=ev=>busy(ev.currentTarget,async()=>{await api('/admin/rules/'+r.version,{method:'DELETE'});$('#modal').close();await loadBasics();await rulePanel();toast('规则已删除');});});
}

function ruleModal(rule,base) {
  const r=rule||base;
  openModal(rule?'编辑结算规则':'新增结算规则',`<form id="rule-form"><div class="field"><label for="new-rule-name">规则名称</label><input id="new-rule-name" maxlength="40" value="${esc(rule?.name||'')}" required></div><div class="field"><label for="rule-start">实际起始点棒</label><input id="rule-start" value="25000" readonly><small>本项目实际对局固定 25000 点起始，统计规则不改变录入点棒。</small></div><div class="field"><label for="rule-return">返还点</label><input id="rule-return" type="number" min="25000" step="100" value="${r.returning}" required></div><div class="form-grid">${r.uma.map((n,i)=>`<div class="field"><label for="uma-${i}">${i+1} 位 Uma</label><input id="uma-${i}" type="number" value="${n}" required></div>`).join('')}</div><p class="subtitle">Oka 根据返还点自动计算，同点按开局东、南、西、北顺序。${rule?'保存后全部统计采用新参数，原有 AI 分析将标记过期。':'新增后可单独设为默认。'}</p><div class="form-footer"><button class="button primary">保存规则</button></div></form>`);
  $('#rule-form').onsubmit=e=>{e.preventDefault();busy(e.submitter,async()=>{const data={name:$('#new-rule-name').value,starting:25000,returning:Number($('#rule-return').value),uma:[0,1,2,3].map(i=>Number($('#uma-'+i).value))};if(rule)await put('/admin/rules/'+rule.version,data);else await post('/admin/rules',data);$('#modal').close();await loadBasics();await rulePanel();toast('规则已保存');});};
}

function rankingTabs() {
  return `<div class="ranking-tabs"><button class="overall-tab ${state.tab==='overall'?'active':''}" data-tab="overall">综合排行榜</button><div class="round-wheel-wrap ${state.tab==='round'?'active':''}"><div id="round-wheel" role="listbox" aria-label="届次滚轮" tabindex="0">${state.rounds.length?state.rounds.map((r,i)=>`<button type="button" role="option" aria-selected="${state.tab==='round'&&r.id===state.round}" tabindex="-1" data-round="${r.id}"><span class="round-wheel-title">第 ${r.number} 届排行榜</span>${i===0?'<small>最新</small>':''}</button>`).join(''):'<span class="round-wheel-empty">暂无届次</span>'}</div></div></div>`;
}

function bindRoundWheel() {
  const wheel=$('#round-wheel');if(!wheel||wheel.dataset.bound)return;wheel.dataset.bound='true';
  const selected=Math.max(0,state.rounds.findIndex(r=>r.id===state.round));
  wheel.scrollTop=selected*44;
  const choose=index=>{const r=state.rounds[index];if(!r||!wheel.isConnected)return;if(state.tab==='round'&&state.round===r.id)return;state.tab='round';state.round=r.id;state.recordPage=1;wheel.scrollTop=index*44;syncRankingTabs();statsPage().catch(err=>toast(err.message));};
  listen('[data-round]','click',e=>choose(state.rounds.findIndex(r=>r.id===Number(e.currentTarget.dataset.round))));
  let timer;
  wheel.addEventListener('scroll',()=>{clearTimeout(timer);timer=setTimeout(()=>{if(wheel.isConnected&&Math.round(wheel.scrollTop/44)!==Math.max(0,state.rounds.findIndex(r=>r.id===state.round)))choose(Math.round(wheel.scrollTop/44));},180);});
  wheel.addEventListener('keydown',e=>{const i=Math.round(wheel.scrollTop/44);const target=e.key==='ArrowDown'?Math.min(i+1,state.rounds.length-1):e.key==='ArrowUp'?Math.max(0,i-1):e.key==='Home'?0:e.key==='End'?state.rounds.length-1:e.key==='Enter'||e.key===' '?i:null;if(target!==null){e.preventDefault();choose(target);}});
}

function syncRankingTabs() {
  const overall=$('[data-tab="overall"]'),wrap=$('.round-wheel-wrap');
  if(overall)overall.classList.toggle('active',state.tab==='overall');
  if(wrap)wrap.classList.toggle('active',state.tab==='round');
  document.querySelectorAll('[data-round]').forEach(el=>el.setAttribute('aria-selected',String(state.tab==='round'&&Number(el.dataset.round)===state.round)));
}

async function statsPage() {
  const requestId=state.statsRequest=(state.statsRequest||0)+1,page=state.page;
  await loadBasics();
  if(requestId!==state.statsRequest||page!==state.page)return;
  const mine=state.page==='mine',uid=state.viewUser||state.user.id,q=new URLSearchParams();
  if(mine)q.set('user_id',uid);
  if(!mine&&state.tab==='round'&&state.round)q.set('round_id',state.round);
  const data=await api('/stats?'+q);
  if(requestId!==state.statsRequest||page!==state.page)return;
  state.stats=data;
  state.ruleVersion=String(state.stats.scoring_rule.version);
  const d=state.stats,p=d.players.find(p=>p.user_id===uid),leader=d.players[0],r=state.rounds.find(r=>r.id===state.round);
  const sub=mine?'自己的表现，值得更仔细地看。':state.tab==='round'&&r?`第 ${r.number} 届 · ${r.start} 至 ${r.end}`:'所有玩家的战绩，在这里汇合。';
  const kpis=mine?[['参与场数',p?.count||0,'所选范围的有效对局'],['累计成绩',signed(p?.total||0),'含 Uma / Oka'],['平均顺位',p?p.average_rank.toFixed(2):'—','数值越低，顺位越好'],['连对率',p?p.top2_rate+'%':'—','一位 + 二位比例']]:[['有效对局',d.count,'所选统计范围'],['参赛玩家',d.players.length,'包含历史停用玩家'],['累计领先',leader?.nickname||'—',leader?signed(leader.total)+' 分':'暂无对局'],['我的平均顺位',p?p.average_rank.toFixed(2):'—',p?p.count+' 场样本':'尚未参赛']];
  const ranking=mine?`<section class="card"><div class="card-head"><h2>个人走势</h2><small>按全体历史场次对齐</small></div><div id="ranking-content"></div></section>`:`<section class="card"><div class="card-head"><h2>排行榜</h2><div class="segmented"><button id="table-view" class="${state.view==='table'?'active':''}">表格</button><button id="chart-view" class="${state.view==='chart'?'active':''}">折线图</button></div></div><div id="ranking-content"></div></section>`;
  const ai=`<section class="card ai-card" id="ai-section"><div class="card-head"><div class="player-cell"><span class="ai-icon">✦</span><h2>AI 战绩解读</h2></div>${canRecord()?'<button class="button small" id="generate-ai">生成分析</button>':''}</div><div id="ai-content" class="muted">正在读取分析…</div></section>`;
  const distribution=`<section class="card"><div class="card-head"><h2>顺位分布</h2><small>占各玩家自身参赛场数</small></div><div id="rank-distribution"></div></section>`;
  const peers=mine&&p?coplayerDetails(p).replace('card section-gap','card'):'';
  const seats=mine&&p?seatDetails(p).replace('card section-gap','card'):'';
  const statsLayout=`<div class="grid-two ${mine?'personal-stats-layout section-gap':'public-stats-layout'}"><div class="${mine?'personal-stats-left':'public-stats-left'}">${ranking}${distribution}${seats}${peers}</div>${ai}</div>`;
  const header=pageTop(mine?'MY SCOREBOOK':'CLUB SCOREBOARD',mine?(uid===state.user.id?'我的数据':(state.players.find(p=>p.id===uid)?.nickname||'玩家')+'的数据'):'公共战绩',sub,mine?(uid===state.user.id?'<button id="profile-button" class="button">账号设置</button>':''):canRecord()?'<button class="button primary" data-nav="entry">＋ 记一场</button>':'');
  const content=`<div class="kpis">${kpis.map((k,i)=>`<div class="card kpi ${i===0?'featured':''}"><div class="label">${k[0]}</div><div class="value">${esc(k[1])}</div><div class="foot">${esc(k[2])}</div></div>`).join('')}</div>`+
    `<div class="rule-scope-note notice good">统一结算规则：${esc(d.scoring_rule.name)} · 由管理员设置，全部统计页面使用该规则。</div>`+
    (mine&&p?personalDetails(p):'')+statsLayout+
    `<section class="card section-gap"><div class="card-head"><h2>${mine?'我的参赛记录':'对局记录'}</h2><a class="button small" href="/api/export">导出全部 CSV</a></div><div id="records-content"></div></section>`;
  const oldBody=$('#stats-body'),reuse=oldBody?.dataset.page===page;
  const scrollY=window.scrollY;
  if(reuse){
    // Keep the shell, tabs and wheel mounted while replacing statistics.
    $('#stats-header .subtitle').textContent=sub;
    oldBody.style.minHeight=Math.max(0,scrollY+innerHeight-(oldBody.getBoundingClientRect().top+scrollY))+'px';
    oldBody.innerHTML=content;
  }else{
    shell(`<div id="stats-header">${header}</div>`+(!mine?rankingTabs():'')+`<div id="stats-body" data-page="${page}">${content}</div>`);
  }
  if(!mine)bindRoundWheel();
  syncRankingTabs();
  if($('[data-tab="overall"]'))$('[data-tab="overall"]').onclick=()=>{if(state.tab==='overall')return;state.tab='overall';state.recordPage=1;syncRankingTabs();statsPage().catch(err=>toast(err.message));};
  if(!mine){$('#table-view').onclick=()=>{state.view='table';renderRanking(false);};$('#chart-view').onclick=()=>{state.view='chart';renderRanking(true);};}
  renderRanking(mine||state.view==='chart');renderRecords();
  if($('#profile-button'))$('#profile-button').onclick=profileModal;
  const scope=mine?'player':state.tab==='round'?'round':'overall',target=mine?uid:scope==='round'?state.round:0;
  if(reuse)window.scrollTo({top:scrollY,left:0,behavior:'instant'});
  await renderAnalysis(scope,target,state.ruleVersion,requestId);
  if(requestId!==state.statsRequest||page!==state.page)return;
  if($('#generate-ai'))$('#generate-ai').onclick=e=>busy(e.currentTarget,async()=>{const result=await post('/analysis',{scope,target});toast('分析生成中，可继续浏览');pollJob(result.job_id,scope,target,state.ruleVersion);});
}

function personalDetails(p) {
  const a=p.details,rec=a.recent,prev=a.previous;
  const delta=prev.count?rec.average-prev.average:null;
  const best=a.best_game,worst=a.worst_game;
  const cells=[['点棒净变化',signed(a.net_total),'平均每场 '+signed(a.net_average)+' 点'],['正成绩场次',a.positive_count+' / '+p.count,(a.positive_count/p.count*100).toFixed(1)+'% · 结算成绩 > 0'],['成绩波动',fmt(a.score_stddev),'结算成绩标准差，越小越平稳'],['最长连对',a.longest_top2+' 场','按本人参赛顺序，一位或二位'],['最长连一',a.longest_first+' 场','按本人参赛顺序，连续一位'],['终局点棒范围',fmt(p.worst_points)+' ～ '+fmt(p.best_points),'平均终局 '+fmt(p.average_points)+' 点']];
  return `<section class="card personal-detail" id="personal-details"><div class="card-head"><h2>近期状态与个人记录</h2><small>所选范围内</small></div><div class="recent-comparison"><div><span class="muted">最近 ${rec.count} 场平均成绩</span><strong class="${cls(rec.average)}">${signed(rec.average)}</strong><small>平均顺位 ${rec.average_rank.toFixed(2)} · 连对率 ${rec.top2_rate}%</small></div><div><span class="muted">此前最多 10 场平均成绩</span><strong class="${prev.count?cls(prev.average):'muted'}">${prev.count?signed(prev.average):'—'}</strong><small>${prev.count?prev.count+' 场 · 平均顺位 '+prev.average_rank.toFixed(2):'还没有更早的参赛样本'}</small></div></div><p class="chart-note">${delta!==null?'近期平均成绩比前一组 '+signed(delta)+'。两组样本分别为 '+rec.count+' / '+prev.count+' 场，仅比较已记录表现。':'累计参赛超过 10 场后，可对比前一组表现。'}</p><div class="personal-metrics">${cells.map(([label,value,note])=>`<div><span>${label}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></div>`).join('')}</div><div class="game-extremes"><div><span class="tag">最佳单场成绩</span><strong class="positive">${signed(best.score)}</strong><p>${esc(best.time.slice(0,16))} · 历史第 ${best.global_index} 场<br>${best.rank} 位 · ${fmt(best.points)} 点</p></div><div><span class="tag">最低单场成绩</span><strong class="${cls(worst.score)}">${signed(worst.score)}</strong><p>${esc(worst.time.slice(0,16))} · 历史第 ${worst.global_index} 场<br>${worst.rank} 位 · ${fmt(worst.points)} 点</p></div></div></section>`;
}

function seatDetails(p) {
  return `<section class="card section-gap"><div class="card-head"><h2>开局座次表现</h2><small>按实际参赛样本统计</small></div><div class="table-wrap"><table><thead><tr><th>开局座次</th><th>场数</th><th>平均顺位</th><th>平均成绩</th></tr></thead><tbody>${p.details.seat_stats.map((s,i)=>`<tr><td>${seatNames[i]}</td><td>${s.count}</td><td>${s.count?s.average_rank.toFixed(2):'—'}</td><td class="${s.count?cls(s.average):''}">${s.count?signed(s.average):'—'}</td></tr>`).join('')}</tbody></table></div><p class="chart-note">不同座次样本可能较少；这些数据描述已有成绩，不用于推断座次优势或打法。</p></section>`;
}

function coplayerDetails(p) {
  const peers=p.details.co_players||[],fields=[['average','平均成绩','分'],['average_rank','平均顺位',''],['top2_rate','连对率','%'],['first_rate','一位率','%'],['last_rate','四位率','%'],['net_average','平均净点棒','点']];
  const value=(v,unit)=>v===null?'—':fmt(v)+(unit?' '+unit:'');
  return `<section class="card section-gap" id="coplayer-details"><div class="card-head"><h2>同桌玩家的影响</h2><small>按同桌场数排序</small></div><p class="chart-note">比较${p.user_id===state.user.id?'你':esc(playerName(p))}和每位玩家同桌、未同桌时的表现；差值为「同桌 − 未同桌」。平均顺位、四位率越低越好，其余指标越高越好。同桌成员、时间和样本可能不同，差异不表示因果或打法影响。</p><div class="coplayer-list">${peers.map(peer=>`<details class="coplayer-row"><summary><span><strong>${esc(playerName(peer))}</strong><small>同桌 ${peer.together.count} 场 / 未同桌 ${peer.apart.count} 场</small></span><span><strong>${signed(peer.together.average)}</strong><small>同桌平均成绩</small></span></summary><div class="coplayer-body"><p class="notice">${!peer.apart.count?'所有已记录对局均同桌，没有未同桌样本，无法比较差异。':peer.small_sample?'至少一组少于 5 场，样本偏少，请谨慎比较。':'两组样本均不少于 5 场，仍仅描述已记录的表现。'}</p><div class="coplayer-comparison"><div class="coplayer-heading"><span>指标</span><span>同桌</span><span>未同桌</span><span>差值</span></div>${fields.map(([key,label,unit])=>`<div><span>${label}</span><span>${value(peer.together[key],unit)}</span><span>${value(peer.apart[key],unit)}</span><span>${peer.difference[key]===null?'—':signed(peer.difference[key])+(unit==='%'?' 个百分点':unit?' '+unit:'')}</span></div>`).join('')}</div><div class="coplayer-direct"><span>顺位领先率<strong>${fmt(peer.ahead_rate)}%</strong><small>同桌时${p.user_id===state.user.id?'你':esc(playerName(p))}的顺位优于该玩家</small></span><span>平均点棒差<strong>${signed(peer.points_gap)} 点</strong><small>${p.user_id===state.user.id?'你':esc(playerName(p))}的终局点棒 − 该玩家终局点棒</small></span></div></div></details>`).join('')||'<div class="empty">暂无同桌玩家数据。</div>'}</div></section>`;
}

function metricTabs(options,selected,label){
  return `<div class="metric-tabs" role="tablist" aria-label="${label}">${Object.entries(options).map(([key,value])=>`<button type="button" role="tab" aria-selected="${key===selected}" tabindex="${key===selected?0:-1}" data-metric="${key}" class="${key===selected?'active':''}">${value[0]}</button>`).join('')}</div>`;
}
function bindMetricTabs(select){
  listen('[data-metric]','click',e=>{const value=e.currentTarget.dataset.metric;select(value);const active=$('[data-metric="'+value+'"]');active?.focus({preventScroll:true});active?.scrollIntoView({block:'nearest',inline:'nearest'});});
  listen('[data-metric]','keydown',e=>{
    const tabs=[...document.querySelectorAll('[data-metric]')],index=tabs.indexOf(e.currentTarget);
    const next=e.key==='ArrowRight'?(index+1)%tabs.length:e.key==='ArrowLeft'?(index-1+tabs.length)%tabs.length:e.key==='Home'?0:e.key==='End'?tabs.length-1:null;
    if(next!==null){e.preventDefault();tabs[next].click();}
  });
}

function rankValue(p,key) {
  if(key==='total'||key==='average')return signed(p[key]);
  if(key==='average_rank')return p[key].toFixed(2);
  if(['net_total','net_average'].includes(key))return signed(p[key])+' 点';
  if(['average_points','best_points','worst_points'].includes(key))return fmt(p[key])+' 点';
  return fmt(p[key])+'%';
}

function mobileRanking(players) {
  const primary=state.sort,secondary=primary==='average_rank'?'total':'average_rank';
  if(!state.expandedRankUsers)state.expandedRankUsers=new Set();
  return `<div class="mobile-ranking"><div class="mobile-rank-heading"><span>排名 / 玩家</span><span>${metrics[primary][0]}</span><span>${metrics[secondary][0]}</span></div>${players.map((p,i)=>{
    const rank=i&&p[primary]===players[i-1][primary]?players.findIndex(x=>x[primary]===p[primary])+1:i+1;
    const values=[['参赛场数',p.count+' 场'],['累计成绩',signed(p.total)],['平均成绩',signed(p.average)],['平均顺位',p.average_rank.toFixed(2)],['一位率',p.first_rate+'%'],['连对率',p.top2_rate+'%'],['四位率',p.last_rate+'%'],['平均终局点棒',fmt(p.average_points)],['最高终局点棒',fmt(p.best_points)],['最低终局点棒',fmt(p.worst_points)],['累计净点棒',signed(p.net_total)+' 点'],['平均净点棒',signed(p.net_average)+' 点']];
    return `<details class="mobile-rank-row" data-rank-user="${p.user_id}" ${state.expandedRankUsers.has(p.user_id)?'open':''}><summary><span class="mobile-rank-player"><span class="rank-number ${rank===1?'first':''}">${rank}</span><span class="mobile-rank-identity"><strong>${esc(playerName(p))}</strong><small>${p.count} 场 · 点击展开</small></span></span><strong class="mobile-rank-value ${['total','average'].includes(primary)?cls(p[primary]):''}">${rankValue(p,primary)}</strong><span class="mobile-rank-value ${secondary==='total'?cls(p.total):''}">${rankValue(p,secondary)}</span></summary><div class="mobile-rank-details"><dl>${values.map(([label,value])=>`<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl><button class="button small soft" data-view-player="${p.user_id}">查看个人战绩 →</button></div></details>`;
  }).join('')}<p class="chart-note">点击玩家行展开完整数据；相同指标并列排名。</p></div>`;
}

function renderRanking(chart) {
  const mine=state.page==='mine',players=state.stats.players;
  if(!mine){$('#table-view').classList.toggle('active',!chart);$('#chart-view').classList.toggle('active',chart);}
  const selected=()=>[...players];
  $('#ranking-content').innerHTML=chart?'<div id="chart-panel"></div>':'<div id="table-panel"></div>';
  const redraw=()=>{
    const shown=selected();
    if(chart){
      const options=mine?personalMetrics:metrics,metric=mine?(state.personalMetric||'score'):state.metric;
      $('#chart-panel').innerHTML=(mine?`<div class="chart-metric-picker"><label for="chart-metric">走势指标</label><select id="chart-metric">${Object.entries(options).map(([k,v])=>`<option value="${k}" ${k===metric?'selected':''}>${v[0]}</option>`).join('')}</select><button class="button small" id="enlarge-chart">放大</button></div>`:metricTabs(options,metric,'走势指标'))+`<div id="chart-container"></div><p class="chart-note">${mine?'长按图表可横向放大。':''}曲线末尾为玩家昵称。横轴为所有有效历史战绩的统一场次序号；未参赛时累计统计沿用上一值，首次参赛前不绘制。净点棒相对实际起始点计算，不含 Uma / Oka。</p>`;
      if(mine)$('#chart-metric').onchange=e=>{state.personalMetric=e.target.value;drawChart(selected());};
      else bindMetricTabs(value=>{state.metric=value;state.sort=value;redraw();});
      drawChart(shown);
    }else{
      shown.sort((a,b)=>state.sort==='average_rank'||state.sort==='last_rate'?a[state.sort]-b[state.sort]:b[state.sort]-a[state.sort]);
      $('#table-panel').innerHTML=metricTabs(metrics,state.sort,'排名指标')+(shown.length?mobileRanking(shown)+`<div class="desktop-ranking"><p class="chart-note ranking-sort-note">按${metrics[state.sort][0]}排序</p><div class="table-wrap"><table><thead><tr><th>排名 / 玩家</th><th>场数</th><th>累计成绩</th><th>平均成绩</th><th>平均顺位</th><th>一位率</th><th>连对率</th><th>四位率</th><th>累计净点棒</th><th>平均净点棒</th><th>平均点棒</th><th>最高 / 最低点棒</th></tr></thead><tbody>${shown.map((p,i)=>{const rank=i&&p[state.sort]===shown[i-1][state.sort]?shown.findIndex(x=>x[state.sort]===p[state.sort])+1:i+1;return `<tr><td><div class="player-cell"><span class="rank-number ${rank===1?'first':''}">${rank}</span><button class="text-link" data-view-player="${p.user_id}">${esc(playerName(p))}</button></div></td><td>${p.count}</td><td class="score ${cls(p.total)}">${signed(p.total)}</td><td class="${cls(p.average)}">${signed(p.average)}</td><td>${p.average_rank.toFixed(2)}</td><td>${p.first_rate}%</td><td>${p.top2_rate}%</td><td>${p.last_rate}%</td><td>${signed(p.net_total)}</td><td>${signed(p.net_average)}</td><td>${fmt(p.average_points)}</td><td>${fmt(p.best_points)} / ${fmt(p.worst_points)}</td></tr>`;}).join('')}</tbody></table></div><p class="chart-note">比例以各玩家有效参赛场数为分母。</p></div>`:'<div class="empty">该范围暂无有效战绩。</div>');
      listen('[data-view-player]','click',e=>{state.viewUser=Number(e.currentTarget.dataset.viewPlayer);state.page='mine';state.recordPage=1;statsPage().catch(err=>toast(err.message));window.scrollTo(0,0);});
    }
    renderDistribution(shown);
    if(!chart){
      bindMetricTabs(value=>{state.sort=value;state.metric=value;redraw();});
      listen('[data-rank-user]','toggle',e=>{const id=Number(e.currentTarget.dataset.rankUser);if(e.currentTarget.open)state.expandedRankUsers.add(id);else state.expandedRankUsers.delete(id);});
    }
  };
  redraw();
}

function renderDistribution(players) {
  $('#rank-distribution').innerHTML=players.length?`<div class="rank-legend">${rankColors.map((color,i)=>`<span><i style="background:${color}"></i>${i+1} 位</span>`).join('')}</div><div class="rank-dist-list">${players.map(p=>`<div class="rank-dist-row"><div class="rank-dist-name"><strong>${esc(playerName(p))}</strong><small>${p.count} 场</small></div><div><div class="rank-stack" role="img" aria-label="${esc(playerName(p))}：${p.ranks.map((n,i)=>`${i+1}位${n}场`).join('，')}">${p.ranks.map((n,i)=>n?`<span style="width:${n/p.count*100}%;background:${rankColors[i]}" title="${i+1} 位：${n} 场 / ${(n/p.count*100).toFixed(1)}%">${n/p.count>=.14?(n/p.count*100).toFixed(0)+'%':''}</span>`:'').join('')}</div><div class="rank-counts">${p.ranks.map((n,i)=>`<span>${i+1} 位 ${n} 场</span>`).join('')}</div></div></div>`).join('')}</div>`:'<div class="empty">该范围暂无可展示的顺位数据。</div>';
}

function chartSeries(player,timeline,key) {
  const events=new Map(player.trend.map(t=>[t.global_index,t])),single=['score','points','net','game_rank'].includes(key);
  let previous=null;
  return timeline.flatMap(t=>{
    const event=events.get(t.global_index);if(event)previous=event;
    if(!previous||single&&!event)return [];
    return [{...t,value:(event||previous)[key],participated:!!event,played_at:previous.time}];
  });
}

function drawChart(players) {
  $('#chart-container').innerHTML=chartSvg(players);
  const button=$('#enlarge-chart'),svg=$('#chart-container svg');
  if(button){button.disabled=!svg;button.onclick=()=>openChartViewer(players);}
  if(svg&&state.page==='mine')bindChartLongPress(svg,players);
}

(async()=>{try{const d=await api('/me');state.user=d.user;state.csrf=d.csrf;await loadBasics();await navigate('stats');}catch{loginPage();}})();
