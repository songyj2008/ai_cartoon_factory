/* Explicit video attachments for approved-video learning, before prompt send. */
(function(root) {
  'use strict';
  async function upload(task, env=globalThis) {
    const files=task.attachments || [];
    if(!files.length)return null;
    if(files.length!==1)throw new Error('MEDIA_ATTACHMENT_ERROR: 本任务只允许一个已确认成片');
    const attachment=files[0], doc=env.document;
    const pause=ms=>new Promise(resolve=>env.setTimeout(resolve,ms));
    const hasName=()=>Boolean(doc.body?.innerText?.includes(attachment.name)) ||
      [...doc.querySelectorAll('[title],[aria-label]')].some(el=>
        (el.getAttribute('title') || el.getAttribute('aria-label') || '').includes(attachment.name));
    const response=await env.chrome.runtime.sendMessage({type:'AICF_TASK_ATTACHMENT',taskId:task.id,
      attachmentId:attachment.id,bindingEpoch:task.binding_epoch});
    if(!response?.ok)throw new Error('MEDIA_ATTACHMENT_ERROR: '+(response?.error || '视频附件读取失败'));
    if(!hasName()) {
      const accepts=input=>{
        const allowed=(input.accept || '').toLowerCase().split(',').map(v=>v.trim()).filter(Boolean);
        const suffix='.'+attachment.name.split('.').pop().toLowerCase();
        return !allowed.length || allowed.some(v=>v==='*/*' || v===suffix || v===response.mime || v==='video/*');
      };
      let input=[...doc.querySelectorAll('input[type="file"]')].find(accepts);
      if(!input) {
        const buttons=[...doc.querySelectorAll('button,[role="button"]')].filter(el=>
          el.getClientRects().length && /^(上传文件|上传视频|添加附件|添加文件|附件|upload files?|attach files?)(?:\s|$)/i.test(
            (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || '').trim()));
        if(buttons.length===1)buttons[0].click();
        for(let i=0;i<20 && !input;i++) {
          await pause(250);
          input=[...doc.querySelectorAll('input[type="file"]')].find(accepts);
        }
      }
      if(!input)throw new Error('MEDIA_ATTACHMENT_UNSUPPORTED: 此网页未提供支持视频的上传入口，请选择支持视频附件的网页 LLM');
      const bytes=Uint8Array.from(env.atob(response.base64),c=>c.charCodeAt(0));
      const file=new env.File([bytes],attachment.name,{type:response.mime});
      const transfer=new env.DataTransfer();transfer.items.add(file);input.files=transfer.files;
      input.dispatchEvent(new env.Event('input',{bubbles:true}));
      input.dispatchEvent(new env.Event('change',{bubbles:true}));
    }
    let readyAt=0;
    for(let i=0;i<100;i++) {
      const progress=[...doc.querySelectorAll('[role="progressbar"]')].some(el=>el.getClientRects().length);
      const errors=[...doc.querySelectorAll('[role="alert"]')].map(el=>el.innerText || '').join(' ');
      if(/不支持|无法上传|上传失败|unsupported|failed to upload|upload failed/i.test(errors))
        throw new Error('MEDIA_ATTACHMENT_UNSUPPORTED: 网页拒绝视频附件：'+errors.slice(0,200));
      if(hasName() && !progress) {
        if(!readyAt)readyAt=Date.now();
        if(Date.now()-readyAt>=1000)return {kind:'media_attachment',id:attachment.id,
          sha256:response.sha256,name:attachment.name};
      } else readyAt=0;
      await pause(250);
    }
    throw new Error('MEDIA_ATTACHMENT_ERROR: 视频附件未完成上传，未发送文字请求；请检查网页上传状态');
  }
  root.TextAttachments={upload};
  if(typeof module!=='undefined')module.exports=root.TextAttachments;
})(globalThis);
