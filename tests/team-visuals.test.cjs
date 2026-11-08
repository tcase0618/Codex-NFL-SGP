const test=require('node:test');
const assert=require('node:assert/strict');
const {colors,theme,rankInfo,matchupOutlook,luminance}=require('../terminal/team-visuals.js');
const {playerTeam,applyTheme}=require('../terminal/team-visuals.js');

test('all 32 team accents remain readable against raised dark surfaces',()=>{
  assert.equal(Object.keys(colors).length,32);
  for(const team of Object.keys(colors)) {
    const t=theme(team);
    assert.match(t.primary,/^#[a-f\d]{6}$/i);
    assert.ok((luminance(t.accent)+.05)/(luminance('#111317')+.05)>=4.5,team);
  }
  assert.notEqual(theme('CHI').accent,theme('SEA').accent);
});
test('raw defensive direction does not reverse an already-normalized rank twice',()=>{
  const r=rankInfo({direction:'lower',percentile:{value:90,peers:32}});
  assert.equal(r.value,90);
  assert.match(r.directionLabel,/Lower raw/);
  assert.match(r.explanation,/always better/);
  assert.equal(r.band,'Top quarter');
});
test('zero rank is real; missing and contextual ranks do not imply strength',()=>{
  assert.equal(rankInfo({direction:'higher',percentile:{value:0}}).band,'Bottom quarter');
  assert.equal(rankInfo({percentile:null}).value,null);
  assert.equal(rankInfo({direction:'context',percentile:{value:99}}).band,'Context only');
  assert.match(rankInfo({direction:'context'}).explanation,/not better/);
});
const factor=(name,a,b)=>({factor:name,offense:{direction:'higher'},defense:{direction:'lower'},
  offense_percentile:{value:a},defense_percentile:{value:b}});
test('combined outlook retains historical rank context and never supplies predictions',()=>{
  const a=matchupOutlook(factor('Passing efficiency',85,40),'CHI','SEA');
  assert.equal(a.leader,'CHI');assert.equal(a.projection,null);
  assert.equal(a.status,'DESCRIPTIVE_ONLY');
  assert.match(a.detail,/45.0 percentile-point gap/);
  assert.equal(matchupOutlook(factor('Passing efficiency',40,85),'CHI','SEA').leader,'SEA');
  assert.equal(matchupOutlook(factor('Sack exposure',50,50),'CHI','SEA').leader,null);
});
test('unlike populations and missing ranks never produce a combined profile lead',()=>{
  assert.equal(matchupOutlook({...factor('Passing efficiency',99,1),comparable:false},'CHI','SEA').status,'CONTEXT_ONLY');
  assert.equal(matchupOutlook(factor('Explosive passing: completion / dropback',99,1),'CHI','SEA').status,'CONTEXT_ONLY');
  assert.equal(matchupOutlook(factor('Red-zone pass offense / overall defense',99,1),'CHI','SEA').leader,null);
  const missing=factor('Passing efficiency',90,60);missing.defense_percentile=null;
  assert.equal(matchupOutlook(missing,'CHI','SEA').status,'UNAVAILABLE');
});
test('matched explosive and red-zone metrics accept explicit common-population contract',()=>{
  for(const name of ['Explosive passing per dropback','Red-zone passing efficiency','Red-zone rushing efficiency']){
    const f={...factor(name,80,40),comparable:true};
    assert.equal(matchupOutlook(f,'CHI','SEA').status,'DESCRIPTIVE_ONLY');
    f.offense.value=.1;f.defense.value=.2;f.defense_percentile=null;
    const result=matchupOutlook(f,'CHI','SEA');
    assert.equal(result.title,'Observed matchup context');
    assert.match(result.detail,/definitions match/);
  }
});
test('player theme follows explicit current team instead of old observed teams',()=>{
  assert.equal(playerTeam({player:{team:'BAL',is_current:true},observed_teams:['MIN']}),'BAL');
  assert.equal(playerTeam({player:{team:'UNKNOWN',is_current:true},observed_teams:['CHI']}),null);
  assert.equal(playerTeam({player:{team:'BAL',is_current:false},observed_teams:['MIN']}),'MIN');
  assert.equal(playerTeam({player:{team:'BAL',is_current:false},observed_teams:['MIN','BAL']}),null);
  assert.equal(playerTeam(null),null);
});
test('selecting another player replaces all theme tokens without altering profile data',()=>{
  const properties={},node={style:{setProperty:(key,value)=>properties[key]=value},dataset:{}};
  const first=applyTheme(node,'CHI');
  assert.equal(properties['--team-accent'],first.accent);
  const next=applyTheme(node,'SEA');
  assert.equal(node.dataset.team,'SEA');
  assert.equal(properties['--team-primary'],next.primary);
  assert.notEqual(first.accent,next.accent);
  applyTheme(node,null);
  assert.equal(node.dataset.team,'NFL');
  assert.equal(properties['--team-accent'],theme(null).accent);
});
