"""Thread-safe in-memory store for live game rounds, with expiry.

Rounds are short-lived, so memory is fine for a single-process server
(`flask run`). For multiple workers, swap this for Redis/SQLite behind the
same interface.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable
from typing import Generic, TypeVar

T = TypeVar("T")


class RoundStore(Generic[T]):
    def __init__(self, ttl_seconds: float = 2 * 60 * 60, clock: Callable[[], float] = time.monotonic) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: dict[str, tuple[float, T, tuple[str, ...]]] = {}
        self._aliases: dict[str, str] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._mutex = threading.Lock()

    def put(self, round_id: str, item: T, aliases: Iterable[str] = ()) -> None:
        with self._mutex:
            self._purge_locked()
            alias_tuple = tuple(aliases)
            self._items[round_id] = (self._clock(), item, alias_tuple)
            self._locks[round_id] = threading.Lock()
            for alias in alias_tuple:
                self._aliases[alias] = round_id

    def get(self, round_id: str) -> T | None:
        with self._mutex:
            entry = self._items.get(round_id)
            if entry is None or self._clock() - entry[0] > self._ttl:
                return None
            return entry[1]

    def resolve_alias(self, alias: str) -> tuple[str, T] | None:
        with self._mutex:
            round_id = self._aliases.get(alias)
        if round_id is None:
            return None
        item = self.get(round_id)
        return None if item is None else (round_id, item)

    def lock(self, round_id: str) -> threading.Lock:
        """Per-round lock so concurrent requests can't interleave state changes."""
        with self._mutex:
            return self._locks.setdefault(round_id, threading.Lock())

    def __len__(self) -> int:
        return len(self._items)

    def _purge_locked(self) -> None:
        now = self._clock()
        expired = [rid for rid, (t, _, _) in self._items.items() if now - t > self._ttl]
        for rid in expired:
            _, _, aliases = self._items.pop(rid)
            self._locks.pop(rid, None)
            for alias in aliases:
                self._aliases.pop(alias, None)
