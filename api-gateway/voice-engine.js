/* Jarvis adaptive voice engine — shared by the dashboard and the voice console.
 *
 * Framework-free and DOM-free: all audio/network I/O is injected by the page.
 * The pure logic (state machine, provider selection, barge-in guard, sentence
 * buffering, submit guard, speech generation, turn history, local end-of-turn)
 * is unit-tested under Node. Browser only.
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  } else {
    root.JarvisVoiceEngine = api;
  }
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const VERSION = '18.2.1';

  // ---------------------------------------------------------------- states
  const STATES = ['off', 'listening', 'thinking', 'speaking', 'error'];

  // Legal transitions. Barge-in is speaking -> listening (interrupt first,
  // the new turn then moves listening -> thinking). Anything else is refused.
  const TRANSITIONS = {
    off: ['listening'],
    listening: ['thinking', 'off', 'error'],
    thinking: ['speaking', 'listening', 'off', 'error'],
    speaking: ['listening', 'off', 'error'],
    error: ['listening', 'off'],
  };

  function createStateMachine(initial, onChange) {
    let current = STATES.includes(initial) ? initial : 'off';
    const notify = typeof onChange === 'function' ? onChange : null;
    return {
      get() { return current; },
      can(to) {
        return (TRANSITIONS[current] || []).includes(to);
      },
      to(next) {
        if (!STATES.includes(next)) return false;
        if (next === current) return true;
        if (!this.can(next)) return false;
        const prev = current;
        current = next;
        if (notify) {
          try { notify(next, prev); } catch (_) { /* listener must not break voice */ }
        }
        return true;
      },
    };
  }

  // ------------------------------------------------------------- provider
  // privacyMode forces local-only (no cloud STT/TTS at all).
  // Repeated Deepgram failures degrade to local until reset.
  const MAX_DG_ERRORS = 2;

  function selectProvider(opts) {
    const o = opts || {};
    if (o.privacyMode) return 'local';
    if (!o.deepgramConfigured) return 'local';
    if ((o.consecutiveErrors || 0) >= (o.maxErrors || MAX_DG_ERRORS)) return 'local';
    return 'deepgram';
  }

  // ------------------------------------------------------- barge-in guard
  // Echo protection: TTS audio leaking into the mic raises the noise floor,
  // so barge-in needs level clearly above floor + a cooldown so one loud
  // moment cannot retrigger repeatedly.
  const BARGE_THRESHOLD = 0.22;
  const BARGE_COOLDOWN_MS = 500;

  function shouldBarge(opts) {
    const o = opts || {};
    if (!o.speaking) return false;
    const level = Number(o.level) || 0;
    const floor = Number(o.floor) || 0;
    if (level - floor < (o.threshold != null ? o.threshold : BARGE_THRESHOLD)) return false;
    const speechFrames = Number(o.speechFrames) || 0;
    const minSpeechFrames = o.minSpeechFrames != null ? o.minSpeechFrames : 2;
    if (speechFrames < minSpeechFrames) return false;
    const now = Number(o.nowMs);
    const last = Number(o.lastBargeMs);
    if (Number.isFinite(now) && Number.isFinite(last)) {
      if (now - last < (o.cooldownMs != null ? o.cooldownMs : BARGE_COOLDOWN_MS)) return false;
    }
    return true;
  }

  // ----------------------------------------------------- sentence buffer
  // Incremental splitter: feed streamed LLM tokens, pull out complete
  // sentences for immediate TTS. Every sentence ending in . ? ! or a newline
  // is spoken as soon as it arrives — never waits for the full response.
  const SENT_END = /([.!?…]+["'”’)]?\s+|\n+)/;

  function createSentenceBuffer() {
    let buf = '';
    function pull(text) {
      buf += text;
      const out = [];
      let m;
      while ((m = buf.match(SENT_END))) {
        const idx = m.index + m[0].length;
        const sentence = buf.slice(0, idx).trim();
        buf = buf.slice(idx);
        if (sentence) out.push(sentence);
      }
      return out;
    }
    return {
      push(chunk) { return pull(String(chunk == null ? '' : chunk)); },
      flush() {
        const rest = buf.trim();
        buf = '';
        return rest;
      },
      pending() { return buf; },
    };
  }

  // -------------------------------------------------------- submit guard
  // Exactly one voice turn may be in flight. A second transcript arriving
  // while THINKING/SPEAKING is either a barge-in (interrupt first) or noise.
  function createSubmitGuard() {
    let owner = null;
    return {
      tryAcquire(id) {
        if (owner !== null) return false;
        owner = id === undefined ? true : id;
        return true;
      },
      release(id) {
        if (id === undefined || owner === id) owner = null;
      },
      inFlight() { return owner !== null; },
    };
  }

  // ------------------------------------------------------------ generation
  // Every spoken response gets a generation id. Barge-in invalidates it so
  // stale TTS chunks can never play over the new turn (no overlapping audio).
  function createGeneration() {
    let current = 0;
    return {
      current() { return current; },
      next() { current += 1; return current; },
      isCurrent(g) { return g === current; },
    };
  }

  // ---------------------------------------------------------------- history
  // Bounded turn history shared across repeated voice turns.
  function createHistory(maxMessages) {
    const max = maxMessages != null ? maxMessages : 24;
    let msgs = [];
    return {
      push(role, content) {
        msgs.push({ role, content: String(content == null ? '' : content) });
        if (msgs.length > max) msgs = msgs.slice(msgs.length - max);
      },
      messages() { return msgs.map((m) => ({ role: m.role, content: m.content })); },
      length() { return msgs.length; },
      reset() { msgs = []; },
    };
  }

  // ------------------------------------------------- local end-of-turn
  // Silence-based turn detector for the Whisper fallback path.
  function localTurnReady(opts) {
    const o = opts || {};
    const now = Number(o.nowMs) || 0;
    const maxMs = o.maxMs != null ? o.maxMs : 45000;
    const silenceMs = o.silenceMs != null ? o.silenceMs : 850;
    if (now - (Number(o.startedMs) || 0) >= maxMs) return 'turn';
    if (!o.heard) return 'wait';
    if (now - (Number(o.lastVoiceMs) || 0) > silenceMs) return 'turn';
    return 'wait';
  }

  // ------------------------------------------------------------ speech text
  // Strip screen-oriented formatting before TTS: never read code, URLs,
  // markdown or UI instructions aloud.
  function speechText(t) {
    return String(t == null ? '' : t)
      .replace(/```[\s\S]*?```/g, ' Code omitted. ')
      .replace(/https?:\/\/\S+/g, ' web link ')
      .replace(/[#*_>`~]/g, '')
      .replace(/\s+/g, ' ')
      .trim();
  }

  return {
    VERSION,
    STATES,
    BARGE_THRESHOLD,
    BARGE_COOLDOWN_MS,
    MAX_DG_ERRORS,
    createStateMachine,
    selectProvider,
    shouldBarge,
    createSentenceBuffer,
    createSubmitGuard,
    createGeneration,
    createHistory,
    localTurnReady,
    speechText,
  };
});
