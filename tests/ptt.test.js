const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function setup(){
  const elements={},listeners={};let starts=0,stops=0,trackStops=0;
  const classList={add(){},remove(){}};
  const document={hidden:false,getElementById(id){return elements[id]??={value:'',textContent:'',hidden:false,classList,setPointerCapture(){}}},addEventListener(name,fn){listeners[name]=fn}};
  class Recorder{
    static isTypeSupported(){return true}
    constructor(stream,options){this.stream=stream;this.mimeType=options?.mimeType||'audio/webm';this.onstop=null}
    start(){starts++}
    stop(){stops++}
  }
  const stream={getTracks(){return[{stop(){trackStops++}}]}};
  const navigator={mediaDevices:{async getUserMedia(){return stream}}};
  const window={MediaRecorder:Recorder,addEventListener(name,fn){listeners[name]=fn}};
  const source=fs.readFileSync(path.join(__dirname,'../api-gateway/ptt.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(source,{document,window,navigator,MediaRecorder:Recorder,sessionStorage:{getItem(){return ''},setItem(){}},fetch(){throw Error('Unexpected network request')},setTimeout,clearTimeout,Blob,FormData});
  return {elements,listeners,document,counts:()=>({starts,stops,trackStops})};
}

test('PTT opens the microphone only while pressed',async()=>{
  const s=setup();assert.equal(s.counts().starts,0);
  s.elements.talk.onpointerdown({preventDefault(){},pointerId:1});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(s.counts().starts,1);
  s.elements.talk.onpointerup();
  assert.deepEqual(s.counts(),{starts:1,stops:1,trackStops:1});
});

test('PTT releases the microphone when the page is hidden',async()=>{
  const s=setup();s.elements.talk.onkeydown({code:'Space',repeat:false,preventDefault(){}});
  await new Promise(resolve=>setImmediate(resolve));
  s.document.hidden=true;s.listeners.visibilitychange();
  assert.deepEqual(s.counts(),{starts:1,stops:1,trackStops:1});
});
