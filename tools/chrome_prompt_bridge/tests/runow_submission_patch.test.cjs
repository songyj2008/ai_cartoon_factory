const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../runow_submission_patch.js'), 'utf8');

function fixture(fetchResponse, libraryModule, {origin = 'https://www.runninghub.cn',
  script = '/runow/assets/index-BbuZvlU0.js', kinds = ['image', 'image', 'image']} = {}) {
  class Request {
    open(method, url) {this.url = url;}
    send(body) {this.sent = body;}
    addEventListener(event, callback) {this.loaded = callback;}
    receive(data) {this.status=200;this.responseText = JSON.stringify(data);this.loaded();}
  }
  const originalFetch = async url => ({ok:true, url, headers: {get: () => ''}, blob:async()=>({size:42}),
    clone: () => ({json: async () => fetchResponse})});
  class File {
    constructor(parts,name,{type}) {this.name=name;this.type=type;this.size=parts[0].size;}
  }
  const window = {fetch: originalFetch, File};
  const context = {window, XMLHttpRequest: Request, URL,
    location: {origin, pathname: '/runow/123', href: origin + '/runow/123'},
    setTimeout: () => 1, clearTimeout: () => {}};
  if(libraryModule) {
    context.document={querySelectorAll:()=>[{src:script}]};
    context.loadModule=async url=>{
      assert.equal(url, new URL(script, origin).href);
      return libraryModule;
    };
  }
  vm.runInNewContext(source.replace('import(entry.href)','loadModule(entry.href)'), context);
  const task = {id: 'one', origin, promptParts: [{text: '人物 '}, {reference_id: 'actor', label: '@图片1'}],
    media: kinds.map((type, i) => ({id: 'media' + i, name: (i + 1) + '.png', type}))};
  const call = command => context.runowSubmissionPatch(command, task);
  const started=call('start');
  const assets = new Request();
  assets.open('GET', '/assets/list');
  const deliver = (count, format = 'material') => assets.receive({data: {records: task.media.slice(0, count).map((item, index) => {
    const url = 'https://files.example/' + (index + 1) + '.png';
    if (format === 'asset') return {assetName: item.name, assetType: item.type, assetUrl: url};
    return {name: item.name, type: item.type, materialUrl: url};
  })}});
  const request = {projectId: '123', categoryId: 'VIDEO', bindingId: 'h3', params: {
    prompt: '人物 @Image 1', duration: 14, refImage1: 'https://files.example/1.png'}};
  return {call, task, deliver, request, Request, window, originalFetch, File, started};
}

test('all three resource URLs enter both prepare and commit in reference order', () => {
  const {call, deliver, request, Request} = fixture();
  deliver(3);
  assert.equal(call('arm').mediaCount, 3);
  for (const stage of ['prepare', 'commit']) {
    const xhr = new Request();
    xhr.open('POST', 'https://www.runninghub.cn/api/task/quick-creation/' + stage);
    const payload = stage === 'commit' ? {prepareToken: 'kept', createRequest: request} : request;
    xhr.send(JSON.stringify(payload));
    const result = JSON.parse(xhr.sent);
    const mapped = result.createRequest || result;
    assert.deepEqual([mapped.params.refImage1, mapped.params.refImage2, mapped.params.refImage3],
      [1, 2, 3].map(index => 'https://files.example/' + index + '.png'));
    assert.equal(mapped.params.duration, 14);
    assert.equal(mapped.bindingId, 'h3');
    if (stage === 'commit') {
      assert.equal(result.prepareToken, 'kept');
      assert.equal(call('receipt').ready, false);
      xhr.receive({code:0,data:{taskId:'2120463631749308418'}});
    }
  }
  assert.deepEqual(Array.from(call('receipt').fields), ['refImage1', 'refImage2', 'refImage3']);
});

