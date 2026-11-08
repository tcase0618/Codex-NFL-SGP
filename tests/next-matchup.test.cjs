const test=require('node:test');
const assert=require('node:assert/strict');
const {gameDate,show}=require('../terminal/next-matchup.js');

test('kickoff date uses Eastern calendar rather than UTC date',()=>{
  const label=gameDate({kickoff:'2026-11-09T01:20:00+00:00',kickoff_confirmed:true});
  assert.match(label,/Sunday, November 8/);
  assert.match(label,/8:20 PM/);
});
test('unconfirmed time stays visibly TBD and invalid date is not formatted',()=>{
  assert.match(gameDate({kickoff:'2027-01-10T05:00Z',kickoff_confirmed:false}),/Time to be announced/);
  assert.equal(gameDate({kickoff:'invalid'}),'Date not confirmed');
});

function host(){
  const buttons={};
  return {innerHTML:'',querySelector:selector=>buttons[selector]||(buttons[selector]={}),querySelectorAll:()=>[]};
}
const fixture=team=>({status:'AVAILABLE',game:{team,opponent:'JAX',opponent_name:'Jacksonville Jaguars',
  kickoff:'2026-11-15T18:00Z',week:10,season:2026,home_away:'away',venue:'Stadium',state:'pre'},
  source:{observed_at:'2026-11-04T17:00Z'}});
test('next matchup forwards exact calendar fixture to comparison and escapes source text',async()=>{
  const original=global.fetch,view=host(),data=fixture('TEN');let selected;
  data.game.opponent_name='<script>not executable</script>';
  global.fetch=async()=>({ok:true,json:async()=>data});
  try{
    await show(view,'TEN',{compare:(...args)=>selected=args});
    assert.match(view.innerHTML,/Week 10/);
    assert.match(view.innerHTML,/&lt;script&gt;/);
    assert.doesNotMatch(view.innerHTML,/<script>/);
    view.querySelector('[data-compare]').onclick();
    assert.deepEqual(selected,['TEN','JAX',data.game]);
  }finally{global.fetch=original;}
});
test('late response for previous selection cannot overwrite next opponent',async()=>{
  const original=global.fetch,view=host();let finishOld;
  global.fetch=async url=>String(url).includes('CHI')?
    new Promise(resolve=>{finishOld=()=>resolve({ok:true,json:async()=>fixture('CHI')});}):
    {ok:true,json:async()=>fixture('TEN')};
  try{
    const first=show(view,'CHI',{name:t=>t});
    await show(view,'TEN',{name:t=>t});
    const latest=view.innerHTML;finishOld();await first;
    assert.equal(view.innerHTML,latest);
    assert.match(view.innerHTML,/>TEN</);
  }finally{global.fetch=original;}
});
test('historical player without current team does not request a current fixture',async()=>{
  const original=global.fetch,view=host();let calls=0;
  global.fetch=async()=>{calls++;throw Error('unexpected request');};
  try{await show(view,null);assert.equal(calls,0);assert.match(view.innerHTML,/current roster team/);}
  finally{global.fetch=original;}
});
