import heapq
import itertools


class Environment:
    def __init__(self):
        self.now = 0.0
        self._queue = []
        self._counter = itertools.count()

    def schedule(self, delay, callback, *args):
        t = self.now + delay
        heapq.heappush(self._queue, (t, next(self._counter), callback, args))

    def timeout(self, delay):
        return ("timeout", delay)

    def process(self, gen):
        self._advance(gen, None)

    def _advance(self, gen, send_value):
        try:
            event = next(gen) if send_value is None else gen.send(send_value)
        except StopIteration:
            return
        kind = event[0]
        if kind == "timeout":
            self.schedule(event[1], self._advance, gen, "ok")
        elif kind == "request":
            resource, priority = event[1], event[2]
            resource._request(gen, self, priority)
        elif kind == "release":
            resource, token = event[1], event[2]
            resource._release(token)
            self._advance(gen, "ok")
        else:
            raise ValueError(f"unknown event kind: {kind}")

    def run(self, until):
        while self._queue and self._queue[0][0] <= until:
            t, _, callback, args = heapq.heappop(self._queue)
            self.now = t
            callback(*args)
        self.now = until


class Resource:
    def __init__(self, env, capacity):
        self.env = env
        self.capacity = capacity
        self.in_use = 0
        self._waiting = []
        self._token_counter = itertools.count()

    def request(self):
        return ("request", self, 0)

    def release(self, token):
        return ("release", self, token)

    def _request(self, gen, env, priority):
        if self.in_use < self.capacity:
            self.in_use += 1
            token = next(self._token_counter)
            env._advance(gen, token)
        else:
            self._waiting.append(gen)

    def _release(self, token):
        self.in_use -= 1
        if self._waiting and self.in_use < self.capacity:
            next_gen = self._waiting.pop(0)
            self.in_use += 1
            token = next(self._token_counter)
            self.env._advance(next_gen, token)

    @property
    def queue_len(self):
        return len(self._waiting)