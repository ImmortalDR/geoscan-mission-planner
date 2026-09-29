const assert = require('node:assert/strict');
const E = require('../h3/editor.js');
assert.deepEqual(E.names([{id:'old'},{id:'Job_0'},{id:'other'}], 'Job'), ['Job_1','Job_0','Job_2']);
assert.equal(E.nextName('Job',['Job_0','Job_2']), 'Job_1');
const sites = [{id:'A',role:'both'},{id:'B',role:'both'},{id:'S',role:'start'},{id:'L',role:'landing'},{id:'R',role:'reserve'},{id:'C',role:'both',candidate:true}];
assert.deepEqual(E.refuels({start_site:'A',landing_site:'A'},sites), ['A']);
assert.deepEqual(E.refuels({start_site:null,landing_site:null},sites), ['A','B']);
assert.deepEqual(E.refuels({start_site:'S',landing_site:'L'},sites), []);
assert.deepEqual(E.eligible(sites,'landing_site'), ['A','B','L']);
assert.equal(E.choose({start_site:null,landing_site:null},sites,'landing_site','R',true),false);
const configs=[];
for (const start of [null,'A','B','S']) for (const end of [null,'A','B','L']) for (const refuel of [null,[],['A'],['B'],['A','B']]) {
 const u={start_site:start,landing_site:end,refuel_sites:structuredClone(refuel)};
 for (const column of ['start_site','landing_site','refuel_sites']) for (const id of [null,...sites.map(s=>s.id)]) for (const checked of [true,false]) {
  const v=structuredClone(u); const before=JSON.stringify(v); const ok=E.choose(v,sites,column,id,checked);
  if(!ok) assert.equal(JSON.stringify(v),before);
  for(const c of ['start_site','landing_site']) assert(v[c]===null || E.eligible(sites,c).includes(v[c]));
  assert(v.refuel_sites===null || v.refuel_sites.every(x=>E.eligible(sites,'refuel_sites').includes(x)));
  if(v.refuel_sites!==null) assert.equal(v.refuel_sites.length,new Set(v.refuel_sites).size);
 }
 configs.push(u);
}
const legacy={start_site:'A',landing_site:'A'};
E.choose(legacy,sites,'start_site','B',true);
assert.deepEqual(legacy,{start_site:'B',landing_site:'A',refuel_sites:['A']});
const broken={start_site:'A',landing_site:'B',refuel_sites:['A','B','C','missing']};
E.reconcile(broken,sites.filter(s=>s.id!=='A'));
assert.deepEqual(broken,{start_site:null,landing_site:'B',refuel_sites:['B']});
if(process.argv[2]) require('node:fs').writeFileSync(process.argv[2],JSON.stringify({sites,configs}));
console.log('PASS: 3360 table transitions, 80 endpoint/refuel configurations, names, legacy policy and deletion repair');