for (const [origin, script, fingerprintExport, cacheExport] of [
  ['https://www.runninghub.cn', '/runow/assets/index-BbuZvlU0.js', 'ep', 'ek'],
  ['https://www.runninghub.cn', '/runow/assets/index-BOSdfXE2.js', 'en', 'ei'],
  ['https://www.runninghub.ai', '/runow/assets/index-D1g3pehG.js', 'ep', 'ek'],
  ['https://www.runninghub.cn', '/runow/assets/index-BQbIze-b.js', 'ep', 'ek'],
  ['https://www.runninghub.ai', '/runow/assets/index-BQbIze-b.js', 'ep', 'ek']
]) test(`the matching library download reuses its cloud reference on ${origin}${script}`, async () => {
  const restored=[];
  const {call,deliver,window,File,started}=fixture(undefined,{
    [fingerprintExport]:file=>`${file.name}:${file.size}`, [cacheExport]:entries=>restored.push(...entries)
  }, {origin, script});
  await started;
  deliver(3);
  const asset=await window.fetch('https://files.example/1.png');
  const unrelated=await window.fetch('https://files.example/unrelated.png');
  const libraryBlob=await asset.blob();
  new window.File([libraryBlob],'2.png',{type:'image/png'});
  new window.File([libraryBlob],'1.png',{type:'video/mp4'});
  new window.File([await unrelated.blob()],'1.png',{type:'image/png'});
  assert.equal(restored.length,0);
  new window.File([libraryBlob],'1.png',{type:'image/png'});
  assert.equal(restored.length,1);
  assert.equal(restored[0].fingerprint,'1.png:42');
  assert.equal(restored[0].url,'https://files.example/1.png');
  call('arm');
  new window.File([libraryBlob],'1.png',{type:'image/png'});
  assert.equal(restored.length,1);
  call('stop');
  assert.equal(window.File,File);
});

test('missing resource URLs prevent arming the submit operation', () => {
  const {call, deliver} = fixture();
  deliver(1);
  assert.throws(() => call('arm'), /2.png、3.png/);
  assert.equal(call('receipt').ready, false);
});

test('main-site asset records supply the original URLs', () => {
  const {call, deliver, request, Request} = fixture();
  deliver(3, 'asset');
  call('arm');
  const xhr = new Request();
  xhr.open('POST', '/api/task/quick-creation/prepare');
  xhr.send(JSON.stringify(request));
  assert.equal(JSON.parse(xhr.sent).params.refImage3, 'https://files.example/3.png');
});

test('another project and unrelated requests keep their original body', () => {
  const {call, deliver, request, Request} = fixture();
  deliver(3);
  call('arm');
  for (const [url, payload] of [
    ['/api/task/quick-creation/prepare', {...request, projectId: '456'}],
    ['/api/projects/save', request],
    ['https://another.example/api/task/quick-creation/prepare', request]
  ]) {
    const xhr = new Request();xhr.open('POST', url);
    const original = JSON.stringify(payload);xhr.send(original);
    assert.equal(xhr.sent, original);
  }
});

test('stopping restores the page network methods and does not confirm an unsent request', () => {
  const {call, window, originalFetch} = fixture();
  assert.notEqual(window.fetch, originalFetch);
  call('stop');
  assert.equal(window.fetch, originalFetch);
  assert.throws(() => call('arm'), /已失效/);
});

for (const data of [{}, {taskId:2120463631749308418}, {taskId:'one',uncertain:true}]) {
  test('unreliable commit response cannot supply a receipt '+JSON.stringify(data), () => {
    const {call, deliver, request, Request} = fixture();
    deliver(3); call('arm');
    const xhr=new Request(); xhr.open('POST','/api/task/quick-creation/commit');
    xhr.send(JSON.stringify({createRequest:request}));
    xhr.receive({code:0,data});
    assert.throws(() => call('receipt'), /任务|ID/);
  });
}
test('another project commit response cannot bind this task', () => {
  const {call, deliver, request, Request} = fixture();
  deliver(3); call('arm');
  const xhr=new Request(); xhr.open('POST','/api/task/quick-creation/commit');
  xhr.send(JSON.stringify({createRequest:{...request,projectId:'other'}}));
  xhr.receive({code:0,data:{taskId:'foreign'}});
  assert.equal(call('receipt').ready,false);
});

test('fetch captures the acknowledged task ID from the matching commit response', async () => {
  const {call,deliver,request,window}=fixture({code:0,data:{taskId:'2120463631749308418'}});
  deliver(3);call('arm');
  await window.fetch('/api/task/quick-creation/prepare',{body:JSON.stringify(request)});
  assert.equal(call('receipt').ready,false);
  await window.fetch('/api/task/quick-creation/commit',{body:JSON.stringify({createRequest:request})});
  assert.equal(call('receipt').taskId,'2120463631749308418');
  assert.equal(call('receipt').projectId,'123');
  assert.equal(call('receipt').source,'runninghub_commit');
});

