const test = require('node:test');
const assert = require('node:assert/strict');
const identity = require('../browser_identity.js');

test('vendor browser signals take precedence over Chrome compatibility identifiers', () => {
  const agent = {userAgent: 'Mozilla Chrome/144.0 QuarkPC/7.4',
    userAgentData: {brands: [{brand: 'Chromium'}, {brand: 'Google Chrome'}]}};
  assert.equal(identity.detect(agent), 'Quark');
  agent.userAgent = 'Mozilla Chrome/144.0';
  agent.userAgentData.brands.push({brand: 'Quark'});
  assert.equal(identity.detect(agent), 'Quark');
  agent.userAgentData.brands.pop();
  agent.userAgent = 'Mozilla Chrome/144.0 360SE/15';
  assert.equal(identity.detect(agent), '360');
  agent.userAgent = 'Mozilla Chrome/144.0 Edg/144.0';
  assert.equal(identity.detect(agent), 'Edge');
});

test('generic Chromium user agents do not identify the browser vendor', () => {
  const agent = {userAgent: 'Mozilla Chrome/144.0 Safari/537.36'};
  assert.equal(identity.detect(agent), 'Chromium');
  agent.userAgentData = {brands: [{brand: 'Chromium'}]};
  assert.equal(identity.detect(agent), 'Chromium');
  agent.userAgentData.brands.push({brand: 'Google Chrome'});
  assert.equal(identity.detect(agent), 'Chrome');
});

test('explicit browser type corrects browsers that hide their vendor', () => {
  const agent = {userAgentData: {brands: [{brand: 'Google Chrome'}]}};
  assert.equal(identity.resolve({browserType: 'Quark', browserName: '工作账号'}, agent), 'Quark');
  assert.equal(identity.resolve({browserType: '360'}, agent), '360');
  assert.equal(identity.resolve({browserType: 'auto'}, agent), 'Chrome');
  assert.equal(identity.resolve({browserType: 'invalid'}, agent), 'Chrome');
});
