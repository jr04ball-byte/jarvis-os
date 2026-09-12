class JarvisCapture extends AudioWorkletProcessor {
  process(inputs) {
    const channel=inputs[0]?.[0];
    if(channel) this.port.postMessage({samples:new Float32Array(channel),rate:sampleRate},[channel.buffer]);
    return true;
  }
}
registerProcessor('jarvis-capture',JarvisCapture);
