"""Carry the browser's Gradio session and saved runtime on custom API calls."""
BROWSER_RUNTIME_JS = r'''
(() => {
  if(window.__AICF_RUNTIME_FETCH_BOUND__)return;
  window.__AICF_RUNTIME_FETCH_BOUND__=true;
  window.AICF=window.AICF||{};
  function captureSession(url){
    const session=new URL(url,window.location.href).searchParams.get('session_hash');
    if(session)window.AICF.browserSessionId=session;
  }
  if(window.EventSource){
    const NativeEventSource=window.EventSource;
    window.EventSource=class extends NativeEventSource{
      constructor(url,options){captureSession(url);super(url,options);}
    };
  }
  const nativeFetch=window.fetch.bind(window);
  window.fetch=async function(input,options){
    const url=new URL(typeof input==='string'?input:input.url,window.location.href);
    if(url.origin!==window.location.origin)return nativeFetch(input,options);
    const A=window.AICF;
    if(url.pathname.startsWith('/gradio_api/')){
      captureSession(url.href);
      try{
        const raw=options?.body;
        const body=typeof raw==='string'?JSON.parse(raw):input instanceof Request?await input.clone().json():null;
        if(body?.session_hash)A.browserSessionId=body.session_hash;
      }catch{}
    }
    if(!url.pathname.startsWith('/api/'))return nativeFetch(input,options);
    const init={...options},headers=new Headers(options?.headers||(input instanceof Request?input.headers:undefined));
    if(A.browserSessionId)headers.set('X-AICF-Session',A.browserSessionId);
    const method=String(options?.method||(input instanceof Request?input.method:'GET')).toUpperCase();
    if(method!=='GET'&&A.pageRuntime&&!headers.has('X-AICF-Runtime')){
      headers.set('X-AICF-Runtime',encodeURIComponent(A.pageRuntime));
      headers.set('X-AICF-Runtime-Encoding','uri');
    }
    init.headers=headers;
    const revision=A.promptRuntimeRevision;
    const response=await nativeFetch(input,init);
    if(url.pathname==='/api/project/current_state'&&response.ok){
      try{const data=await response.clone().json();
        if(revision===A.promptRuntimeRevision&&data.project_dir)A.pageRuntime=data.project_dir;
      }catch{}
    }
    return response;
  };
})();
'''
