"""The printed boundary of the temporary combat prevention and draw grant."""

from collections.abc import Mapping
from dataclasses import dataclass
import re


FRAGMENT_KIND = "combat_prevention_draw"
CAPABILITY = "damage.prevention.granted_combat_dual_draw"
_GRANT = re.compile(
    r'Until end of turn, target creature gains ["“]'
    r'If this creature would deal combat damage to a player, prevent that damage\. '
    r'This creature[\'’]s controller and that player each draw half that many cards, rounded down\.["”]\.?',
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CombatPreventionDrawSpec:
    schema_version: int = 1
    grant_id: str | None = None

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            raise ValueError("Unsupported combat prevention and draw fragment")
        if (self.schema_version == 1 and self.grant_id is not None
                or self.schema_version == 2 and (type(self.grant_id) is not str or not self.grant_id)):
            raise ValueError("A resolved combat prevention and draw fragment needs its own grant identity")

    def to_dict(self):
        return {"schema_version": self.schema_version,
                **({"grant_id": self.grant_id} if self.schema_version == 2 else {})}

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, Mapping) or set(raw) not in ({"schema_version"}, {"schema_version", "grant_id"}):
            raise ValueError("Combat prevention and draw fragments have a closed schema")
        if (raw.get("schema_version") == 2) != ("grant_id" in raw):
            raise ValueError("Combat prevention and draw fragment version and identity disagree")
        return cls(raw["schema_version"], raw.get("grant_id"))


def matches_combat_prevention_draw_grant(text: str) -> bool:
    return _GRANT.fullmatch(" ".join(text.split())) is not None
