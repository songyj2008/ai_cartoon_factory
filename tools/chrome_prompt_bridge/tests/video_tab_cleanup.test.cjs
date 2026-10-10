const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../video_worker.js'),'utf8');
test('only owned completed video tabs close after preview fetch succeeds',async()=>{
  for(const [owned,ready,accessible,expected] of [[true,true,true,1],[false,true,true,0],[true,false,true,0],[true,true,false,0]]) {
    const removed=[];
    const context={videoApi:async()=>({task:{preview_ready:ready,preview_url:'/gradio_api/file=video.mp4'}}),
      videoTaskSave:async()=>{},baseUrl:()=> 'http://127.0.0.1:7860',URL,AbortSignal,
      fetch:async()=>({ok:accessible,headers:{get:()=> 'video/mp4'},arrayBuffer:async()=>new Uint8Array([1]).buffer}),
      chrome:{tabs:{get:async()=>({url:'https://www.doubao.com/chat/one'}),remove:async id=>removed.push(id)}},
      videoAdapter:async()=>({hostname:'doubao.com'})};
    const start=source.indexOf('async function videoReport('),end=source.indexOf('async function videoDownload(',start);
    vm.runInNewContext(source.slice(start,end),context);
    await context.videoReport({}, {id:'task',channel:'doubao_web',ownedTab:owned,tabId:7},'complete');
    assert.equal(removed.length,expected);
  }
});
