"use strict";
// Pure checks shared by the page adapter and fixture tests.
(function(root) {
  const normalize=value=>String(value || '').replace(/\s+/g,' ').trim();
  const enhancedModelName = /^MiniMax\s*H3[-\s]*RH\s*(?:增强版|Enhanced)$/i;
  function uniqueResource(items,name,type) {
    const matches=items.filter(item=>item.name===name && item.type===type);
    if(matches.length>1)throw new Error('资源库存在多个同名素材，请先整理：'+name);
    return matches[0] || null;
  }
  function validateSelection(media,selected) {
    if(media.length!==selected.length)throw new Error('Runow 已选素材数量与本次任务不一致');
    for(let i=0;i<media.length;i++) {
      if(media[i].name!==selected[i].name || media[i].type!==selected[i].type)
        throw new Error('Runow 已选素材顺序不一致：'+media[i].name);
    }
    return true;
  }
  function splitReferences(parts) {
    if(!Array.isArray(parts))throw new Error('Runow 提示词缺少结构化引用');
    for(const part of parts) {
      if(typeof part.text==='string' && !part.reference_id)continue;
      if(typeof part.reference_id==='string' && /^@(图片|音频|视频)\d+$/.test(part.label || ''))continue;
      throw new Error('Runow 提示词引用结构无效');
    }
    return parts;
  }
  function newOutput(current,baseline) {
    const fresh=current.filter(item=>item.url && !baseline.includes(item.url));
    if(fresh.length>1)throw new Error('Runow 出现多个新视频，无法确认所属任务');
    return fresh[0] || null;
  }
  const resultTaskName = 'Minimax H3 RH Enhanced Multi-Reference';

  function matchingResultTask(records, taskId) {
    if (!taskId) throw new Error('缺少本次提交的远端任务 ID，需人工核对历史任务');
    const matches = records.filter(record => record.id === taskId);
    if (matches.length > 1) throw new Error('任务列表存在重复任务 ID');
    return matches[0] || null;
  }

  function taskResult(response, taskId) {
    if (String(response.taskId) !== taskId && String(response.parentTaskId) !== taskId) {
      return null;
    }
    const receipt = {taskId, resultTaskId: String(response.taskId), source: 'runninghub_task_list'};
    const status = String(response.status || '').toUpperCase();
    if (['FAILED', 'FAILURE', 'ERROR', 'CANCELLED', 'CANCELED'].includes(status)) {
      return {ok: true, ready: false, failed: true, receipt,
        error: response.errorMessage || response.errorCode || 'RunningHub 列表任务失败或已取消'};
    }
    const video = (response.results || []).find(item =>
      /^https?:\/\//i.test(item.url || '') &&
      (item.outputType === 'mp4' || /\.mp4(?:\?|$)/i.test(item.url)));
    if (status === 'SUCCESS' && video) {
      return {ok: true, ready: true, url: video.url, receipt};
    }
    return {ok: true, ready: false, receipt};
  }

  function pagePath(pathname) {return pathname.replace(/^\/zh-cn(?=\/)/,'');}
  function originForChannel(channel) {
    return ({runninghub_web:'https://www.runninghub.cn',runninghub_international_web:'https://www.runninghub.ai'})[channel];
  }
  const api={pagePath,originForChannel,normalize,enhancedModelName,uniqueResource,validateSelection,splitReferences,newOutput,
    resultTaskName,matchingResultTask,taskResult};
  root.RunowContract=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
