const test=require('node:test');
const assert=require('node:assert/strict');
const {rows}=require('../terminal/polymarket-odds.js');
test('American odds are primary; unavailable quotes stay labeled and raw text is escaped',()=>{
 const html=rows({markets:[{question:'A < B',outcomes:[{label:'<script>',american:'+150',status:'AVAILABLE'},{label:'B',american:'-250',status:'STALE'},{label:'C',american:null,status:'NO_ASK'}]}]});
 assert.match(html,/>\+150</);assert.match(html,/>-250</);assert.match(html,/STALE/);assert.match(html,/NO ASK/);
 assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>/);
});
