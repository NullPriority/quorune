from __future__ import annotations

from dataclasses import dataclass

from ..earthbend_model import EarthbendObject
from ..replacement.immutable import FrozenMap
from .context import SemanticSourceContext


@dataclass(frozen=True, slots=True)
class EarthbendIntent:
    actor: str
    target: str
    amount: int
    source: SemanticSourceContext


@dataclass(frozen=True, slots=True)
class FinishEarthbendIntent:
    actor: str
    land: EarthbendObject
    source: SemanticSourceContext


@dataclass(frozen=True, slots=True)
class ReturnEarthbentObjectsIntent:
    actor: str
    objects: tuple[EarthbendObject, ...]
    replacement_selections: tuple[str | FrozenMap, ...] = ()
