'use strict';

function chartSvg(players,expanded=false,width=620,height=250) {
  const mine=state.page==='mine',metric=mine?(state.personalMetric||'score'):state.metric,defs=mine?personalMetrics:metrics,key=defs[metric][1],timeline=state.stats.timeline;
  if(!players.length||!timeline.length)return '<div class="empty">该范围没有勾选玩家的战绩。</div>';
  const series=players.map(p=>({player:p,values:chartSeries(p,timeline,key)})),values=series.flatMap(s=>s.values.map(t=>t.value));
  let lo=Math.min(0,...values),hi=Math.max(...values);if(hi===lo)hi=lo+1;
  const gap=expanded?22:16,first=timeline[0].global_index,last=timeline[timeline.length-1].global_index,W=width,H=Math.max(height,series.length*gap+50),L=expanded?65:50,R=expanded?Math.min(210,W*.25):115,T=20,B=30,x=index=>L+(index-first)/Math.max(1,last-first)*(W-L-R),y=value=>T+(hi-value)/(hi-lo)*(H-T-B);
  let svg=`<svg class="chart ${expanded?'chart-expanded':''}" viewBox="0 0 ${W} ${H}" role="img" tabindex="0" aria-label="${esc(defs[metric][0])}走势，历史第${first}至${last}场" data-first-index="${first}" data-last-index="${last}">`;
  for(let n=0;n<5;n++){const v=lo+(hi-lo)*n/4,yy=y(v);svg+=`<line x1="${L}" y1="${yy}" x2="${W-R}" y2="${yy}" stroke="#e5e8dd"/><text x="${L-8}" y="${yy+3}" text-anchor="end">${fmt(Math.round(v*10)/10)}</text>`;}
  const ticks=[...new Set([first,...Array.from({length:3},(_,i)=>Math.round(first+(last-first)*(i+1)/4)),last])];
  ticks.forEach(index=>{svg+=`<text x="${x(index)}" y="${H-5}" text-anchor="${index===first?'start':index===last?'end':'middle'}">第 ${index} 场</text>`;});
  const labels=[];
  for(const {player:p,values:list} of series){
    const color=chartColor(state.stats.players.findIndex(t=>t.user_id===p.user_id));
    svg+=`<polyline data-player="${p.user_id}" fill="none" stroke="${color}" stroke-width="2.5" stroke-linejoin="round" points="${list.map(t=>x(t.global_index)+','+y(t.value)).join(' ')}"/>`;
    for(const t of list)if(t.participated)svg+=`<circle data-player="${p.user_id}" data-global-index="${t.global_index}" cx="${x(t.global_index)}" cy="${y(t.value)}" r="4" fill="${color}" tabindex="0"><title>${esc(p.nickname)} · 历史第 ${t.global_index} 场 · ${esc(t.time)} · ${t.value}${metric.includes('rate')?'%':''}</title></circle>`;
    if(list.length){const end=list[list.length-1];labels.push({player:p,color,x:x(end.global_index),y:y(end.value),position:y(end.value)});}
  }
  labels.sort((a,b)=>a.y-b.y);
  labels.forEach((label,i)=>{label.position=Math.max(T,label.y,i?labels[i-1].position+gap:T);});
  if(labels.length&&labels[labels.length-1].position>H-B){labels[labels.length-1].position=H-B;for(let i=labels.length-2;i>=0;i--)labels[i].position=Math.min(labels[i].position,labels[i+1].position-gap);}
  for(const label of labels){
    const labelX=W-R+12,font=expanded?14:12,textWidth=Array.from(label.player.nickname).reduce((n,c)=>n+(c.codePointAt(0)>255?font:font*.65),0),fit=textWidth>R-20?` textLength="${R-20}" lengthAdjust="spacingAndGlyphs"`:'';
    svg+=`<path class="chart-label-connector" d="M ${label.x} ${label.y} L ${W-R+5} ${label.position} L ${labelX-3} ${label.position}" fill="none" stroke="${label.color}" stroke-width="1" opacity=".6"/><text class="chart-end-label" data-player="${label.player.user_id}" x="${labelX}" y="${label.position}" dominant-baseline="middle" style="fill:${label.color}"${fit}>${esc(label.player.nickname)}<title>${esc(label.player.nickname)}</title></text>`;
  }
  return svg+'</svg>';
}

function bindChartLongPress(svg,players) {
  let press=null;
  const cancel=()=>{if(press){clearTimeout(press.timer);press=null;}};
  svg.addEventListener('contextmenu',event=>event.preventDefault());
  svg.addEventListener('pointerdown',event=>{
    if(!event.isPrimary||event.button!==0)return;
    cancel();
    press={id:event.pointerId,x:event.clientX,y:event.clientY,timer:setTimeout(()=>{
      if(!press)return;
      press.opened=true;openChartViewer(players,press.id);
    },550)};
    try{svg.setPointerCapture(event.pointerId);}catch{}
  });
  svg.addEventListener('pointermove',event=>{if(press&&event.pointerId===press.id&&Math.hypot(event.clientX-press.x,event.clientY-press.y)>12)cancel();});
  svg.addEventListener('pointerup',()=>{
    // Touch activation may start on release: retry native fullscreen with that gesture.
    if(press?.opened&&state.chartViewer)requestChartFullscreen(state.chartViewer);
    cancel();
  });
  svg.addEventListener('pointercancel',cancel);
  svg.addEventListener('lostpointercapture',cancel);
  svg.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();openChartViewer(players);}});
}

