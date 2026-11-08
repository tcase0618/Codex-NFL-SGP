(function(root){
  "use strict";
  const pending=new WeakMap();
  const escape=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  function gameDate(game){
    const date=new Date(game.kickoff);
    if(!Number.isFinite(date.getTime()))return "Date not confirmed";
    const options={weekday:"long",month:"long",day:"numeric",timeZone:"America/New_York"};
    const day=new Intl.DateTimeFormat("en-US",options).format(date);
    return game.kickoff_confirmed===false?day+" · Time to be announced":day+" · "+new Intl.DateTimeFormat("en-US",{hour:"numeric",minute:"2-digit",timeZone:"America/New_York",timeZoneName:"short"}).format(date);
  }
  async function show(host,team,{name=t=>t,compare}={}){
    const token={};pending.set(host,token);
    if(!team){host.innerHTML='<div class="info-panel"><h2>Next matchup</h2><p>Select a player with a current roster team to see that team’s next game.</p></div>';return;}
    host.innerHTML='<div class="info-panel"><h2>Next matchup</h2><p role="status">Checking the current schedule…</p></div>';
    try{
      const response=await fetch('/api/next-matchup?'+new URLSearchParams({team}));
      const data=await response.json();
      if(pending.get(host)!==token)return;
      if(!response.ok)throw Error(data.error||"Schedule unavailable");
      if(!data.game){host.innerHTML=`<div class="info-panel"><h2>Next matchup</h2><p>${escape(data.reason||"No upcoming game is listed in the current published schedule.")}</p><button class="button secondary" data-retry>Refresh schedule</button></div>`;host.querySelector('[data-retry]').onclick=()=>show(host,team,{name,compare});return;}
      const game=data.game,opponent=game.opponent;
      const palette=t=>root.TeamVisuals?.theme(t)||{primary:'#18232d',accent:'#b8c9d9'};
      const a=palette(team),b=palette(opponent);
      host.innerHTML=`<div class="section-heading"><div><h2>Next matchup</h2><p class="muted">The next scheduled game, including after a bye. Uses today’s calendar.</p></div><button class="button secondary" data-retry>Refresh</button></div><article class="next-game-card"><div class="next-game-meta"><span>${game.state==="in"?"In progress":`Week ${escape(game.week??"not assigned")}`}</span><span>${escape(gameDate(game))}</span></div><div class="next-game-teams"><div class="next-game-team" data-team-color="${team}"><span>${game.home_away==="home"?"Home":"Away"}</span><h3>${escape(name(team))}</h3></div><span class="next-game-versus">vs</span><div class="next-game-team" data-team-color="${opponent}"><span>${game.home_away==="home"?"Away":"Home"}</span><h3>${escape(game.opponent_name||name(opponent))}</h3></div></div><div class="next-game-footer"><span>${escape(game.venue||"Venue not confirmed")}</span>${compare?'<button class="button secondary" data-compare>Compare this matchup</button>':""}</div></article><details class="schedule-source"><summary>Schedule source</summary><p>ESPN schedule · Retrieved ${escape(new Date(data.source.observed_at).toLocaleString("en-US",{timeZone:"America/New_York"}))} Eastern</p><p>Current team calendar. Historical profile filters do not change this fixture. It does not confirm player participation.</p></details>`;
      host.querySelectorAll('[data-team-color]').forEach(node=>{const tone=palette(node.dataset.teamColor);node.style.setProperty('--side-primary',tone.primary);node.style.setProperty('--side-accent',tone.accent);});
      host.querySelector('[data-retry]').onclick=()=>show(host,team,{name,compare});
      if(compare)host.querySelector('[data-compare]').onclick=()=>compare(team,opponent,game);
    }catch(error){if(pending.get(host)===token)host.innerHTML=`<div class="info-panel"><h2>Next matchup</h2><p role="status">${escape(error.message)}</p></div>`;}
  }
  root.NextMatchup={show,gameDate};
  if(typeof module!=="undefined"&&module.exports)module.exports={gameDate,show};
})(globalThis);
