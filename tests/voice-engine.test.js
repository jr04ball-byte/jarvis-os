/* Jarvis V18.2 voice-engine tests — run with: node --test tests/voice-engine.test.js */
'use strict';
const { describe, it } = require('node:test');
const assert = require('node:assert/strict');
const E = require('../api-gateway/voice-engine.js');

describe('state machine', () => {
  it('walks the full voice loop', () => {
    const seen = [];
    const sm = E.createStateMachine('off', (n, p) => seen.push([p, n]));
    assert.equal(sm.to('listening'), true);
    assert.equal(sm.to('thinking'), true);
    assert.equal(sm.to('speaking'), true);
    assert.equal(sm.to('listening'), true);
    assert.deepEqual(seen, [['off', 'listening'], ['listening', 'thinking'], ['thinking', 'speaking'], ['speaking', 'listening']]);
  });

  it('refuses illegal transitions', () => {
    const sm = E.createStateMachine('off', null);
    assert.equal(sm.to('speaking'), false);
    assert.equal(sm.get(), 'off');
    assert.equal(sm.to('thinking'), false);
    sm.to('listening');
    assert.equal(sm.to('speaking'), false); // must capture a turn first
    assert.equal(sm.get(), 'listening');
  });

  it('supports barge-in, error recovery and stop', () => {
    const sm = E.createStateMachine('off', null);
    sm.to('listening'); sm.to('thinking'); sm.to('speaking');
    assert.equal(sm.to('listening'), true); // interrupt: stop TTS, listen again
    sm.to('thinking');
    assert.equal(sm.to('error'), true);
    assert.equal(sm.to('listening'), true); // recover without reinstall
    assert.equal(sm.to('off'), true);
    assert.equal(sm.to('bogus'), false);
  });
});

describe('provider selection', () => {
  it('prefers Deepgram when configured', () => {
    assert.equal(E.selectProvider({ deepgramConfigured: true }), 'deepgram');
  });
  it('uses local Whisper when Deepgram is not configured', () => {
    assert.equal(E.selectProvider({ deepgramConfigured: false }), 'local');
  });
  it('privacy mode forces local even when Deepgram is configured', () => {
    assert.equal(E.selectProvider({ deepgramConfigured: true, privacyMode: true }), 'local');
  });
  it('degrades to local after repeated Deepgram failures and recovers', () => {
    assert.equal(E.selectProvider({ deepgramConfigured: true, consecutiveErrors: 1 }), 'deepgram');
    assert.equal(E.selectProvider({ deepgramConfigured: true, consecutiveErrors: 2 }), 'local');
    assert.equal(E.selectProvider({ deepgramConfigured: true, consecutiveErrors: 0 }), 'deepgram');
  });
});

describe('barge-in guard', () => {
  it('fires on loud speech while speaking', () => {
    assert.equal(E.shouldBarge({ speaking: true, level: 0.5, floor: 0.02, nowMs: 2000, lastBargeMs: 0, speechFrames: 2 }), true);
  });
  it('ignores TTS echo near the noise floor', () => {
    assert.equal(E.shouldBarge({ speaking: true, level: 0.05, floor: 0.02, nowMs: 2000, lastBargeMs: 0 }), false);
  });
  it('never fires when not speaking', () => {
    assert.equal(E.shouldBarge({ speaking: false, level: 0.9, floor: 0.02, nowMs: 2000, lastBargeMs: 0 }), false);
  });
  it('cooldown prevents repeat triggers from one loud moment', () => {
    const base = { speaking: true, level: 0.6, floor: 0.02, nowMs: 1000, lastBargeMs: 800 };
    assert.equal(E.shouldBarge(base), false);
    assert.equal(E.shouldBarge({ ...base, nowMs: 1600, speechFrames: 2 }), true);
  });
});

describe('sentence buffer (streaming TTS input)', () => {
  it('emits complete sentences incrementally, never waiting for the full text', () => {
    const b = E.createSentenceBuffer();
    assert.deepEqual(b.push('Hello there. How are'), ['Hello there.']);
    assert.deepEqual(b.push(' you doing today? I am'), ['How are you doing today?']);
    assert.equal(b.flush(), 'I am');
  });
  it('handles exclamation, question and newline boundaries', () => {
    const b = E.createSentenceBuffer();
    assert.deepEqual(b.push('Wow! Really?\nYes.'), ['Wow!', 'Really?']);
    assert.equal(b.flush(), 'Yes.');
  });
  it('speaks short interjections immediately instead of damming them', () => {
    const b = E.createSentenceBuffer();
    assert.deepEqual(b.push('Hi. '), ['Hi.']);
    assert.equal(b.flush(), '');
  });
});

describe('submit guard (duplicate prevention)', () => {
  it('allows exactly one in-flight turn', () => {
    const g = E.createSubmitGuard();
    assert.equal(g.tryAcquire('t1'), true);
    assert.equal(g.inFlight(), true);
    assert.equal(g.tryAcquire('t2'), false); // duplicate transcript ignored
    g.release('t1');
    assert.equal(g.inFlight(), false);
    assert.equal(g.tryAcquire('t2'), true);
    g.release();
  });
});

describe('speech generation (interruption cancellation)', () => {
  it('invalidates stale TTS chunks on barge-in', () => {
    const gen = E.createGeneration();
    const g1 = gen.current();
    assert.equal(gen.isCurrent(g1), true);
    gen.next(); // user interrupted: new generation
    assert.equal(gen.isCurrent(g1), false);
    assert.equal(gen.isCurrent(gen.current()), true);
  });
});

describe('turn history', () => {
  it('keeps multi-turn context with a cap', () => {
    const h = E.createHistory(4);
    h.push('user', 'hi');
    h.push('assistant', 'hello');
    h.push('user', 'joke');
    h.push('assistant', 'atoms');
    h.push('user', 'another');
    assert.equal(h.length(), 4);
    assert.deepEqual(h.messages()[0], { role: 'assistant', content: 'hello' });
    h.reset();
    assert.equal(h.length(), 0);
  });
});

describe('local end-of-turn detector', () => {
  it('waits for speech, turns on silence, turns on timeout', () => {
    assert.equal(E.localTurnReady({ heard: false, nowMs: 1000, startedMs: 0 }), 'wait');
    assert.equal(E.localTurnReady({ heard: true, lastVoiceMs: 900, nowMs: 1000, startedMs: 0 }), 'wait');
    assert.equal(E.localTurnReady({ heard: true, lastVoiceMs: 0, nowMs: 1000, startedMs: 0 }), 'turn');
    assert.equal(E.localTurnReady({ heard: false, nowMs: 46000, startedMs: 0 }), 'turn');
  });
});

describe('speech text cleanup', () => {
  it('strips code, urls and markdown for TTS', () => {
    const out = E.speechText('See **this** at https://x.y/z and ```code()``` now');
    assert.ok(!out.includes('**') && !out.includes('https://') && !out.includes('```'));
    assert.ok(out.includes('web link') && out.includes('Code omitted.'));
  });
});
