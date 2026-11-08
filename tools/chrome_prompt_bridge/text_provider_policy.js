/* Pure browser text-provider contract. Keep page selectors in content.js. */
(function(root) {
  'use strict';
  const defaults={available:true,allowReadWake:true,allowSendWake:true,stallMs:45000,wakeCooldownMs:90000,maxWakes:3,
    capabilities:['video_learning_attachments_v1']};
  const definitions={
    deepseek:{displayName:'DeepSeek',homeUrl:'https://chat.deepseek.com/',hosts:['chat.deepseek.com'],
      conversationPattern:/^\/a\/chat\/s\/([^/?#]+)/},
    chatgpt:{displayName:'ChatGPT',homeUrl:'https://chatgpt.com/',hosts:['chatgpt.com'],
      conversationPattern:/(?:^|\/)c\/([^/?#]+)/},
    doubao:{displayName:'豆包',homeUrl:'https://www.doubao.com/chat/',hosts:['doubao.com'],
      conversationPattern:/^\/chat\/([^/?#]+)/},
    qianwen:{displayName:'千问',homeUrl:'https://www.qianwen.com/',hosts:['www.qianwen.com'],
      available:true,capabilities:[],routeIdentity:true,maxInputChars:10000}
  };
  function policy(id) {
    const entry=definitions[id];
    if(!entry) return null;
    return {id,...defaults,...entry,
      assertInputLength(text) {
        const count=String(text || '').length;
        if(this.maxInputChars && count>this.maxInputChars)
          throw new Error(`PROVIDER_INPUT_LIMIT: ${this.displayName}单条输入上限 ${this.maxInputChars} 字符，本次 ${count} 字符。未填入或发送；完整原请求保留。请减少上下文或手动选择支持更长输入的服务。`);
      },
      matchesUrl(url) {try {const value=new URL(url);return value.protocol==='https:' &&
        entry.hosts.some(host=>value.hostname===host || (id==='doubao' && value.hostname.endsWith('.'+host)));}catch{return false;}},
      conversationId(url) {try {
        if(!this.matchesUrl(url))return '';
        const value=new URL(url);
        // An opaque route key, not an assumed server conversation ID. Page
        // request evidence must authorize the first binding on Qianwen.
        if(entry.routeIdentity)return value.pathname==='/' && !value.search && !value.hash ? '' :
          value.pathname+value.search+value.hash;
        return value.pathname.match(entry.conversationPattern)?.[1] || '';
      }catch{return '';}},
      isCanonicalConversation(url) {const value=this.conversationId(url);return Boolean(value && !(id==='doubao' && value.startsWith('local_')));}
    };
  }
  const ids=Object.keys(definitions);
  function fromUrl(url) {return ids.map(policy).find(value=>value.matchesUrl(url)) || null;}
  function fromName(name) {return ids.map(policy).find(value=>value.displayName===name) || null;}
  const api={ids,policy,fromUrl,fromName};
  root.TextProviderPolicy=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