async function requestChartFullscreen(viewer) {
  if(!viewer||state.chartViewer!==viewer||viewer.pending||document.fullscreenElement===viewer.element)return;
  if(!viewer.element.requestFullscreen){viewer.hint.textContent='已横向放大；当前浏览器不支持系统全屏，可转动手机查看。';return;}
  viewer.pending=true;
  try{
    await viewer.element.requestFullscreen({navigationUI:'hide'});
    if(state.chartViewer!==viewer){if(document.fullscreenElement===viewer.element)await document.exitFullscreen();return;}
    viewer.native=true;viewer.fullscreenButton.hidden=true;
    viewer.hint.textContent='点击关闭或按 Esc 返回';
    if(viewer.mobile&&screen.orientation?.lock){
      try{await screen.orientation.lock('landscape');viewer.locked=true;if(state.chartViewer!==viewer)screen.orientation.unlock();}
      catch{if(state.chartViewer===viewer)viewer.hint.textContent='已全屏；若未自动横屏，可转动手机查看。';}
    }
  }catch{if(state.chartViewer===viewer)viewer.hint.textContent='已横向放大；可点击全屏重试，或转动手机查看。';}
  finally{viewer.pending=false;}
}

function openChartViewer(players,gesturePointer=null) {
  if(state.chartViewer||!players.length)return;
  const focus=document.activeElement,element=document.createElement('section');
  element.className='chart-viewer';element.setAttribute('role','dialog');element.setAttribute('aria-modal','true');element.setAttribute('aria-labelledby','chart-viewer-title');
  const mine=state.page==='mine',metric=mine?(state.personalMetric||'score'):state.metric,defs=mine?personalMetrics:metrics;
  element.innerHTML=`<div class="chart-viewer-stage"><header class="chart-viewer-header"><div><h2 id="chart-viewer-title">${esc(defs[metric][0])}</h2><p id="chart-viewer-hint">横向放大 · 曲线末尾显示昵称</p></div><div class="actions"><button class="button small" id="chart-native-fullscreen">全屏</button><button class="button small primary" id="close-chart-viewer">关闭</button></div></header><div class="chart-viewer-plot"></div></div>`;
  document.body.append(element);
  const viewer={element,focus,players,hint:element.querySelector('#chart-viewer-hint'),fullscreenButton:element.querySelector('#chart-native-fullscreen'),mobile:matchMedia('(pointer:coarse)').matches||window.innerWidth<=700,overflow:document.body.style.overflow,native:false,locked:false,pending:false};
  state.chartViewer=viewer;
  $('#app').inert=true;document.body.style.overflow='hidden';
  const plot=element.querySelector('.chart-viewer-plot');
  viewer.redraw=()=>{
    if(state.chartViewer!==viewer)return;
    const stage=element.querySelector('.chart-viewer-stage'),header=element.querySelector('.chart-viewer-header');
    const width=Math.max(480,stage.clientWidth-32),height=Math.max(220,stage.clientHeight-header.offsetHeight-24);
    plot.innerHTML=chartSvg(players,true,width,height);
    plot.querySelector('svg')?.style.setProperty('height',Math.max(height,players.length*22+50)+'px');
  };
  viewer.observer=new ResizeObserver(viewer.redraw);viewer.observer.observe(element.querySelector('.chart-viewer-stage'));viewer.redraw();
  viewer.onFullscreen=()=>{if(viewer.native&&document.fullscreenElement!==element)closeChartViewer(viewer);};
  viewer.onKey=event=>{
    if(event.key==='Escape'){event.preventDefault();closeChartViewer(viewer);}
    if(event.key==='Tab'){
      const buttons=Array.from(element.querySelectorAll('button')).filter(b=>!b.hidden),first=buttons[0],last=buttons[buttons.length-1];
      if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
    }
  };
  document.addEventListener('fullscreenchange',viewer.onFullscreen);document.addEventListener('keydown',viewer.onKey);
  viewer.onGestureRelease=event=>{if(event.pointerId===gesturePointer){document.removeEventListener('pointerup',viewer.onGestureRelease,true);if(state.chartViewer===viewer)requestChartFullscreen(viewer);}};
  if(gesturePointer!==null)document.addEventListener('pointerup',viewer.onGestureRelease,true);
  element.querySelector('#close-chart-viewer').onclick=()=>closeChartViewer(viewer);
  viewer.fullscreenButton.onclick=()=>requestChartFullscreen(viewer);
  element.querySelector('#close-chart-viewer').focus({preventScroll:true});
  requestChartFullscreen(viewer);
}

function closeChartViewer(viewer=state.chartViewer) {
  if(!viewer||state.chartViewer!==viewer)return;
  state.chartViewer=null;viewer.observer.disconnect();
  document.removeEventListener('fullscreenchange',viewer.onFullscreen);document.removeEventListener('keydown',viewer.onKey);
  document.removeEventListener('pointerup',viewer.onGestureRelease,true);
  if(viewer.locked)try{screen.orientation.unlock();}catch{}
  if(document.fullscreenElement===viewer.element)document.exitFullscreen().catch(()=>{});
  viewer.element.remove();$('#app').inert=false;document.body.style.overflow=viewer.overflow;
  if(viewer.focus?.isConnected)viewer.focus.focus({preventScroll:true});
}
