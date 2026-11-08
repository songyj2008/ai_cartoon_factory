const test=require('node:test');
const assert=require('node:assert/strict');
const {create,readEditor,isSlate}=require('../qianwen.js');
const P=require('../protocol.js');

// Minimal DOM tree for the Slate nodes captured in the supplied homepage HTML.
const text=value=>({nodeType:3,nodeValue:value,get textContent(){return this.nodeValue;}});
function element(tag,attrs={},children=[]) {
 return {nodeType:1,tagName:tag.toUpperCase(),childNodes:children,
   getAttribute:name=>Object.hasOwn(attrs,name)?attrs[name]:null,
   hasAttribute:name=>Object.hasOwn(attrs,name),
   get textContent(){return this.childNodes.map(node=>node.textContent || '').join('');},
   querySelectorAll(){
     const leaves=[];
     const visit=node=>{if(node.nodeType!==1)return;
       if(node.hasAttribute('data-slate-string') || node.hasAttribute('data-slate-zero-width'))leaves.push(node);
       node.childNodes.forEach(visit);};
     this.childNodes.forEach(visit);return leaves;
   }};
}
const slate=(...blocks)=>element('div',{'data-slate-editor':'true','data-slate-node':'value'},blocks);
const paragraph=(...children)=>element('p',{'data-slate-node':'element'},children);
const string=value=>element('span',{'data-slate-string':'true'},[text(value)]);

test('empty Slate placeholder and its sentinel are not an existing user draft',()=>{
 const zero=element('span',{'data-slate-zero-width':'n','data-slate-length':'0'},[text('\uFEFF'),element('br')]);
 const placeholder=element('span',{'data-slate-placeholder':'true',contenteditable:'false'},[text('向千问提问')]);
 const editor=slate(paragraph(element('span',{'data-slate-node':'text'},[zero,placeholder])));
 editor.innerText='\uFEFF\n向千问提问';
 assert.equal(readEditor(editor),'');
 const adapter=create({all:()=>[],inputText:readEditor,evidenceText:P.evidenceText});
 assert.equal(adapter.isEmptyHome('https://www.qianwen.com/',editor),true);
});

test('Slate reading preserves real text, blank paragraphs, inline text and JSON escapes',()=>{
 const inline=element('span',{'data-slate-node':'element','data-slate-inline':'true'},[string('inline')]);
 const editor=slate(paragraph(string('first '),inline,string(' line')),
   paragraph(element('span',{'data-slate-zero-width':'n'},[text('\uFEFF'),element('br')])),
   paragraph(string('{"text":"a\\nb"}')));
 assert.equal(readEditor(editor),'first inline line\n\n{"text":"a\\nb"}');
 assert.equal(readEditor(slate(paragraph(string('向千问提问\u200B\uFEFF')))),'向千问提问\u200B\uFEFF');
});

test('Slate sentinel handling keeps real content temporarily present in a zero-width leaf',()=>{
 const editor=slate(paragraph(element('span',{'data-slate-zero-width':'z'},[text('\uFEFFdraft')])));
 assert.equal(readEditor(editor),'draft');
});

test('non-Slate custom editors retain their existing value and selection behavior',()=>{
 assert.equal(readEditor({value:'textarea draft'}),'textarea draft');
 assert.equal(readEditor({innerText:'rich editor'}),'rich editor');
 assert.equal(isSlate({value:'draft'}),false);
 assert.equal(isSlate(slate(paragraph())),true);
});

test('Slate writing delegates to the worker and never falls back to execCommand',async()=>{
 const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
 const source=fs.readFileSync(path.join(__dirname,'../content.js'),'utf8');
 const start=source.indexOf('  async function writeRequestEditor(');
 const end=source.indexOf('  async function repairJson(',start);
 const messages=[];let focused=0;
 const context={site:'千问',pagePolicy:require('../text_provider_policy.js').policy('qianwen'),QianwenPage:{isSlate:()=>true},
   writeEditor:()=>{throw new Error('DOM write forbidden');},
   chrome:{runtime:{sendMessage:async message=>{messages.push(message);return {ok:messages.length===1,error:'debugger busy'};}}}};
 vm.runInNewContext(source.slice(start,end),context);
 const el={focus:()=>focused++},task={id:'slate-job',binding_epoch:3};
 await context.writeRequestEditor(el,'task text',task);
 assert.equal(messages[0].type,'AICF_QIANWEN_INPUT');
 assert.equal(messages[0].taskId,task.id);assert.equal(messages[0].purpose,'request');
 assert.equal(messages[0].text,undefined); // Worker chooses the stored task text.
 await assert.rejects(context.writeRequestEditor(el,'repair',{...task,json_repair:{request:'repair'}}),/debugger busy/);
 assert.equal(messages[1].purpose,'repair');assert.equal(focused,2);
});

function fixture() {
 const selectors=new Map();
 const adapter=create({all:selector=>selectors.get(selector) || [],inputText:el=>el.value,evidenceText:P.evidenceText});
 // Capture semantic hooks from a real request to the adapter, without tying
 // assertions to selector spellings. Unrecognized DOM remains empty.
 let observed;
 const probe=create({all:selector=>{observed=selector;return [];},inputText:el=>el.value,evidenceText:P.evidenceText});
 probe.replies();const assistant=observed;
 probe.hasRequest({request:'request'});const user=observed;
 probe.isEmptyHome('https://www.qianwen.com/',{value:''});const messages=observed;
 return {adapter,selectors,assistant,user,messages};
}

test('only assistant messages are read and nested reasoning is removed before parsing',()=>{
 const f=fixture();
 const excluded={closest:()=>({})};
 const reply={closest:()=>null,cloneNode:()=>{
   const clone={textContent:'reasoning\nfinal answer'};
   clone.querySelectorAll=()=>[{remove:()=>{clone.textContent='final answer';}}];
   return clone;
 }};
 f.selectors.set(f.assistant,[excluded,reply]);
 assert.deepEqual(f.adapter.replies().map(el=>el.innerText),['final answer']);
 f.selectors.set('.configured-final',[reply]);
 assert.equal(f.adapter.replies({reply:'.configured-final'})[0].innerText,'final answer');
});

test('only full submitted user messages authorize a binding; editor drafts and prefixes do not',()=>{
 const f=fixture(),task={request:'complete unique request'};
 f.selectors.set(f.user,[{innerText:'complete unique',closest:()=>null}]);
 assert.equal(f.adapter.hasRequest(task),false);
 f.selectors.set(f.user,[{innerText:task.request,closest:()=>({})}]);
 assert.equal(f.adapter.hasRequest(task),false);
 f.selectors.set('.submitted-user',[{innerText:task.request,closest:()=>null}]);
 assert.equal(f.adapter.hasRequest({...task,settings:{user:'.submitted-user'}}),true);
});

test('a non-home route, draft, or recognized message prevents blank-home classification',()=>{
 const f=fixture();
 assert.equal(f.adapter.isEmptyHome('https://www.qianwen.com/',{value:''}),true);
 assert.equal(f.adapter.isEmptyHome('https://www.qianwen.com/session',{value:''}),false);
 assert.equal(f.adapter.isEmptyHome('https://www.qianwen.com/',{value:'draft'}),false);
 f.selectors.set(f.messages,[{}]);
 assert.equal(f.adapter.isEmptyHome('https://www.qianwen.com/',{value:''}),false);
});
