const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const adapterSource = fs.readFileSync(path.join(__dirname, '..', 'tools', 'chrome_prompt_bridge', 'video', 'doubao.js'), 'utf8');

function makePage(initialVideoMode, withImage = false, ratioCloses = false, targetDuration = 10,
                  initialDuration = 10, fileInputInitiallyPresent = true, revertPrompt = false, noAriaValue = false,
                  imageViaPath = false, options = {}) {
  let videoMode = initialVideoMode;
  let settingsOpen = false;
  let fileInputPresent = fileInputInitiallyPresent;
  let menuVisible = false;
  let listener;
  const clicks = [];
  const keys = [];
  const previews = [];
  let promptFilled = '';
  const element = (text, x, attributes = {}) => ({
    isConnected: true, childNodes: text ? [{nodeType: 3, textContent: text}] : [],
    children: [], parentElement: null,
    getAttribute: name => attributes[name] || null,
    getBoundingClientRect: () => ({left: x, top: 850, width: 60, height: 30}),
    matches: () => false,
    querySelectorAll: () => [],
  });
  const mode = element('视频生成', options.iconPlus ? 600 : 100);
  const settings = element('自动 · 10s', 200);
  const ratio = element('16:9', 300);
  const plus = element(options.iconPlus || options.structuralPlus ? '' : '+', 500,
    options.iconPlus && !options.structuralPlus ? {'aria-label':'添加'} : {});
  const uploadChoice = element('上传文件或图片', 700);
  const newChat = element('新对话', 20);
  const send = element('发送', 600, {'data-testid':'chat_input_send_button'});
  send.hasAttribute = () => false;
  const fileInput = element('', 700, {accept: 'image/*'});
  fileInput.multiple = true;
  fileInput.dispatchEvent = event => {
    if (event.type === 'change') previews.push(...fileInput.files.map(() => element('', 750)));
  };
  let sliderValue=initialDuration;
  const sliderAttributes = {'aria-valuemin': '4', 'aria-valuemax': '15'};
  if(!noAriaValue)sliderAttributes['aria-valuenow'] = String(initialDuration);
  if(options.ariaIndex)sliderAttributes['aria-valuenow']='6';
  const slider = element('', 500, sliderAttributes);
  const valueLabel = element(initialDuration+'s', 500);
  valueLabel.getBoundingClientRect = () => ({left:500,top:815,right:560,bottom:845,width:60,height:30});
  const editor = element('', 400);
  editor.parentElement = {
    getBoundingClientRect: () => ({height: 100}),
    querySelectorAll: selector => selector.startsWith('.guidance-input-actions') ?
      (options.structuralPlus ? [plus] : []) : selector === 'img' ? previews : selector.includes('button') ? [plus] : [],
    parentElement: null,
  };
  class FakeTextarea {}
  Object.defineProperty(FakeTextarea.prototype, 'value', {
    get() {return this._value || '';},
    set(value) {this._value = value; if(value)promptFilled = value;},
  });
  Object.setPrototypeOf(editor, FakeTextarea.prototype);
  editor.disabled = false;
  editor.readOnly = false;
  editor.focus = () => {};
  editor.dispatchEvent = () => {if(revertPrompt)setTimeout(() => {editor.value = '';},50);};
  editor.getAttribute = name => name === 'placeholder' ?
    (videoMode ? '描述你想要的视频' : '发消息或按住空格说话') : null;
  const document = {
    readyState: options.delayedLoad ? 'loading' : 'complete',
    activeElement: null,
    querySelectorAll(selector) {
      if (selector.includes('textarea')) return [editor];
      if (selector.includes('input[type="range"]')) return settingsOpen ? [slider] : [];
      if (selector.includes('input[type="file"]')) return fileInputPresent ? [fileInput] : [];
      if (selector === 'img') return previews;
      if (selector === 'button,[role="button"],[tabindex="0"],svg') return [plus];
      if (selector.includes('button,[role="button"]')) return [mode, ...(videoMode ? [settings, plus, send] : []), ...(settingsOpen ? [ratio,valueLabel] : []), ...(menuVisible ? [uploadChoice] : []), ...(options.newChat ? [newChat] : [])];
      if (selector === 'span,div,p') return settingsOpen ? [valueLabel] : [];
      return [];
    },
  };
  slider.focus = () => {document.activeElement = slider;};
  const context = {
    document, location: {href: 'https://www.doubao.com/chat/'}, innerHeight: 1000, innerWidth: 1000,
    getComputedStyle: () => ({display: 'block', visibility: 'visible'}),
    chrome: {runtime: {
      onMessage: {addListener: callback => { listener = callback; }},
      async sendMessage(message) {
        assert.equal(document.readyState, 'complete', 'must wait for document load before clicking or uploading');
        if (message.type === 'AICF_VIDEO_CHOOSE_FILE') {
          if (imageViaPath && message.x===530 && message.files[0] === 'C:\\reference.png') {
            previews.push(element('', 750));
            menuVisible = false;
            return {ok:true};
          }
          return {ok:false,error:'wrong file'};
        }
        if (message.type === 'AICF_VIDEO_TRUSTED_KEY') {
          keys.push(message.key);
          sliderValue--;
          if(!noAriaValue)sliderAttributes['aria-valuenow'] = String(sliderValue);
          valueLabel.childNodes[0].textContent=sliderValue+'s';
          settings.childNodes[0].textContent = '16:9 · ' + sliderValue + 's';
          return {ok:true};
        }
        clicks.push(message.x);
        if (message.x === 50) {
          sliderValue=10;sliderAttributes['aria-valuenow']='10';
          valueLabel.childNodes[0].textContent='10s';
          videoMode=false;settingsOpen=false;
        }
        if (message.x === 130) videoMode = true;
        if (message.x === 230) settingsOpen = true;
        if (message.x === 330 && ratioCloses) settingsOpen = false;
        if (message.x === 530) {fileInputPresent = true;menuVisible = true;}
        if (message.x === 630) editor.value = '';
        return {ok: true};
      },
    }},
    HTMLTextAreaElement: FakeTextarea, HTMLInputElement: class {},
    File: class {constructor(bytes,name,options) {this.bytes=bytes;this.name=name;this.type=options.type;}},
    DataTransfer: class {
      constructor() {this.files=[];this.items={add:file=>this.files.push(file)};}
    },
    Event: class {constructor(type) {this.type=type;}},
    setTimeout, clearTimeout,
  };
  if(options.delayedLoad)setTimeout(()=>{document.readyState='complete';},300);
  vm.runInNewContext(adapterSource, context);
  const run = () => new Promise(resolve => listener({type: 'AICF_VIDEO_SUBMIT', task: {
    id: 'test', input: {aspect_ratio: '16:9', duration_sec: targetDuration, prompt: '镜头推进'},
    images: withImage ? [imageViaPath ? {name:'reference.png',path:'C:\\reference.png'} :
      {name: 'reference.png', mime: 'image/png', data: [1,2,3]}] : [],
  }}, null, resolve));
  return {run, clicks, keys, previews, get promptFilled() {return promptFilled;}};
}

