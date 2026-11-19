const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const page = fs.readFileSync(path.join(__dirname, '../video/runow.js'), 'utf8');

function extract(start, end, context) {
  vm.runInNewContext(page.slice(page.indexOf(start), page.indexOf(end, page.indexOf(start))), context);
}

test('operation delays cover the inclusive 20–150 ms range', async () => {
  const delays = [];
  let random = 0;
  const context = {Math:{floor:Math.floor,random:()=>random},
    setTimeout:(resolve,delay)=>{delays.push(delay);resolve();}};
  const start = page.indexOf('  const sleep=');
  const end = page.indexOf('  let preparationTask=', start);
  vm.runInNewContext(page.slice(start,end)+'\nthis.pause=actionPause;',context);
  for (random of [0,0.5,0.999999]) await context.pause();
  assert.deepEqual(delays,[20,85,150]);
});

test('visible quotations distinguish free, points, pending and failed uploads', () => {
  let label = '免费';
  let pending = false;
  let aria = '';
  const fee = {getAttribute:()=>null,classList:{contains:()=>pending},get innerText(){return label;}};
  const context = {visible:()=>true,text:node=>node.innerText,
    composer:()=>({querySelectorAll:()=>[fee],querySelector:()=>({getAttribute:()=>aria})})};
  extract('  function quoteState()', '  async function waitForQuote(', context);
  assert.equal(context.quoteState().ready,true);
  label='⚡ 100';
  assert.equal(context.quoteState().ready,true);
  pending=true;
  assert.equal(context.quoteState().ready,false);
  pending=false;label='计算中';
  assert.equal(context.quoteState().ready,false);
  aria='当前报价未就绪，请稍后再试 · 参考图或视频上传失败';
  assert.equal(context.quoteState().uploadFailed,true);
});

function waitFixture() {
  let now = 1000;
  let state = {ready:true};
  const readyReceipts = new Set();
  let queries = 0;
  const context = {Date:{now:()=>now},heartbeat:async()=>{},
    sleep:async delay=>{now+=delay;},quoteState:()=>state,
    submissionPatch:async(_task,command)=>{queries++;return {ready:readyReceipts.has(command)};}};
  extract('  async function waitFor(', '  async function click(', context);
  extract('  async function waitForQuote(', '  async function submit(', context);
  return {context,setState:value=>{state=value;},setReceipt:command=>readyReceipts.add(command),
    now:()=>now,queries:()=>queries};
}

test('initial quote settles after model selection without requiring an enabled send button', async () => {
  const fixture = waitFixture();
  await fixture.context.waitForQuote({},'模型初始报价');
  assert.ok(fixture.now()>=1650);
  assert.equal(fixture.queries(),0);
});

test('each stage requires its own valid quote and a visible price', async () => {
  const fixture = waitFixture();
  await assert.rejects(fixture.context.waitForQuote({},'全部参考素材报价','media-quote'),/全部参考素材报价/);
  assert.ok(fixture.queries()>0);
  fixture.setReceipt('media-quote');
  await fixture.context.waitForQuote({},'全部参考素材报价','media-quote');
  await assert.rejects(fixture.context.waitForQuote({},'完整文案报价','quote'),/完整文案报价/);
  fixture.setReceipt('quote');
  fixture.setState({ready:false});
  await assert.rejects(fixture.context.waitForQuote({},'完整文案报价','quote'),/完整文案报价/);
  fixture.setState({ready:true});
  await fixture.context.waitForQuote({},'完整文案报价','quote');
});

test('an explicit upload failure identifies the current media and stops immediately', async () => {
  const fixture = waitFixture();
  fixture.setState({ready:false,uploadFailed:true,error:'参考图或视频上传失败'});
  await assert.rejects(fixture.context.waitForQuote({media:[{name:'actor.png'}]},'素材报价'),/上传失败.*actor\.png/);
  assert.equal(fixture.queries(),0);
});

for(const failedPhase of ['模型初始报价','填写提示词前的全部参考素材报价','全部参考素材的报价']) {
  test('normal submit entry stops before dispatch when '+failedPhase+' fails',async()=>{
    let configured=false;
    let sent=false;
    let selections=0;
    let promptFilled=false;
    const context={preparationTask:null,lastHeartbeat:0,
      C:require('../video/runow_contract.js'),
      location:{origin:'https://www.runninghub.cn',pathname:'/runow/123'},
      openProject:async()=>{},setMode:async()=>{configured=true;},
      waitForQuote:async(_task,label)=>{if(label.includes(failedPhase))throw new Error('报价失败');},
      selectMedia:async()=>{selections++;},fillPrompt:async()=>{promptFilled=true;},
      submissionPatch:async()=>({ok:true}),
      outputs:()=>[],pending:()=>0,sendButton:()=>({}),waitFor:async read=>read(),
      click:async()=>{sent=true;}};
    extract('  async function submit(', '  async function status(', context);
    await assert.rejects(context.submit({}),error=>error.beforeSend===true && /报价失败/.test(error.message));
    assert.equal(sent,false);
    assert.equal(configured,failedPhase!=='模型初始报价');
    assert.equal(selections,configured?1:0);
    assert.equal(promptFilled,failedPhase==='全部参考素材的报价');
  });
}
