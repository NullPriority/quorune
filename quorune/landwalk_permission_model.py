from __future__ import annotations

"""Source-owned permissions for treating Landwalk as absent while blocking."""

from dataclasses import dataclass
from typing import Any, Mapping

from .landwalk_vocabulary import LANDWALK_KEYWORDS

LANDWALK_PERMISSION_CAPABILITY = "combat.block.landwalk.as_though"
LANDWALK_PERMISSION_HANDLER = "ability.static.landwalk-block-permission.v1"


@dataclass(frozen=True, slots=True)
class LandwalkBlockPermission:
    scope: str = "global"
    keyword: str | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported Landwalk block-permission schema")
        if self.scope not in {"global", "attached_blocker"}:
            raise ValueError("Landwalk block permission needs a closed source scope")
        if self.keyword is not None and (
            type(self.keyword) is not str or self.keyword not in LANDWALK_KEYWORDS
        ):
            raise ValueError("Landwalk block permission requires a canonical variant")

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": self.schema_version, "scope": self.scope, "keyword": self.keyword}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LandwalkBlockPermission":
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "scope", "keyword"}:
            raise ValueError("Landwalk block permissions have a closed shape")
        return cls(scope=value["scope"], keyword=value["keyword"], schema_version=value["schema_version"])
