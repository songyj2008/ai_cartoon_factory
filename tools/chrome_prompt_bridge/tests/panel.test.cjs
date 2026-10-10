const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

test('missing or invalid cached task IDs do not break panel rendering', () => {
  const elements = new Map();
  const context = {
    crypto: require('node:crypto').webcrypto,
    PromptBridge: require('../protocol.js'),
    document: {getElementById(id) {
      if (!elements.has(id)) elements.set(id, {value:'',classList:{toggle(){}},replaceChildren(){},append(){}});
      return elements.get(id);
    }},
  };
  const source = fs.readFileSync(path.join(__dirname,'../panel.js'),'utf8');
  vm.createContext(context);
  vm.runInContext(source.slice(0, source.indexOf('function loadSelectors()')),context);
  for (const invalid of [{status:'waiting'}, {id:42}, {id:''}, {id:'   '}]) {
    context.next = invalid;
    vm.runInContext('renderTask(next)',context);
    assert.match(elements.get('status').textContent,/缺少编号/);
    assert.equal(elements.get('read').disabled,true);
  }
  context.next = {id:'valid-task',provider:'DeepSeek',created_at:new Date().toISOString(),status:'waiting',message:'等待回复'};
  vm.runInContext('renderTask(next)',context);
  assert.match(elements.get('task-info').textContent,/valid-ta/);
  assert.equal(elements.get('status').textContent,'等待回复');
});

test('task clear action binds when Gradio inserts the panel after the page script', async () => {
  const source = fs.readFileSync(path.join(__dirname,'../../../ui/text_jobs_ui.py'),'utf8');
  const script = source.match(/PANEL_JS = r'''([\s\S]*?)'''/)[1];
  let panelReady = false, interval, clearCalls = 0;
  const listeners = new Map();
  const node = () => ({textContent:'',value:0,disabled:false,hidden:false,open:false,dataset:{},classList:{toggle(){}},
    addEventListener(type,fn){listeners.set(type,fn);},replaceChildren(){},append(){}});
  const nodes = new Map();
  const getNode = key => {if(!nodes.has(key))nodes.set(key,node());return nodes.get(key);};
  const resultBox = node();
  const panel = {classList:{toggle(){}},querySelector(selector){
    if(selector.startsWith('[data-count='))return {textContent:'',};
    if(selector==='.text-operations-result')return resultBox;
    return getNode(selector);
  }};
  const context = {
    window:{confirm:()=>true},document:{hidden:false,getElementById:id=>panelReady&&id==='text-operations'?panel:null,
      querySelector:()=>null},
    fetch:async(_url,options={})=>{
      if(options.method==='POST'){clearCalls++;return {ok:true,json:async()=>({ok:true,deleted:1})};}
      return {ok:true,json:async()=>({ok:true,tasks:clearCalls?[]:[{id:'failed',state:'error',title:'任务',project:'项目',created:1,updated:1}]})};
    },
    setInterval:fn=>{interval=fn;},setTimeout,clearTimeout,Event:class{},HTMLTextAreaElement:class{},HTMLInputElement:class{}
  };
  vm.createContext(context);vm.runInContext(script,context);
  panelReady=true;interval();
  for(let i=0;i<8;i++)await new Promise(resolve=>setTimeout(resolve,0));
  const button=getNode('.text-operations-clear');
  assert.equal(typeof listeners.get('click'),'function');
  await listeners.get('click')();
  assert.equal(clearCalls,1);
});
