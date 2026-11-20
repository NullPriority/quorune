"""A compiled single colored hand-exile Evoke price; no runtime Oracle parsing."""
from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import re

from .additional_cost_vocabulary import EXILE_ONE_FROM_HAND_COST, ZONE_CHANGE_COST_KIND
from .evoke import EVOKE_CAPABILITY_ID, EVOKE_PAYMENT_FIELD, evoke_payment_marker
from .object_predicate import ObjectQuerySpec
from .util import stable_json

HAND_EXILE_EVOKE_CAPABILITY = "casting.evoke.fixed_hand_exile"
HAND_EXILE_EVOKE_HANDLER_ID = "casting.evoke.fixed-hand-exile.v1"
HAND_EXILE_EVOKE_DEPENDENCIES = (HAND_EXILE_EVOKE_CAPABILITY, EVOKE_CAPABILITY_ID,
    "casting.additional_cost.zone_change.fixed_exile")
_COLORS = {"white":"W", "blue":"U", "black":"B", "red":"R", "green":"G"}
_GRAMMAR = re.compile(r"Evoke\s*[—–-]\s*Exile a (?P<color>white|blue|black|red|green) card from your hand\.?", re.I)


@dataclass(frozen=True, slots=True)
class HandExileEvokeSpec:
    ability_id: str
    line_index: int
    color: str
    source_line_hash: str
    schema_version: int = 1

    def __post_init__(self):
        if (type(self.schema_version) is not int or self.schema_version != 1
                or type(self.line_index) is not int or self.line_index < 0
                or self.ability_id != f"ab{self.line_index + 1}"
                or type(self.color) is not str or self.color not in _COLORS.values()
                or type(self.source_line_hash) is not str
                or re.fullmatch(r"[0-9a-f]{64}", self.source_line_hash) is None):
            raise ValueError("Hand-exile Evoke requires a closed source-pinned single-color descriptor")

    def to_dict(self):
        return {"schema_version":self.schema_version,"ability_id":self.ability_id,"line_index":self.line_index,
                "color":self.color,"source_line_hash":self.source_line_hash}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version","ability_id","line_index","color","source_line_hash"}:
            raise ValueError("Hand-exile Evoke descriptor fields are closed")
        return cls(**dict(value))

    @property
    def fingerprint(self):
        return hashlib.sha256(stable_json(self.to_dict()).encode("utf-8")).hexdigest()

    def additional_cost(self):
        query = ObjectQuerySpec(zones=("hand",), owner="$actor", known_to_actor=True, colors_all=(self.color,))
        return {"schema_version":1,"kind":ZONE_CHANGE_COST_KIND,"operation":EXILE_ONE_FROM_HAND_COST,
                "count":1,"choice_field":"exile_cards","predicate":query.to_dict()}

    def cast_cost_options(self):
        word = next(word for word, symbol in _COLORS.items() if symbol == self.color)
        return ({"id":"evoke","kind":"alternate","label":f"Evoke — Exile a {word} card",
            "requirements":{key:0 for key in ("GENERIC","W","U","B","R","G","C")},
            "_additional_option_costs":[self.additional_cost()],"evoke_fingerprint":self.fingerprint,
            EVOKE_PAYMENT_FIELD:evoke_payment_marker(schema_version=2)},)


def compile_hand_exile_evoke(*, material_line, oracle_line, line_index):
    match = _GRAMMAR.fullmatch(" ".join(material_line.strip().split()))
    if match is None:return None
    return HandExileEvokeSpec(f"ab{line_index + 1}", line_index, _COLORS[match["color"].casefold()],
        hashlib.sha256(oracle_line.encode("utf-8")).hexdigest())
