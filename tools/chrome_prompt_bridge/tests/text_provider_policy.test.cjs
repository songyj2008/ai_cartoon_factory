const test=require('node:test');
const assert=require('node:assert/strict');
const policies=require('../text_provider_policy.js');
test('recognizes canonical conversation routes and excludes provisional Doubao URLs',()=>{
 assert.equal(policies.policy('chatgpt').conversationId('https://chatgpt.com/g/example/c/final?x=1'),'final');
 assert.equal(policies.policy('deepseek').conversationId('https://chat.deepseek.com/a/chat/s/123'),'123');
 assert.equal(policies.policy('doubao').isCanonicalConversation('https://www.doubao.com/chat/local_abc'),false);
 assert.equal(policies.policy('doubao').isCanonicalConversation('https://www.doubao.com/chat/abc'),true);
 assert.equal(policies.fromUrl('https://chatgpt.com.evil.test/'),null);
});
