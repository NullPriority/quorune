from __future__ import annotations

"""Invocation-scoped reuse of read-only cohort compiler inputs and outputs."""

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from typing import Any, Iterable

from quorune.carddb import CardRecord
from quorune.oracle_ir import compile_oracle_card as _compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry
from quorune.rules.evidence import _capability_validation_snapshot
from quorune.util import stable_json


_COMPILATIONS: ContextVar[dict[tuple[Any, ...], Any] | None] = ContextVar(
    "cohort_compilations", default=None)


@contextmanager
def cohort_compilation_snapshot():
    """Discard reused compiler results after one synchronous measurement build."""
    if _COMPILATIONS.get() is not None:
        yield
        return
    token = _COMPILATIONS.set({})
    try:
        with _capability_validation_snapshot():
            yield
    finally:
        _COMPILATIONS.reset(token)


def compile_cohort_card(
    record: CardRecord, *, trusted_mechanics: Iterable[str] = (),
    capability_registry: CapabilityRegistry | None = None,
    capability_profile: str = "traditional",
):
    """Reuse identical full-record IR; independent programs still bind normally."""
    cache = _COMPILATIONS.get()
    arguments = dict(trusted_mechanics=trusted_mechanics,
                     capability_registry=capability_registry,
                     capability_profile=capability_profile)
    if (cache is None or type(record) is not CardRecord
            or type(capability_registry) is not CapabilityRegistry):
        return _compile_oracle_card(record, **arguments)
    arguments["trusted_mechanics"] = tuple(trusted_mechanics)
    try:
        source = stable_json(asdict(record))
    except (TypeError, ValueError):
        return _compile_oracle_card(record, **arguments)
    mechanics = tuple(str(value).casefold() for value in arguments["trusted_mechanics"])
    key = (source, mechanics,
           id(capability_registry), capability_registry.fingerprint,
           capability_registry.evidence_fingerprint, capability_profile,
           _compile_oracle_card)
    if key in cache:
        return deepcopy(cache[key])
    result = _compile_oracle_card(record, **arguments)
    cache[key] = deepcopy(result)
    return result
