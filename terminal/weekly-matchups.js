(function(root){
  "use strict";
  const states=new WeakMap();
  const esc=v=>String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const fmt=v=>v==null?"Not observed":Number(v).toLocaleString('en-US',{maximumFractionDigits:3});
  const key=week=>`${week.season_type}:${week.week}`;
  const weatherLabel=w=>({WATCH:'Weather review',NO_SCREEN_TRIGGER:'No weather flag',INDOOR:'Indoor venue',NOT_FUTURE:'Game underway or final',OUTSIDE_FORECAST_WINDOW:'Forecast pending',OUTSIDE_NWS_COVERAGE:'Weather source unavailable',LOCATION_UNVERIFIED:'Location unverified',EXPOSURE_UNVERIFIED:'Roof exposure unverified',STALE_FORECAST:'Forecast stale',PARTIAL_FORECAST:'Forecast incomplete',UNAVAILABLE:'Weather unavailable'}[w?.status]||'Weather check pending');
  function weatherPanel(w){
    if(!w)return '<section class="weather-panel" aria-label="Weather effect alert"><h3>Weather effect alert</h3><p>Forecast check pending.</p></section>';
    const signals=(w.signals||[]).map(s=>`<span class="weather-signal">${esc(s)}</span>`).join('');
    const readings=w.status==='WATCH'||w.status==='NO_SCREEN_TRIGGER'?`<p>Four-hour kickoff window · Peak wind ${w.max_wind_mph==null?'unavailable':esc(w.max_wind_mph)+' mph'} · Lowest temperature ${w.min_temperature_f==null?'unavailable':esc(w.min_temperature_f)+'°F'} · Peak precipitation chance ${w.max_precipitation_probability_pct==null?'unavailable':esc(w.max_precipitation_probability_pct)+'%'}</p>`:'';
    const source=w.source?`<p class="weather-source">National Weather Service · Forecast generated ${esc(w.source.generated_at||'unknown')} · Checked ${esc(w.source.forecast?.observed_at||'unknown')}</p>`:'';
    return `<section class="weather-panel" data-weather-status="${esc(w.status)}" aria-label="Weather effect alert"><div class="weather-panel-head"><h3>Weather effect alert</h3><strong>${esc(weatherLabel(w))}</strong></div><p>${esc(w.reason)}</p>${readings}${signals?`<div class="weather-signals">${signals}</div>`:''}${(w.metric_reviews||[]).map(m=>`<p class="weather-metric">${esc(m)}</p>`).join('')}${source}<p class="weather-disclaimer">Research screen only. These thresholds are uncalibrated and do not change projections or parlay odds.</p></section>`;
  }
  async function loadWeather(host,state){
    try{
      const response=await fetch('/api/week-weather?'+new URLSearchParams(state.selection));
      const payload=await response.json();
      if(!response.ok)throw Error(payload.error||'Weather unavailable');
      if(states.get(host)!==state||payload.season!==state.selection.season||payload.season_type!==state.selection.season_type||payload.week!==state.selection.week)return;
      state.weather=payload.games;
    }catch(_error){if(states.get(host)!==state)return;state.weather={};}
    host.querySelectorAll('[data-weather-game]').forEach(el=>{const w=state.weather[el.dataset.weatherGame];el.textContent=weatherLabel(w);el.dataset.weatherStatus=w?.status||'UNAVAILABLE';});
    const panel=host.querySelector('[data-weather-detail]');if(panel&&state.selected)panel.innerHTML=weatherPanel(state.weather[state.selected]);
  }
  function validateDetail(data,id){
    if(data.game.game_id!==id||data.parlays.game_id!==id)throw Error('Returned game or tickets do not match the selected row');
    if(data.matchup&&[...new Set(data.matchup.teams)].sort().join('|')!==[data.game.home,data.game.away].sort().join('|'))throw Error('Comparison teams do not match the selected game');
  }
  function comparison(data){
    if(!data.matchup)return `<p class="muted">${esc(data.matchup_error||"Team comparison unavailable.")}</p>`;
    return data.matchup.comparisons.map(c=>`<article class="team-card matchup-card"><h3>${esc(c.offense)} offense against ${esc(c.defense)} defense</h3><div class="matchup-table-wrap" tabindex="0" role="region" aria-label="Game team comparison"><table class="matchup-table"><thead><tr><th>Category</th><th data-tone="${esc(c.offense)}">${esc(c.offense)} offense</th><th data-tone="${esc(c.defense)}">${esc(c.defense)} defense</th><th>Combined outlook</th></tr></thead><tbody>${c.factors.map(f=>{const outlook=TeamVisuals.matchupOutlook(f,c.offense,c.defense);const cell=(v,p)=>`${fmt(v.value)} <small>${v.value==null?"":esc(v.unit)}</small><span class="rank-caption">${p?fmt(p.value)+" / 100":"Not ranked"}</span>`;return `<tr><th scope="row">${esc(f.factor)}</th><td>${cell(f.offense,f.offense_percentile)}</td><td>${cell(f.defense,f.defense_percentile)}</td><td><strong data-tone="${esc(outlook.leader||"")}">${esc(outlook.title)}</strong><small class="matchup-outlook-detail">${esc(outlook.detail)}</small></td></tr>`;}).join('')}</tbody></table></div></article>`).join('');
  }
  function tiers(data){
    return `<div class="section-heading"><div><h2>Parlay research</h2><p class="muted">3, 5 and 8 legs, plus a separate three-scorer anytime TD tier. FanDuel · Tennessee. Frozen quotes are not live prices.</p></div></div><div class="tier-grid">${data.parlays.tiers.map(t=>`<article class="parlay-tier"><div class="tier-heading"><h3>${t.market_family==='anytime_td'?'Anytime TD':`Tier ${esc(t.tier)}`}</h3><span>${t.leg_count} legs</span></div>${t.tickets.length?t.tickets.map(ticket=>`<details class="recorded-ticket" open><summary>${esc(ticket.ticket_id)} <span>${ticket.decision_status==='BLOCKED'?'Blocked research':'Recorded research'}</span></summary><ol>${ticket.legs.map(l=>`<li><strong>Player ${esc(l.player_id)}</strong><span>${esc(l.team_id)} · ${esc(l.market.replaceAll('_',' '))} · ${esc(l.line)}</span></li>`).join('')}</ol><p class="muted">${ticket.quote?`Recorded combined quote: ${esc(ticket.quote.decimal_odds)} decimal`:'Combined quote unavailable'} · ${esc(ticket.settlement_status)}</p></details>`).join(''):`<div class="tier-empty"><strong>${t.status==='UNAVAILABLE'?'Unavailable':'Not generated yet'}</strong><p>${t.market_family==='anytime_td'?'Awaiting game-specific scoring opportunity, eligibility and price evidence.':'Awaiting game-specific evidence.'}</p></div>`}</article>`).join('')}</div><p class="team-mini-note">${esc(data.parlays.reason)}</p>`;
  }
  function paint(host){host.querySelectorAll('[data-tone]').forEach(node=>{if(node.dataset.tone)node.style.color=TeamVisuals.theme(node.dataset.tone).accent;});host.querySelectorAll('[data-week-team]').forEach(node=>{const t=TeamVisuals.theme(node.dataset.weekTeam);node.style.setProperty('--side-accent',t.accent);node.style.setProperty('--side-primary',t.primary);});}
  async function select(host,id){
    const state=states.get(host);const token=++state.detailToken;state.selected=id;
    host.querySelectorAll('[data-game]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.game===id)));
    const detail=host.querySelector('[data-game-detail]');detail.hidden=false;detail.innerHTML='<p role="status">Loading game comparison and recorded tiers…</p>';
    const params=new URLSearchParams({...state.selection,game_id:id});
    try{
      const response=await fetch('/api/week-matchup?'+params);const data=await response.json();
      if(states.get(host)!==state||token!==state.detailToken)return;
      if(!response.ok)throw Error(data.error||'Game detail unavailable');
      validateDetail(data,id);
      const g=data.game;
      detail.innerHTML=`<div class="weekly-detail-heading"><div><span class="eyebrow">Selected game · Week ${g.week}</span><h2><span data-tone="${g.away}">${esc(g.away_name)}</span> at <span data-tone="${g.home}">${esc(g.home_name)}</span></h2><p class="muted">${esc(NextMatchup.gameDate(g))}${g.venue?' · '+esc(g.venue):''}</p></div><button class="button secondary" data-back>Back to games</button></div><div data-weather-detail>${weatherPanel(state.weather?.[id])}</div><section data-polymarket-odds aria-label="Polymarket American odds"></section>${tiers(data)}<div class="section-heading"><div><h2>Team matchup</h2><p class="muted">Season-level historical profiles. Profile leads are not predicted winners.</p></div></div>${comparison(data)}`;
      if(root.PolymarketOdds)root.PolymarketOdds.show(detail.querySelector("[data-polymarket-odds]"),g,state.selection);
      paint(detail);detail.querySelector('[data-back]').onclick=()=>host.querySelector('[data-game-list]').scrollIntoView({block:'start',behavior:'smooth'});
      detail.scrollIntoView({block:'start',behavior:'smooth'});
    }catch(error){if(token===state.detailToken)detail.innerHTML=`<p role="status">${esc(error.message)}</p>`;}
  }
  async function show(host,selection=null){
    const old=states.get(host);if(selection===null&&old)selection=old.selection;
    const state={selection:selection||{},detailToken:0};states.set(host,state);
    host.innerHTML='<div class="section-heading"><h2>Weekly matchups</h2></div><p role="status">Loading this week’s NFL games…</p>';
    try{
      const response=await fetch('/api/week-matchups?'+new URLSearchParams(state.selection));const data=await response.json();
      if(states.get(host)!==state)return;
      if(!response.ok)throw Error(data.error||'Weekly calendar unavailable');
      state.selection={season:data.season,season_type:data.season_type,week:data.week};
      const options=data.weeks.length?data.weeks:[{...state.selection,label:`Week ${data.week}`}];
      host.innerHTML=`<div class="section-heading weekly-heading"><div><h2>Weekly matchups</h2><p class="muted">${data.season} · Week ${data.week} · ${data.games.length} games. Select a game for its comparison and 3/5/8/TD research tiers.</p></div><div class="week-controls"><label>Week <select aria-label="Matchup week" data-week>${options.map(w=>`<option value="${key(w)}" ${key(w)===key(data)?'selected':''}>${esc(w.label)}</option>`).join('')}</select></label><button class="button secondary" data-current>This week</button></div></div><div class="weekly-game-list" data-game-list>${data.games.map(g=>`<button class="weekly-game-row" data-game="${esc(g.game_id)}" aria-pressed="false" aria-label="Open ${esc(g.away_name)} at ${esc(g.home_name)}"><span class="weekly-kickoff">${esc(NextMatchup.gameDate(g))}${g.state==='pre'?'':`<small>${esc(g.status_detail)}</small>`}</span><span class="weekly-club" data-week-team="${g.away}"><b>${esc(g.away)}</b><span>${esc(g.away_name)}</span></span><span class="weekly-at">at</span><span class="weekly-club" data-week-team="${g.home}"><b>${esc(g.home)}</b><span>${esc(g.home_name)}</span></span><span class="weekly-open"><span data-weather-game="${esc(g.game_id)}">Weather check pending</span><small>View matchup</small></span></button>`).join('')||'<p class="empty">No games are listed for this week.</p>'}</div>${data.byes.length?`<p class="team-mini-note">Bye: ${data.byes.map(esc).join(', ')}</p>`:''}<section data-game-detail class="weekly-game-detail" hidden></section>`;
      paint(host);host.querySelectorAll('[data-game]').forEach(button=>button.onclick=()=>select(host,button.dataset.game));
      host.querySelector('[data-week]').onchange=event=>{const [kind,week]=event.target.value.split(':');show(host,{season:data.season,season_type:kind,week});};
      host.querySelector('[data-current]').onclick=()=>{states.delete(host);show(host,{});};
      loadWeather(host,state);
    }catch(error){if(states.get(host)===state)host.innerHTML=`<div class="info-panel"><h2>Weekly matchups</h2><p role="status">${esc(error.message)}</p><button class="button secondary" data-retry>Retry</button></div>`;const retry=host.querySelector('[data-retry]');if(retry)retry.onclick=()=>show(host,selection);}
  }
  root.WeeklyMatchups={show,select,tiers};
  if(typeof module!=='undefined'&&module.exports)module.exports={show,select,tiers,validateDetail,weatherLabel,weatherPanel};
})(globalThis);
