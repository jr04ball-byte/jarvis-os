class JarvisCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel=inputs[0]?.[0];
    if(channel) { const copy=new Float32Array(channel); this.port.postMessage({samples:copy,rate:sampleRate},[copy.buffer]); }
    return true;
  }
}
registerProcessor('jarvis-capture',JarvisCapture);
