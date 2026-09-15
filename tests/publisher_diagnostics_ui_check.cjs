// Node VM + DOM/fetch fixtures only. Does not start Chrome or a server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const elements = new Map();
function element(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    value: '', textContent: '', innerHTML: '', disabled: false, open: false,
    elements: { namedItem: name => element(selector + ':' + name) },
    querySelectorAll: () => [], classList: {toggle(){}},
    showModal(){this.open=true;}, close(){this.open=false;}
  });
  return elements.get(selector);
}
const account = {id:1, name:'Fixture', active:true, browser_profile:'fixture', auth_status:'LOGIN_IN_PROGRESS', login_active:true};
const snapshot = {accounts:[account],drafts:[],products:[],presets:[],batches:[],logs:[],
  statuses:[],review_sessions:[],media_assets:[],executions:[],
  worker_config:{auth_diagnostics_available:true}};
let diagnostic = {status:'OK', diagnostic_state:'AUTH_STATE_PRESENT_BUT_PAGE_STUCK',
  context_count:1,page_count:1,auth_page_id:'page-1',captured_at:'fixture-time',cookies:[],
  pages:[{page_id:'page-1',is_auth_page:true,url:'https://www.tiktok.com/login/qrcode',
    readyState:'complete',visibilityState:'visible',login_state:'QR_SCANNED'}],
  errors:[],events:[{event:'PAGE_ERROR',message:'<script>fixture</script>'}]};
const calls = [];
const context = vm.createContext({
  document:{querySelector:element,querySelectorAll:()=>[],addEventListener(){}},
  setInterval(){}, queueMicrotask, FormData:class{},
  fetch:async (url,options) => {
    calls.push({url,options});
    assert.equal(options?.method, undefined, 'Diagnostics must only use GET');
    assert.ok(url.startsWith('/api/publisher'), 'No external request');
    return {ok:true,json:async()=>url.endsWith('/auth-diagnostics')?diagnostic:snapshot};
  }
});
(async () => {
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../dashboard/publisher.js'),'utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.match(element('#accountList').innerHTML,/data-auth-diagnostic="1"/);
  const before=calls.length;
  await vm.runInContext('showAuthDiagnostics(1)',context);
  assert.equal(calls.length,before+1);
  assert.equal(calls.at(-1).url,'/api/publisher/accounts/1/auth-diagnostics');
  assert.equal(element('#authDiagnosticDialog').open,true);
  let html=element('#authDiagnosticBody').innerHTML;
  assert.match(html,/AUTH_STATE_PRESENT_BUT_PAGE_STUCK/);
  assert.match(html,/QR_SCANNED/);
  assert.match(html,/complete/);
  assert.match(html,/&lt;script&gt;/);
  assert.ok(!html.includes('<script>'));
  diagnostic={...diagnostic,status:'NO_ACTIVE_CONTEXT',diagnostic_state:'UNKNOWN',context_count:0,page_count:0,auth_page_id:null,pages:[]};
  await vm.runInContext('showAuthDiagnostics(1)',context);
  assert.match(element('#authDiagnosticBody').innerHTML,/NO_ACTIVE_CONTEXT/);
  assert.ok(calls.every(call=>!call.url.endsWith('/login')));
  snapshot.worker_config.auth_diagnostics_available=false;
  await vm.runInContext('refresh()',context);
  assert.match(element('#accountList').innerHTML,/data-auth-diagnostic="1" disabled/);
  console.log('PASS: mocked UI renders snapshot/errors, escapes HTML, GET only, no login, old-server capability gate');
})().catch(error=>{console.error(error);process.exitCode=1;});
