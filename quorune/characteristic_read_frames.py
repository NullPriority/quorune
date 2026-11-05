from __future__ import annotations

"""Scoped characteristic reads and detached source views."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any
from collections.abc import Mapping
from functools import wraps


@dataclass(frozen=True,slots=True)
class CharacteristicStateView:
    original: Any
    cards: Mapping
    players: Mapping

    def __getattr__(self,name):return getattr(self.original,name)


@dataclass(frozen=True,slots=True)
class CharacteristicReadHost:
    original: Any
    state: Any

    def __getattr__(self,name):
        value=getattr(self.original,name)
        if getattr(value,'__self__',None) is self.original and hasattr(value,'__func__'):
            return value.__func__.__get__(self,type(self))
        return value


@dataclass(slots=True)
class _CharacteristicReadFrame:
    host: Any
    values: dict = field(default_factory=dict)
    active: set = field(default_factory=set)
    identities: dict = field(default_factory=dict)


_FRAME = ContextVar('quorune_characteristic_read_frame', default=None)


@contextmanager
def characteristic_read_frame(host):
    current = _FRAME.get()
    if current is not None and current.host is host:
        yield current
        return
    token = _FRAME.set(_CharacteristicReadFrame(host))
    try:
        yield _FRAME.get()
    finally:
        _FRAME.reset(token)


def memoized_characteristic_read(host, key, read, *, identity=None):
    with characteristic_read_frame(host) as frame:
        if identity is not None:
            frame.identities[id(identity)] = identity
        if key in frame.values:
            return deepcopy(frame.values[key])
        # An in-progress read retains its existing recursion/error behavior;
        # no provisional value becomes authority for another query.
        if key in frame.active:
            return read()
        frame.active.add(key)
        try:
            value = read()
            frame.values[key] = deepcopy(value)
            return value
        finally:
            frame.active.remove(key)


def characteristic_read_method(read):
    @wraps(read)
    def scoped(host, *args, **kwargs):
        with characteristic_read_frame(host):
            return read(host, *args, **kwargs)
    return scoped