test('projectless fee preview receives the same ordered references as prepare and commit', () => {
  const {call,deliver,request,Request}=fixture();
  deliver(3);call('arm');
  const {projectId,...quoteRequest}=request;
  const quote=new Request();quote.open('POST','/api/task/quick-creation/fee-preview');
  quote.send(JSON.stringify(quoteRequest));
  assert.equal(JSON.parse(quote.sent).params.refImage3,'https://files.example/3.png');
  assert.equal(call('quote').ready,false);
  quote.receive({code:0,data:{passed:true,free:true}});
  assert.equal(call('quote').ready,true);
  assert.deepEqual(Array.from(call('quote').fields),['refImage1','refImage2','refImage3']);
  assert.equal(call('receipt').ready,false);
});

for (const origin of ['https://www.runninghub.cn', 'https://www.runninghub.ai']) {
  test('waits for a media-only quote with every selected kind on ' + origin, () => {
    const {call, deliver, request, Request} = fixture(undefined, undefined,
      {origin, kinds:['image', 'image', 'video', 'audio']});
    const sendQuote = params => {
      const xhr = new Request();
      xhr.open('POST', '/api/task/quick-creation/fee-preview');
      xhr.send(JSON.stringify({categoryId:request.categoryId, params}));
      return xhr;
    };
    deliver(4);
    const initial = sendQuote({prompt:'', duration:14});
    const partial = sendQuote({prompt:'', duration:14, refImage1:'https://files.example/1.png'});
    const media = sendQuote({prompt:'', duration:14, refImage1:'https://files.example/1.png',
      refVideo1:'https://files.example/3.png', refAudio1:'https://files.example/4.png'});
    assert.equal(JSON.parse(partial.sent).params.refImage2, undefined);
    assert.deepEqual(JSON.parse(media.sent).params, {prompt:'', duration:14, refImage1:'https://files.example/1.png',
      refImage2:'https://files.example/2.png', refVideo1:'https://files.example/3.png', refAudio1:'https://files.example/4.png'});
    const valid = {code:0, data:{passed:true, free:true}};
    initial.receive(valid);
    partial.receive(valid);
    call('arm');
    assert.equal(call('media-quote').ready, false);
    media.receive(valid);
    assert.equal(call('media-quote').ready, true);
    assert.equal(call('quote').ready, false);
    const final = sendQuote(request.params);
    final.receive(valid);
    assert.equal(call('quote').ready, true);
    media.receive({code:500});
    assert.equal(call('quote').ready, true);
  });
}

test('late old quote replies cannot ready a newer pending or failed quote', () => {
  const {call,deliver,request,Request}=fixture();
  deliver(3);call('arm');
  const quotes=[1,2].map(()=>{
    const xhr=new Request();xhr.open('POST','/api/task/quick-creation/fee-preview');
    xhr.send(JSON.stringify(request));return xhr;
  });
  quotes[0].receive({code:0,data:{passed:true,free:true}});
  assert.equal(call('quote').ready,false);
  quotes[1].receive({code:500,msg:'quote service unavailable'});
  assert.throws(()=>call('quote'),/quote service unavailable/);
  quotes[0].receive({code:0,data:{passed:true,free:true}});
  assert.throws(()=>call('quote'),/quote service unavailable/);
});

for (const data of [{}, {passed:false,errorMessage:'素材地址无效'}, {free:true,freeQuotaRemaining:0}]) {
  test('invalid quotation stops with a specific diagnostic '+JSON.stringify(data),()=>{
    const {call,deliver,request,Request}=fixture();deliver(3);call('arm');
    const xhr=new Request();xhr.open('POST','/api/task/quick-creation/fee-preview');
    xhr.send(JSON.stringify(request));xhr.receive({code:0,data});
    assert.throws(()=>call('quote'),/报价失败/);
  });
}

test('unrelated prompts and foreign projects never supply this task quotation',()=>{
  const {call,deliver,request,Request}=fixture();deliver(3);call('arm');
  for(const payload of [{...request,projectId:'other'},
      {...request,params:{...request.params,prompt:'someone else'}}]) {
    const xhr=new Request();xhr.open('POST','/api/task/quick-creation/fee-preview');
    const body=JSON.stringify(payload);xhr.send(body);
    assert.equal(xhr.sent,body);
    xhr.receive({code:0,data:{free:true}});
    assert.equal(call('quote').ready,false);
  }
});

for(const price of [{requiredPointUnit:12000},{pointAmount:'0'},{rhAmount:'12.5'}]) {
  test('native price fields count as a valid quotation '+JSON.stringify(price),()=>{
    const {call,deliver,request,Request}=fixture();deliver(3);call('arm');
    const xhr=new Request();xhr.open('POST','/api/task/quick-creation/fee-preview');
    xhr.send(JSON.stringify(request));xhr.receive({code:0,data:{passed:true,...price}});
    assert.equal(call('quote').ready,true);
  });
}
