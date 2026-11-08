const test=require('node:test');
const assert=require('node:assert/strict');
const {upload}=require('../text_attachments.js');

function fixture(accept='video/*',reject=false) {
  let clock=1, changes=0;
  const body={innerText:''};
  const input={accept,dispatchEvent(event){if(event.type==='change'){changes++;body.innerText='approved_123.mp4';}}};
  const document={body,querySelectorAll(selector){
    if(selector==='input[type="file"]')return [input];
    if(selector==='[role="alert"]' && reject)return [{innerText:'视频格式不支持'}];
    return [];
  }};
  const env={document,atob:s=>Buffer.from(s,'base64').toString('binary'),
    setTimeout(fn,ms){clock+=ms;fn();},
    Event:class{constructor(type){this.type=type;}},
    File:class{constructor(parts,name,opts){this.name=name;this.type=opts.type;}},
    DataTransfer:class{constructor(){this.files=[];this.items={add:f=>this.files.push(f)};}},
    chrome:{runtime:{sendMessage:async message=>{
      assert.equal(message.type,'AICF_TASK_ATTACHMENT');assert.equal(message.attachmentId,'approved_video');
      return {ok:true,base64:Buffer.from('movie').toString('base64'),mime:'video/mp4',sha256:'approved-hash'};
    }}}
  };
  return {env,input,clock:()=>clock,changes:()=>changes};
}
const task={id:'task',binding_epoch:3,attachments:[{id:'approved_video',name:'approved_123.mp4'}]};

test('uploads the approved video and returns evidence without modifying prompt text',async()=>{
  const f=fixture(), originalNow=Date.now;Date.now=f.clock;
  try {
    const receipt=await upload(task,f.env);
    assert.equal(f.changes(),1);assert.equal(f.input.files[0].name,'approved_123.mp4');
    assert.deepEqual(receipt,{kind:'media_attachment',id:'approved_video',name:'approved_123.mp4',sha256:'approved-hash'});
  } finally {Date.now=originalNow;}
});
test('unsupported file inputs fail instead of sending a text-only learning request',async()=>{
  const f=fixture('.pdf,.txt');
  await assert.rejects(upload(task,f.env),/MEDIA_ATTACHMENT_UNSUPPORTED/);
  assert.equal(f.changes(),0);
});
test('page rejection is surfaced and cannot be reported as observed video',async()=>{
  const f=fixture('video/*',true);
  await assert.rejects(upload(task,f.env),/网页拒绝视频附件/);
});
test('ordinary text tasks never access attachments',async()=>{
  assert.equal(await upload({id:'text'},{}),null);
});
