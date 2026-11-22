"use strict";
(function(root) {
  const types = ['Chrome', 'Edge', 'Quark', '360', 'Chromium'];
  function detect(agent) {
    const brands = (agent.userAgentData?.brands || []).map(item => item.brand);
    const signals = [agent.userAgent || '', ...brands].join(' ');
    if (/\bQuark(?:Browser|PC)?\b/i.test(signals)) return 'Quark';
    if (/360SE|360EE|360Browser|360Chrome|Qihoo|QIHU|QHBrowser/i.test(signals) ||
        brands.some(brand => /^(360|360安全浏览器|360极速浏览器)$/i.test(brand))) return '360';
    if (/Edg(?:A|iOS)?\/|Microsoft Edge/i.test(signals)) return 'Edge';
    if (brands.some(brand => brand === 'Google Chrome')) return 'Chrome';
    return 'Chromium';
  }
  function resolve(saved, agent) {
    return types.includes(saved.browserType) ? saved.browserType : detect(agent);
  }
  root.BrowserIdentity = {detect, resolve};
  if (typeof module !== 'undefined') module.exports = root.BrowserIdentity;
})(globalThis);
