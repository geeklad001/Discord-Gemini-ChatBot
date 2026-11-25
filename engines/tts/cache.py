"""In-memory thread-safe LRU cache for hot short TTS audio clips."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Optional


class AudioLRUCache:
    """Thread-safe/asyncio-safe LRU cache bounded by entry count and total byte size."""

    def __init__(self, max_entries: int = 50, max_total_bytes: int = 10 * 1024 * 1024):
        self.max_entries = max(1, int(max_entries))
        self.max_total_bytes = max(1024, int(max_total_bytes))
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self._total_bytes = 0

    def get(self, key: str) -> Optional[tuple[bytes, str]]:
        with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            self._store.move_to_end(key)
            return item

    def put(self, key: str, audio_bytes: bytes, ext: str) -> None:
        size = len(audio_bytes)
        if size <= 0 or size > self.max_total_bytes // 2:
            return
        with self._lock:
            if key in self._store:
                old_bytes, _ = self._store.pop(key)
                self._total_bytes -= len(old_bytes)
            self._store[key] = (audio_bytes, ext)
            self._total_bytes += size
            while len(self._store) > self.max_entries or self._total_bytes > self.max_total_bytes:
                _, (evicted, _) = self._store.popitem(last=False)
                self._total_bytes -= len(evicted)

    def delete_by_prefix(self, prefix: str) -> int:
        if not prefix:
            return 0
        with self._lock:
            matched = [k for k in self._store if k.startswith(prefix)]
            for k in matched:
                old_bytes, _ = self._store.pop(k)
                self._total_bytes -= len(old_bytes)
            return len(matched)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._total_bytes = 0
