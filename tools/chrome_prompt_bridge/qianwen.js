/* Qianwen DOM adapter. Slate editor and send-query hooks were verified from
 * user-supplied homepage HTML. Reply hooks still need a real answer snapshot.
 * No private React/Slate state or unverified CSS class names are used. */
(function(root) {
  'use strict';
  const userSelector='[data-message-author-role="user"],[data-message-role="user"],[data-role="user"]';
  const assistantSelector='[data-message-author-role="assistant"],[data-message-role="assistant"],[data-role="assistant"]';
  const thinkingSelector='[data-message-type="thinking"],[data-role="reasoning"],[data-type="reasoning"],[aria-label="思考过程"],[aria-label="深度思考"]';
  function isSlate(el) {return el.getAttribute?.('data-slate-editor')==='true';}
  function readEditor(el) {
    if(!isSlate(el))return typeof el.value==='string' ? el.value : el.innerText;
    const block=node=>node.nodeType===1 && node.getAttribute('data-slate-node')==='element' &&
      node.getAttribute('data-slate-inline')!=='true';
    const read=node=>{
      if(node.nodeType===3)return node.nodeValue || '';
      if(node.nodeType!==1)return '';
      if(node.getAttribute('data-slate-placeholder')==='true')return '';
      // Remove Slate's sentinel only inside its own zero-width leaf. A real
      // data-slate-string may legitimately contain zero-width characters.
      if(node.hasAttribute('data-slate-zero-width'))return (node.textContent || '').replace(/\uFEFF/g,'');
      if(node.tagName==='BR')return '\n';
      const children=Array.from(node.childNodes);
      return children.map((child,index)=>(index>0 && (block(child) || block(children[index-1])) ? '\n' : '')+read(child)).join('');
    };
    return read(el);
  }
  function create({all,inputText,evidenceText}) {
    function replies(settings={}) {
      const nodes=all(settings.reply || assistantSelector)
        .filter(el=>!el.closest(userSelector+',textarea,input,[contenteditable="true"],'+thinkingSelector));
      // Exclude nested reasoning from a semantic assistant message. Never join
      // unrelated messages or include a second answer from the conversation.
      return nodes.map(el=>{
        const copy=el.cloneNode(true);
        copy.querySelectorAll(thinkingSelector+',script,style,textarea,input,[contenteditable]').forEach(node=>node.remove());
        return {innerText:copy.textContent || '',textContent:copy.textContent || '',closest:()=>null};
      });
    }
    function hasRequest(currentTask) {
      const expected=evidenceText(currentTask.request);
      return Boolean(expected) && all(currentTask.settings?.user || userSelector)
        .filter(el=>!el.closest('textarea,input,[contenteditable="true"]'))
        .some(el=>evidenceText(el.innerText || el.textContent).includes(expected));
    }
    function isGenerating() {
      return all('button,[role="button"]').some(el=>
        [el.getAttribute('aria-label'),el.getAttribute('title'),el.innerText].some(label=>
          /^(停止|停止生成|停止回答|stop|stop generating|stop response)$/i.test((label || '').trim())));
    }
    function isEmptyHome(url,editor,settings={}) {
      const value=new URL(url);
      return value.pathname==='/' && !value.search && !value.hash && !inputText(editor).trim() &&
        !all(userSelector+','+assistantSelector).length &&
        !(settings.user && all(settings.user).length) && !(settings.reply && all(settings.reply).length);
    }
    return {replies,hasRequest,isGenerating,isEmptyHome};
  }
  root.QianwenPage={create,readEditor,isSlate};
  if(typeof module!=='undefined')module.exports=root.QianwenPage;
})(globalThis);
