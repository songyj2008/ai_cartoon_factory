"""An author-only view of the complete H3 prompt used by existing save APIs."""

PROMPT_EDITOR_JS = r'''
(() => {
 const A=window.AICF,managedTitles=new Set(['主体定义','参考保留关系','人物音色绑定']);
 function split(text){
  // A section-looking line inside a spoken quotation is still authored dialogue.
  const headers=[...text.matchAll(/<d\b[^>]*>[\s\S]*?<\/d>|^【([^】\r\n]+)】[ \t]*\r?$/gmi)].filter(match=>match[1]);
  const sections=headers.map((header,index)=>({title:header[1],start:header.index,
   text:text.slice(header.index,headers[index+1]?.index??text.length)}));
  const authored=sections.filter(section=>!managedTitles.has(section.title));
  return {headers:authored,body:text.slice(0,headers[0]?.index??text.length)+authored.map(section=>section.text).join(''),
   managed:sections.filter(section=>managedTitles.has(section.title)).map(section=>({text:section.text,
    before:authored.filter(next=>next.start>section.start).map(next=>next.title)}))};
 }
 function compose(body,managed){
  const headers=split(body).headers,inserts=new Map();
  for(const section of managed){
   const title=section.before.find(title=>headers.some(header=>header.title===title));
   const position=title?headers.find(header=>header.title===title).start:body.length;
   inserts.set(position,(inserts.get(position)||'')+section.text);
  }
  let text=body;
  for(const [position,value] of [...inserts].sort((a,b)=>b[0]-a[0]))text=text.slice(0,position)+value+text.slice(position);
  return text;
 }
 A.installH3PromptEditor=(ov,enabled)=>{
  const editor=ov.querySelector('.aicf-prompt-modal-body');
  if(!editor||typeof HTMLTextAreaElement==='undefined')return;
  let state=editor._h3PromptView;
  if(!state){
   const native=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
   if(!native?.get||!native?.set)return;
   const panel=document.createElement('details');panel.className='aicf-prompt-generated';
   const summary=document.createElement('summary');summary.textContent='自动参考信息（只读）';
   const help=document.createElement('p');help.textContent='由左侧素材与各镜入画名单自动生成。要调整人物是否出镜，请修改对应 Shot 的画面人物。';
   const preview=document.createElement('pre');preview.tabIndex=0;preview.setAttribute('aria-label','自动生成的主体、参考和音色信息');
   panel.append(summary,help,preview);editor.before(panel);
   state={enabled:false,full:'',body:'',managed:[],panel,preview};editor._h3PromptView=state;
   const set=text=>{
    text=String(text??'').replace(/\r\n?/g,'\n');
    if(!state.enabled){native.set.call(editor,text);return;}
    const parts=split(text);state.full=text;state.body=parts.body;state.managed=parts.managed;
    native.set.call(editor,parts.body);preview.textContent=parts.managed.map(section=>section.text.trim()).join('\n\n');panel.hidden=!parts.managed.length;
   };
   // Existing saves, comparisons and Shot operations still exchange complete
   // prompts. Native textarea input/selection exposes only authored sections.
   Object.defineProperty(editor,'value',{configurable:true,
    get(){const body=native.get.call(editor);return !state.enabled?body:body===state.body?state.full:compose(body,state.managed);},
    set});
   editor.addEventListener('input',()=>{
    if(state.enabled){const pasted=split(native.get.call(editor));if(pasted.managed.length)set(compose(pasted.body,state.managed));}
   });
  }
  const full=editor.value;state.enabled=!!enabled;state.panel.hidden=!enabled;
  editor.value=full;
 };
})();
'''

PROMPT_EDITOR_CSS = '''
.aicf-prompt-generated{flex:0 0 auto;margin:8px 0;padding:8px 12px;border:1px solid var(--border,#e2e8f0);border-radius:8px;background:var(--surface-muted,#f8fafc)}
.aicf-prompt-generated summary{cursor:pointer;font-size:13px;line-height:28px;color:var(--text-body,#334155)}
.aicf-prompt-generated p{margin:6px 0;font-size:12px;line-height:1.6;color:var(--text-secondary,#475569)}
.aicf-prompt-generated pre{max-height:260px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.6;color:var(--text-body,#334155)}
.aicf-prompt-generated :is(summary,pre):focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:2px}
'''