test('already selected video mode is not clicked a second time', async () => {
  const page = makePage(true);
  const result = await page.run();
  assert.match(result.error, /没有参考图片/);
  assert.equal(result.before_send, true);
  assert.deepEqual(page.clicks, [230, 330]);
});

test('chat mode enters video mode and finds ratio text inside non-button element', async () => {
  const page = makePage(false);
  const result = await page.run();
  assert.match(result.error, /没有参考图片/);
  assert.deepEqual(page.clicks, [130, 230, 330]);
});

test('duration settings reopen when selecting ratio closes the popover', async () => {
  const page = makePage(true, false, true);
  const result = await page.run();
  assert.match(result.error, /没有参考图片/);
  assert.deepEqual(page.clicks, [230, 330, 230]);
});

test('reference preview appears and prompt is filled before sending', async () => {
  const page = makePage(true, true);
  const result = await page.run();
  assert.equal(result.ok, true, JSON.stringify({result, clicks:page.clicks, previews:page.previews.length, prompt:page.promptFilled}));
  assert.equal(page.previews.length, 1);
  assert.equal(page.promptFilled, '镜头推进');
  assert.deepEqual(page.clicks, [230, 330, 430, 630]);
  assert.deepEqual(page.keys, []);
});

test('local reference path is chosen through the plus menu before prompt entry', async () => {
  const page = makePage(true, true, false, 10, 10, true, false, false, true);
  const result = await page.run();
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.equal(page.previews.length, 1);
  assert.equal(page.promptFilled, '镜头推进');
});

