import asyncio


class QueueState:
    """Tracks in-flight (queued, not-yet-throttled-through) request counts —
    the live "how backed up are we right now" gauge shown on stat cards and
    the per-model breakdown table.

    Historical queue depth and token levels for charts live in PostgreSQL
    (see app/db.py's queue_samples table), written on a periodic sampler in
    main.py — this class only holds the current instantaneous count.
    """

    def __init__(self):
        self.global_queued = 0
        self.by_model: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def inc(self, model: str):
        async with self._lock:
            self.global_queued += 1
            self.by_model[model] = self.by_model.get(model, 0) + 1

    async def dec(self, model: str):
        async with self._lock:
            self.global_queued = max(0, self.global_queued - 1)
            self.by_model[model] = max(0, self.by_model.get(model, 0) - 1)

    async def snapshot(self) -> dict:
        async with self._lock:
            return {"queued": self.global_queued, "by_model": dict(self.by_model)}

    async def reset(self):
        async with self._lock:
            self.global_queued = 0
            self.by_model.clear()
