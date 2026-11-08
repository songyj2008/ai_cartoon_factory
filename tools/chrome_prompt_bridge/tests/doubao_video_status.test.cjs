const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function page({ready=false,confirmation=true,completedText='你的视频生成好了。',nested=false,videoUrl='',failure=''}={}){
  const listeners=[];
  const element=text=>({isConnected:true,children:[],textContent:text,innerText:text,
    getBoundingClientRect:()=>({width:100,height:30}),getAttribute:()=>'',querySelectorAll:()=>[]});
  const confirmationElement=element('确认后我再开始生成视频');
  const completed=element(completedText);
  const failed=element(failure);
  if(nested)completed.children=[element('10 秒')];
  const context={
    document:{querySelectorAll:selector=>{
      if(selector==='div,span,p')return [...(confirmation?[confirmationElement]:[]),...(ready?[completed]:[]),...(failure?[failed]:[])];
      if(selector==='video' && videoUrl)return [{currentSrc:videoUrl}];
      if(selector.includes('data-message-author-role'))return [];
      return [];
    }},
    getComputedStyle:()=>({display:'block',visibility:'visible'}),
    chrome:{runtime:{onMessage:{addListener:listener=>listeners.push(listener)}}},
    innerHeight:1000,innerWidth:1000,
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../video/doubao.js'),'utf8'),context);
  return task=>new Promise(resolve=>listeners[0]({type:'AICF_VIDEO_STATUS',task},null,resolve));
}

test('completed video card wins over historical confirmation',async()=>{
  const status=await page({ready:true})({baseline:[],baselineReadyCount:0,confirmationAttempted:true});
  assert.equal(status.ready,true);
  assert.equal(status.cardReady,true);
});

test('confirmed request does not ask again while waiting for video',async()=>{
  const status=await page()({baseline:[],baselineReadyCount:0,confirmationAttempted:true});
  assert.equal(status.ready,false);
  assert.equal(status.confirmationRequired,undefined);
});

test('plain Doubao reply requests confirmation for the current submission',async()=>{
  const status=await page()({baseline:[],baselineReadyCount:0,baselineConfirmationCount:0});
  assert.equal(status.confirmationRequired,true);
});

test('confirmation present before submission is ignored',async()=>{
  const status=await page()({baseline:[],baselineReadyCount:0,baselineConfirmationCount:1});
  assert.equal(status.confirmationRequired,undefined);
});

for(const completedText of ['视频已生成完毕，10 秒 16:9 古装战争对峙短片。','视频已经生成完成。','视频生成成功。']) {
  test('recognizes completion wording: '+completedText,async()=>{
    const status=await page({ready:true,completedText,nested:true})({baseline:[],baselineReadyCount:0});
    assert.equal(status.ready,true);
    assert.equal(status.cardReady,true);
  });
}

test('recognizes extensionless video CDN sources without confusing normal links',async()=>{
  const videoUrl='https://cdn.example.test/media/opaque-result?token=test';
  const status=await page({videoUrl,confirmation:false})({baseline:[]});
  assert.equal(status.ready,true);
  assert.equal(status.url,videoUrl);
  const previous=await page({videoUrl,confirmation:false})({baseline:[videoUrl]});
  assert.equal(previous.ready,false);
});

test('ignores completed cards present before submission',async()=>{
  const status=await page({ready:true,completedText:'视频已生成完毕。'})({baseline:[],baselineReadyCount:1,confirmationAttempted:true});
  assert.equal(status.ready,false);
});

test('reports the quota refusal and its recovery time instead of waiting or confirming',async()=>{
  const failure='视频生成失败：近 7 天免费额度已用完，预计 11 月 10 日 07:51 恢复。';
  const status=await page({failure})({baseline:[],baselineFailureCount:0});
  assert.equal(status.failed,true);
  assert.equal(status.ready,false);
  assert.equal(status.error,failure);
  assert.equal(status.confirmationRequired,undefined);
});

test('ignores failure notices from before the current submission',async()=>{
  const status=await page({failure:'视频生成失败：额度已用完。',confirmation:false})({baseline:[],baselineFailureCount:1});
  assert.equal(status.failed,undefined);
  assert.equal(status.ready,false);
});
