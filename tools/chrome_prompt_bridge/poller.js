"use strict";
const clockWorker = new Worker('poller-clock.js');
let busy = false;
clockWorker.onmessage = async () => {
  if (busy) return;
  busy = true;
  let timer;
  try { await Promise.race([
    chrome.runtime.sendMessage({type:'AICF_BACKEND_POLL'}),
    new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('后台心跳确认超时')),2000);})
  ]); }
  catch (error) { console.warn('Task queue poll failed:', error.message); }
  finally { clearTimeout(timer); busy = false; }
};
