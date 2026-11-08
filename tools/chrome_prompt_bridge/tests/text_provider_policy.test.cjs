const test=require('node:test');
const assert=require('node:assert/strict');
const policies=require('../text_provider_policy.js');

test('Qianwen rejects oversized input without truncating the original and leaves other providers unchanged',()=>{
 const site=policies.policy('qianwen');
 assert.doesNotThrow(()=>site.assertInputLength('字'.repeat(10000)));
 const request='字'.repeat(13248);
 assert.throws(()=>site.assertInputLength(request),/PROVIDER_INPUT_LIMIT:.*10000.*13248/);
 assert.equal(request.length,13248);
 assert.doesNotThrow(()=>policies.policy('deepseek').assertInputLength(request));
});
test('recognizes canonical conversation routes and excludes provisional Doubao URLs',()=>{
 assert.equal(policies.policy('chatgpt').conversationId('https://chatgpt.com/g/example/c/final?x=1'),'final');
 assert.equal(policies.policy('deepseek').conversationId('https://chat.deepseek.com/a/chat/s/123'),'123');
 assert.equal(policies.policy('doubao').isCanonicalConversation('https://www.doubao.com/chat/local_abc'),false);
 assert.equal(policies.policy('doubao').isCanonicalConversation('https://www.doubao.com/chat/abc'),true);
 assert.equal(policies.fromUrl('https://chatgpt.com.evil.test/'),null);
});

test('Qianwen uses exact HTTPS host and opaque route keys without assuming a server ID',()=>{
 const site=policies.policy('qianwen');
 assert.equal(site.available,true);
 assert.deepEqual(site.capabilities,[]);
 assert.equal(site.homeUrl,'https://www.qianwen.com/');
 assert.equal(site.conversationId(site.homeUrl),'');
 for(const route of ['/opaque-session','/?session=opaque','#session=opaque']) {
   const url=new URL(route,site.homeUrl);
   assert.equal(site.conversationId(url.href),url.pathname+url.search+url.hash);
 }
 for(const url of ['http://www.qianwen.com/','https://qianwen.com/','https://www.qianwen.com.evil.test/','https://evil.test/?www.qianwen.com'])
   assert.equal(site.matchesUrl(url),false);
 assert.equal(policies.fromName('千问').id,'qianwen');
});
