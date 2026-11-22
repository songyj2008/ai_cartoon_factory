"use strict";
(function(root) {
  const boot = crypto.randomUUID();
  async function receipt(chrome, build) {
    const saved = (await chrome.storage.local.get('extensionUpdate')).extensionUpdate;
    if (!saved) return {};
    return {...saved, phase: saved.phase === 'reloading' && saved.boot !== boot ? 'reloaded' : saved.phase, build};
  }
  function busy(saved) {
    const textBusy = task => task && ['prepared', 'sending', 'waiting'].includes(task.status);
    return textBusy(saved.task) || Object.entries(saved).some(([key, task]) =>
      key.startsWith('bridgeTask:') && task?.bridge && !task.bridge.reported ||
      key.startsWith('videoTask:') && task && !['complete', 'error', 'uncertain', 'cancelled_unclaimed'].includes(task.state));
  }
  async function handle(chrome, command, inFlight = false) {
    if (!command) {
      await chrome.storage.local.remove('extensionUpdate');
      return false;
    }
    const saved = await chrome.storage.local.get(null);
    if (saved.extensionUpdate?.id === command.id && ['reloading', 'failed'].includes(saved.extensionUpdate.phase)) return true;
    if (busy(saved) || inFlight) {
      await chrome.storage.local.set({extensionUpdate: {id: command.id, phase: 'waiting'}});
      return true;
    }
    await chrome.storage.local.set({extensionUpdate: {id: command.id, phase: 'reloading', boot}});
    try { chrome.runtime.reload(); }
    catch (error) {
      await chrome.storage.local.set({extensionUpdate: {id: command.id, phase: 'failed', message: error.message}});
    }
    return true;
  }
  root.ExtensionUpdate = {receipt, handle, busy};
  if (typeof module !== 'undefined') module.exports = root.ExtensionUpdate;
})(globalThis);
