const assert=require('node:assert/strict');
const {test}=require('node:test');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

function page({ready=false,confirmation=true}={}){
  const listeners=[];
  const element=text=>({isConnected:true,children:[],textContent:text,innerText:text,
    getBoundingClientRect:()=>({width:100,height:30}),getAttribute:()=>'',querySelectorAll:()=>[]});
  const confirmationElement=element('确认后我再开始生成视频');
  const completed=element('你的视频生成好了。');
  const context={
    document:{querySelectorAll:selector=>{
      if(selector==='div,span,p')return [...(confirmation?[confirmationElement]:[]),...(ready?[completed]:[])];
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
