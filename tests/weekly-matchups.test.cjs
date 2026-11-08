const test=require('node:test');
const assert=require('node:assert/strict');
const {tiers,validateDetail,weatherLabel,weatherPanel}=require('../terminal/weekly-matchups.js');

test('weather alert shows source, reviewed metrics and explicit unavailable status',()=>{
  const html=weatherPanel({status:'WATCH',reason:'Screening threshold met',signals:['wind'],metric_reviews:['Review passing'],max_wind_mph:20,min_temperature_f:38,max_precipitation_probability_pct:30,source:{generated_at:'2026-11-06T12:00Z',forecast:{observed_at:'2026-11-06T13:00Z'}}});
  assert.match(html,/Weather review/);assert.match(html,/Peak wind 20 mph/);assert.match(html,/National Weather Service/);
  assert.match(html,/uncalibrated/);
  assert.equal(weatherLabel({status:'UNAVAILABLE'}),'Weather unavailable');
  assert.doesNotMatch(weatherPanel({status:'UNAVAILABLE',reason:'Source failed'}),/No weather flag/);
});

test('game detail has 3/5/8 and anytime TD families without fake tickets',()=>{
  const families=[3,5,8].map((n,i)=>({tier:i+1,leg_count:n,market_family:'mixed',status:'NOT_GENERATED',tickets:[]}));
  families.push({tier:'TD',leg_count:3,market_family:'anytime_td',status:'NOT_GENERATED',tickets:[]});
  const html=tiers({parlays:{reason:'No evidence',tiers:families}});
  assert.equal((html.match(/class="parlay-tier"/g)||[]).length,4);
  for(const count of [3,5,8])assert.match(html,new RegExp(count+' legs'));
  assert.match(html,/Anytime TD/);
  assert.equal((html.match(/Not generated yet/g)||[]).length,4);
});
test('recorded tier escapes identities and keeps frozen quote distinct from live price',()=>{
  const html=tiers({parlays:{reason:'Frozen research',tiers:[{tier:1,leg_count:3,status:'RECORDED_RESEARCH',tickets:[{ticket_id:'<bad>',decision_status:'BLOCKED',legs:[{player_id:'<id>',team_id:'CHI',market:'rushing_yards',line:40}],quote:{decimal_odds:4},settlement_status:'unresolved'}]}]}});
  assert.match(html,/&lt;bad&gt;/);assert.doesNotMatch(html,/<bad>/);
  assert.match(html,/Blocked research/);assert.match(html,/Recorded combined quote/);assert.match(html,/not live prices/);
});
test('game selection rejects another games tickets and unrelated comparison teams',()=>{
  const data={game:{game_id:'one',home:'BAL',away:'JAX'},parlays:{game_id:'one'},matchup:{teams:['JAX','BAL']}};
  assert.doesNotThrow(()=>validateDetail(data,'one'));
  assert.throws(()=>validateDetail({...data,parlays:{game_id:'two'}},'one'),/tickets/);
  assert.throws(()=>validateDetail({...data,matchup:{teams:['CHI','SEA']}},'one'),/Comparison teams/);
});