test('waits for document load before image upload and prompt entry', async () => {
  const page=makePage(true,true,false,10,10,true,false,false,true,{delayedLoad:true});
  const result=await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.equal(page.promptFilled,'镜头推进');
});

test('displayed seconds take precedence over a numeric slider index',async()=>{
  const page=makePage(true,true,false,10,10,true,false,false,true,{ariaIndex:true});
  const result=await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.deepEqual(page.keys,[]);
  assert.equal(page.promptFilled,'镜头推进');
});

test('icon-only add button uploads before filling prompt',async()=>{
  const page=makePage(true,true,false,10,10,true,false,false,true,{iconPlus:true});
  const result=await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.equal(page.previews.length,1);
  assert.equal(page.promptFilled,'镜头推进');
});

test('observed guidance actions wrapper finds unlabeled SVG upload button',async()=>{
  const page=makePage(true,true,false,10,10,true,false,false,true,{structuralPlus:true});
  const result=await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.equal(page.previews.length,1);
  assert.equal(page.promptFilled,'镜头推进');
});

test('new empty chat at the same URL can restore duration then upload and fill', async () => {
  const page=makePage(true,true,false,10,6,true,false,false,true,{newChat:true});
  const result=await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.equal(page.previews.length,1);
  assert.equal(page.promptFilled,'镜头推进');
  assert.deepEqual(page.keys,[]);
});

test('free duration decreases only with ArrowLeft before image upload and prompt fill', async () => {
  const page = makePage(true, true, false, 8);
  const result = await page.run();
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(page.keys, ['ArrowLeft','ArrowLeft']);
  assert.equal(page.previews.length, 1);
  assert.equal(page.promptFilled, '镜头推进');
  assert.deepEqual(page.clicks, [230,330,430,630]);
});

test('free duration never moves right when the page starts below the requested time', async () => {
  const page = makePage(true, true, false, 10, 9);
  const result = await page.run();
  assert.match(result.error, /无法唯一识别豆包“新对话”入口/);
  assert.equal(result.before_send, true);
  assert.deepEqual(page.keys, []);
  assert.equal(page.previews.length, 0);
  assert.equal(page.promptFilled, '');
});

test('opens the plus control when the reference upload input is mounted on demand', async () => {
  const page = makePage(true, true, false, 10, 10, false);
  const result = await page.run();
  assert.equal(result.ok, true, JSON.stringify(result));
  assert.deepEqual(page.clicks, [230,330,430,530,630]);
  assert.equal(page.previews.length, 1);
  assert.equal(page.promptFilled, '镜头推进');
});

test('does not send when the video page clears the filled prompt', async () => {
  const page = makePage(true, true, false, 10, 10, true, true);
  const result = await page.run();
  assert.match(result.error, /未保留视频提示词/);
  assert.equal(result.before_send, true);
  assert.deepEqual(page.clicks, [230,330,430]);
});

test('reads the visible 10s label when the slider has no aria value and avoids any movement', async () => {
  const page = makePage(true, true, false, 10, 10, true, false, true);
  const result = await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.deepEqual(page.keys,[]);
  assert.equal(page.previews.length,1);
  assert.equal(page.promptFilled,'镜头推进');
});

test('reads the changing visible duration label after each leftward key', async () => {
  const page = makePage(true, true, false, 9, 10, true, false, true);
  const result = await page.run();
  assert.equal(result.ok,true,JSON.stringify(result));
  assert.deepEqual(page.keys,['ArrowLeft']);
  assert.equal(page.previews.length,1);
  assert.equal(page.promptFilled,'镜头推进');
});
