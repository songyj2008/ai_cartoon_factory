const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../video_worker.js'),'utf8');

function worker({ready=false,confirmationRequired=false,downloadState='complete',failed=false,error=''}={}) {
  const calls=[];
  const context={Date,
    videoTaskSave:async()=>{},
    videoPage:async(task,type)=>{calls.push(type);return {ready,confirmationRequired,failed,error};},
    videoDownload:async()=>calls.push('download'),
    videoReport:async(config,task,state,extra)=>{calls.push(state);context.reportExtra=extra;},
    chrome:{downloads:{search:async()=>[{state:downloadState,filename:'video.mp4'}]}}};
  const start=source.indexOf('async function videoResume('),end=source.indexOf('async function videoPumpFromStorage(',start);
  vm.runInNewContext(source.slice(start,end),context);
  return {calls,resume:task=>context.videoResume({},task),get reportExtra(){return context.reportExtra;}};
}
const expired=()=>({id:'task',state:'submitted',submittedAt:Date.now()-46*60*1000});

test('checks completed result before declaring generation timeout',async()=>{
  const run=worker({ready:true});
  await run.resume(expired());
  assert.deepEqual(run.calls,['AICF_VIDEO_STATUS','download']);
});

test('reports an already downloaded result even after generation deadline',async()=>{
  const run=worker();
  await run.resume({...expired(),downloadId:7});
  assert.deepEqual(run.calls,['complete']);
});

test('still stops expired generation without confirming or submitting again',async()=>{
  for(const confirmationRequired of [false,true]) {
    const run=worker({confirmationRequired});
    await assert.rejects(run.resume(expired()),/45 分钟/);
    assert.deepEqual(run.calls,['AICF_VIDEO_STATUS']);
  }
});

test('settles explicit generation failure with its original reason and never downloads or retries',async()=>{
  const error='视频生成失败：近 7 天免费额度已用完，预计 11 月 10 日 07:51 恢复。';
  const run=worker({failed:true,error,confirmationRequired:true});
  await run.resume(expired());
  assert.deepEqual(run.calls,['AICF_VIDEO_STATUS','uncertain']);
  assert.equal(run.reportExtra.error,error);
});
