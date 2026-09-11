/* The neural sequence is a projection of observed events, never a work simulator. */
(function(root){
  'use strict';
  function lane(event){
    if(event.name.startsWith('provider.')||event.name.startsWith('health.'))return 'providers';
    if(event.name.startsWith('memory.')||event.name.startsWith('file.'))return 'files';
    if(event.name.startsWith('task.')||event.name.startsWith('worker.')||event.name.startsWith('verification.'))return 'tasks';
    return 'system';
  }
  function label(event){
    return event.name.replaceAll('.', ' / ') + ' · ' + event.state.replaceAll('_',' ');
  }
  function createLedger(){
    let sequence=null,stream=null,history=[];
    return {
      ingest(snapshot, now=Date.now()/1000){
        const reset=sequence===null||snapshot.sequence<sequence||snapshot.stream_id!==stream;
        const previous=reset?snapshot.sequence:sequence;
        if(reset)history=[];
        const events=(snapshot.events||[]).filter(e=>Number.isInteger(e.id)&&typeof e.name==='string'&&typeof e.state==='string');
        const fresh=events.filter(e=>e.id>previous&&now-e.ts>=0&&now-e.ts<8);
        const ids=new Set(history.map(e=>e.id));
        for(const e of events)if(!ids.has(e.id)){history.push(e);ids.add(e.id)}
        history=history.slice(-40);sequence=snapshot.sequence;stream=snapshot.stream_id;
        return {fresh:fresh.slice(-12),history:[...history].reverse(),reset,gap:!!snapshot.truncated};
      },
      cursor(){return sequence??0}
    };
  }
  if(typeof module!=='undefined')module.exports={lane,label,createLedger};
  else root.JarvisActivity={lane,label,createLedger};
})(globalThis);
