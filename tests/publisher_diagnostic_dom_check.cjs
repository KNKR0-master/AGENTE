// Execute the diagnostic projection in a VM with fake browser APIs, never Chrome.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');
const path = require('node:path');
const script = execFileSync('python',['-c','from src.publisher.diagnostics import DOM_SNAPSHOT; print(DOM_SNAPSHOT)'],
  {cwd:path.join(__dirname,'..'),encoding:'utf8'});
function node(text,shown=true){return {innerText:text,textContent:text,getClientRects:()=>shown?[{}]:[],getAttribute:()=>null};}
async function project(text,opts={}){
  const body=node(text);
  body.querySelectorAll=()=>opts.logout?[node('Sair')]:[];
  const context={
    document:{body,title:'Log in | TikTok',readyState:'complete',visibilityState:'visible',querySelectorAll:()=>[node('QR code expired',false)]},
    location:{href:'https://www.tiktok.com/login/qrcode'},
    localStorage:{language:'SECRET_VALUE'},sessionStorage:{login_state:'SECRET_VALUE'},
    indexedDB:{databases:async()=>[{name:'store',version:1}]},
    navigator:{serviceWorker:{getRegistrations:async()=>[]}},
    getComputedStyle:()=>({visibility:'visible'}),setTimeout,clearTimeout
  };
  if(opts.failure)context.localStorage=new Proxy({},{ownKeys(){const e=new Error('token=SECRET_VALUE');e.name='SecurityError';throw e;}});
  const result=await vm.runInNewContext(`(${script})()`,context);
  assert.ok(!JSON.stringify(result).includes('SECRET_VALUE'));
  return result;
}
(async()=>{
  for(const [text,state] of [
    ['Scan the QR code','QR_WAITING_SCAN'],['QR code scanned','QR_SCANNED'],
    ['Waiting for confirmation','CONFIRMATION_PENDING'],['QR code expired','QR_EXPIRED'],
    ['Verify you are human','CHALLENGE'],['Something went wrong','ERROR'],
    ['Você está conectado','CONFIRMATION_COMPLETE']
  ]){
    const result=await project(text);
    assert.ok(result.signals.visible.includes(state),state);
    assert.ok(result.signals.hidden.includes('QR_EXPIRED'));
    assert.deepEqual(Array.from(result.localStorageKeys),['language']);
  }
  const result=await project('TikTok Studio',{logout:true,failure:true});
  assert.equal(result.signals.logout,true);
  assert.equal(result.localStorageKeys,null);
  assert.equal(result.errors[0].error_type,'SecurityError');
  console.log('PASS: actual DOM projection in mocked VM; 7 semantic states, hidden status, storage failures, values excluded');
})().catch(error=>{console.error(error);process.exitCode=1;});
