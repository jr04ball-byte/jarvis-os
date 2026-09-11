const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');

function setup(){
  const elements={};const listeners={};let starts=0,stops=0,aborts=0;
  const document={hidden:false,getElementById(id){return elements[id]??={value:'',textContent:'',setPointerCapture(){}}},addEventListener(name,fn){listeners[name]=fn}};
  class Recognition{start(){starts++}stop(){stops++;this.onend()}abort(){aborts++;this.onend()}}
  const window={SpeechRecognition:Recognition,addEventListener(name,fn){listeners[name]=fn}};
  const source=fs.readFileSync(path.join(__dirname,'../api-gateway/ptt.html'),'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
  vm.runInNewContext(source,{document,window,sessionStorage:{getItem(){return ''},setItem(){}},fetch(){throw Error('Unexpected network request')}});
  return {elements,listeners,document,counts:()=>({starts,stops,aborts})};
}
test('PTT never starts automatically and stops on release',()=>{
  const s=setup();assert.equal(s.counts().starts,0);
  s.elements.talk.onpointerdown({preventDefault(){},pointerId:1});
  s.elements.talk.onpointerup();assert.deepEqual(s.counts(),{starts:1,stops:1,aborts:0});
});
test('PTT aborts on hidden page and does not restart',()=>{
  const s=setup();s.elements.talk.onkeydown({code:'Space',repeat:false,preventDefault(){}});
  s.document.hidden=true;s.listeners.visibilitychange();
  s.document.hidden=false;s.listeners.visibilitychange();
  assert.deepEqual(s.counts(),{starts:1,stops:0,aborts:1});
});
test('PTT ignores key repeat and cancels pointer loss',()=>{
  const s=setup();const e={code:'Space',repeat:false,preventDefault(){}};
  s.elements.talk.onkeydown(e);s.elements.talk.onkeydown({...e,repeat:true});
  s.elements.talk.onlostpointercapture();assert.deepEqual(s.counts(),{starts:1,stops:0,aborts:1});
});
