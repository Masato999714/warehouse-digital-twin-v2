import heapq
import itertools


class Environment:
    """SimPyのEnvironmentを軽量に再現した自作クラス。
    SimPyがインストールされていない環境向けのフォールバックとして使用する。
    """

    def __init__(self):
        self.now = 0.0
        self._event_queue = []
        self._counter = itertools.count()

    def schedule(self, delay, callback, *args):
        t = self.now + delay
        heapq.heappush(self._event_queue, (t, next(self._counter), callback, args))

    def timeout(self, delay):
        return ("timeout", delay)

    def process(self, gen):
        self._advance(gen, None)
        return gen

    def _advance(self, gen, send_value):
        try:
            event = next(gen) if send_value is None else gen.send(send_value)
        except StopIteration:
            return
        kind = event[0]
        if kind == "timeout":
            self.schedule(event[1], self._advance, gen, "ok")
        elif kind == "request":
            resource = event[1]
            resource._request(gen, self)
        else:
            raise ValueError(f"unknown event kind: {kind}")

    def run(self, until):
        while self._event_queue and self._event_queue[0][0] <= until:
            t, _, callback, args = heapq.heappop(self._event_queue)
            self.now = t
            callback(*args)
        self.now = until


class Resource:
    """SimPyのResourceと同じ使い方ができる、簡易版リソースクラス。

    使い方（SimPyと同一）:
        req = resource.request()
        yield req
        ... 処理 ...
        resource.release(req)
    """

    def __init__(self, env, capacity):
        self.env = env
        self.capacity = capacity
        self.count = 0     # 使用中の数（SimPy互換の属性名）
        self.queue = []    # 順番待ちのプロセス一覧（SimPy互換の属性名）

    def request(self):
        return ("request", self)

    def release(self, req=None):
        # SimPyと同じく、release()は同期的に即時実行される（yield不要）
        if self.count > 0:
            self.count -= 1
        if self.queue and self.count < self.capacity:
            next_gen = self.queue.pop(0)
            self.count += 1
            self.env._advance(next_gen, "ok")

    def _request(self, gen, env):
        if self.count < self.capacity:
            self.count += 1
            env._advance(gen, "ok")
        else:
            self.queue.append(gen)

    @property
    def in_use(self):
        return self.count

    @property
    def queue_len(self):
        return len(self.queue)
