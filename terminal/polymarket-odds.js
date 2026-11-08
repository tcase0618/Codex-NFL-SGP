(function(root){
  'use strict';
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function rows(data){
    return (data.markets||[]).map(m=>`<article class="team-card"><h3>${esc(m.question)}</h3><div class="polymarket-outcomes">${m.outcomes.map(o=>`<div class="polymarket-outcome ${o.status==='AVAILABLE'?'':'quote-unavailable'}"><span>${esc(o.label)}</span><strong>${/^[+-]\d+$/.test(o.american||'')?esc(o.american):'—'}</strong><small>${o.status==='AVAILABLE'?'Buy · best ask':esc(o.status.replaceAll('_',' '))}</small>${o.book_timestamp?`<small>Book: ${esc(o.book_timestamp)}</small>`:''}</div>`).join('')}</div><details><summary>Market rules and quote sources</summary><p>${esc(m.resolution_rules)}</p><pre class="evidence-json">${esc(JSON.stringify({market_id:m.market_id,condition_id:m.condition_id,fee_schedule:m.fee_schedule,source:m.source,outcomes:m.outcomes},null,2))}</pre></details></article>`).join('')||'<p class="muted">No available markets in this selection.</p>';
  }
  function show(host,game,selection){
    let page=0,type='all',serial=0,timer=null;
    host.innerHTML='<div class="section-heading"><h2>Polymarket odds</h2></div><p class="muted">American odds · buy-side best ask · before fees. Prediction-market contracts have their own settlement rules.</p><div class="week-controls"><label>Market <select data-poly-type><option value="all">All markets</option></select></label><button class="button secondary" data-poly-refresh>Refresh odds</button></div><p data-poly-status role="status">Loading public order books…</p><div data-poly-rows></div><div class="week-controls"><button class="button secondary" data-poly-prev>Previous</button><span data-poly-page></span><button class="button secondary" data-poly-next>Next markets</button></div>';
    const get=s=>host.querySelector(s);
    async function load(){
      if(!host.isConnected)return;
      clearTimeout(timer);if(document.hidden){timer=setTimeout(load,30000);return;}const token=++serial;
      get('[data-poly-status]').textContent='Refreshing public order books…';
      try{
        const query=new URLSearchParams({...selection,game_id:game.game_id,market_type:type,page});
        const response=await fetch('/api/polymarket/odds?'+query);const data=await response.json();
        if(token!==serial||!host.isConnected)return;
        if(!response.ok)throw Error(data.error||'Odds unavailable');
        if(data.game_id!==game.game_id||data.provider!=='Polymarket')throw Error('Quote identity mismatch');
        const picker=get('[data-poly-type]');picker.innerHTML='<option value="all">All markets</option>'+(data.market_types||[]).map(t=>`<option value="${esc(t)}">${esc(t.replaceAll('_',' '))}</option>`).join('');picker.value=type;
        get('[data-poly-rows]').innerHTML=rows(data);
        get('[data-poly-status]').textContent=`${data.status} · Retrieved ${data.observed_at} · ${data.available_outcomes||0}/${data.total_outcomes||0} fresh outcome quotes. ${data.reason||'Updates every 30 seconds while visible.'}`;
        get('[data-poly-prev]').disabled=page===0;get('[data-poly-next]').disabled=!data.has_more;
        get('[data-poly-page]').textContent=`Page ${page+1} · ${data.total_markets} markets`;
      }catch(error){if(token===serial){get('[data-poly-status]').textContent='Odds unavailable: '+error.message;get('[data-poly-rows]').innerHTML='';}}
      finally{if(token===serial&&host.isConnected)timer=setTimeout(load,30000);}
    }
    get('[data-poly-type]').onchange=e=>{type=e.target.value;page=0;load();};
    get('[data-poly-refresh]').onclick=load;
    get('[data-poly-prev]').onclick=()=>{page=Math.max(0,page-1);load();};
    get('[data-poly-next]').onclick=()=>{page++;load();};load();
  }
  root.PolymarketOdds={show,rows};
  if(typeof module!=='undefined'&&module.exports)module.exports={rows};
})(globalThis);
