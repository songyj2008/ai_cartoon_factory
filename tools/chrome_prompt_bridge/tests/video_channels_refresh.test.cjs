const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../../../ui/scripts.py'),'utf8');
test('channel refresh enables Doubao after audio removal and retains a valid choice',async()=>{
  function element(){return {addEventListener(){},children:[],style:{},isConnected:true,appendChild(x){this.children.push(x);},append(...xs){this.children.push(...xs);},remove(){this.isConnected=false;},querySelector(){return this.children.flatMap(x=>x.children||[]).find(x=>x.checked);}};}
  let field=element();
  field.children=[{children:[{checked:true,value:'comfyui'}]}];
  const pane={querySelector:()=>field,prepend:x=>{field=x;}};
  const modal={dataset:{segmentIndex:'3',h3Session:'one'},querySelector:()=>pane};
  let available=false;
  const context={window:{AICF:{}},document:{getElementById:()=>modal,createElement:element,createTextNode:text=>({text})},fetch:async()=>({ok:true,json:async()=>({ok:true,selected:'comfyui',channels:[{id:'comfyui',available:true},{id:'doubao',available}]})})};
  const start=source.indexOf('window.AICF.mountVideoChannels = async');
  const end=source.indexOf('window.AICF.chooseVideoChannel',start);
  vm.runInNewContext(source.slice(start,end),context);
  await context.window.AICF.mountVideoChannels(3);
  const radios=()=>field.children.flatMap(x=>x.children||[]).filter(x=>x.type==='radio');
  assert.equal(radios()[1].disabled,true);
  available=true;
  await context.window.AICF.mountVideoChannels(3);
  assert.equal(radios()[1].disabled,false);
  assert.equal(radios()[0].checked,true);
});

