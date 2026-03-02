from __future__ import annotations

import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(self, max_items: int = 512, ttl_seconds: int = 900):
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._store: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        item = self._store.get(key)
        if item is None:
            return None
        expires_at, value = item
        now = time.time()
        if now > expires_at:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return value

    def set(self, key: str, value: T) -> None:
        now = time.time()
        self._store[key] = (now + self.ttl_seconds, value)
        self._store.move_to_end(key)
        while len(self._store) > self.max_items:
            self._store.popitem(last=False)

