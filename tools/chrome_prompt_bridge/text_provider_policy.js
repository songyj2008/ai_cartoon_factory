/* Pure browser text-provider contract. Keep page selectors in content.js. */
(function(root) {
  'use strict';
  const defaults={allowReadWake:true,allowSendWake:true,stallMs:45000,wakeCooldownMs:90000,maxWakes:3};
  const definitions={
    deepseek:{displayName:'DeepSeek',homeUrl:'https://chat.deepseek.com/',hosts:['chat.deepseek.com'],
      conversationPattern:/^\/a\/chat\/s\/([^/?#]+)/},
    chatgpt:{displayName:'ChatGPT',homeUrl:'https://chatgpt.com/',hosts:['chatgpt.com'],
      conversationPattern:/(?:^|\/)c\/([^/?#]+)/},
    doubao:{displayName:'豆包',homeUrl:'https://www.doubao.com/chat/',hosts:['doubao.com'],
      conversationPattern:/^\/chat\/([^/?#]+)/}
  };
  function policy(id) {
    const entry=definitions[id];
    if(!entry) return null;
    return {id,...defaults,...entry,
      matchesUrl(url) {try {const value=new URL(url);return value.protocol==='https:' &&
        entry.hosts.some(host=>value.hostname===host || (id==='doubao' && value.hostname.endsWith('.'+host)));}catch{return false;}},
      conversationId(url) {try {if(!this.matchesUrl(url))return '';return new URL(url).pathname.match(entry.conversationPattern)?.[1] || '';}catch{return '';}},
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
