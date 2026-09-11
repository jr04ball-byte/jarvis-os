"""Bounded, redacted activity projection of actual gateway events."""
import threading
import time
import uuid
from collections import Counter, deque

from events import Event


class ActivityCore:
    def __init__(self, bus):
        self.bus = bus
        self.lock = threading.RLock()
        self.history = deque(maxlen=200)
        self.counts = Counter()
        self.sequence = 0
        self.stream_id = uuid.uuid4().hex

    def start(self):
        self.bus.subscribe(Event, self.record)

    def close(self):
        self.bus.unsubscribe(Event, self.record)

    def record(self, event):
        # Never export arbitrary payloads, prompts, paths or provider errors.
        name = event.name
        state = 'active'
        outcome = getattr(event, 'status', '')
        if 'failed' in name or outcome == 'failed':
            state = 'failed'
        elif outcome.startswith('blocked'):
            state = 'blocked'
        elif 'passed' in name or 'completed' in name:
            state = 'completed'
        elif 'queued' in name:
            state = 'queued'
        elif 'approval' in name:
            state = 'awaiting_approval'
        elif name == 'health.changed':
            state = 'online' if getattr(event, 'online', False) else 'offline'
        with self.lock:
            self.sequence += 1
            self.counts[name] += 1
            record = dict(id=self.sequence, name=name, state=state, ts=event.ts)
            provider = getattr(event, 'provider', '')
            if provider in {'gemini', 'openai', 'ollama', 'opencode'}:
                record['provider'] = provider
            self.history.append(record)

    def snapshot(self, after=0):
        with self.lock:
            latest = self.history[-1] if self.history else None
            return dict(sequence=self.sequence, stream_id=self.stream_id,
                        state=latest['state'] if latest and time.time()-latest['ts'] < 8 else 'idle',
                        latest=latest, counts=dict(self.counts),
                        events=[dict(e) for e in self.history if e['id'] > after],
                        truncated=bool(self.history and after and after < self.history[0]['id']-1))
