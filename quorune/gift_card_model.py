"""A versioned cast declaration for the bounded spell Gift a card production."""

from collections.abc import Mapping
from dataclasses import dataclass

GIFT_CARD_COST_FIELD = "gift_a_card"
GIFT_EXTRA_TURN_COST_FIELD = "gift_an_extra_turn"
GIFT_CAST_COST_FIELDS = frozenset({GIFT_CARD_COST_FIELD, GIFT_EXTRA_TURN_COST_FIELD})
GIFT_CARD_PROMISE_FIELD = "gift_card_promise"
GIFT_CARD_TEMPLATE = "gift-card-controller-protection-v1"
GIFT_CARD_CAPABILITY = "casting.additional_cost.gift_card_spell"
GIFT_CARD_PRICE = {"schema_version": 1}


@dataclass(frozen=True, slots=True)
class GiftCardPromise:
    recipient: str | None
    schema_version: int = 1

    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise ValueError("Unsupported gift promise version")
        if self.recipient is not None and (type(self.recipient) is not str or not self.recipient):
            raise ValueError("A promised gift requires one player")

    def to_dict(self):
        return {"schema_version": self.schema_version, "recipient": self.recipient}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"schema_version", "recipient"}:
            raise ValueError("Gift promise has a closed schema")
        return cls(value["recipient"], value["schema_version"])



def bind_gift_promise_target_schema(item, *, unpromised, promised):
    """Rebind the original declared branch for an actual spell and its copies."""
    if item.kind not in {"spell", "spell_copy"}:
        raise ValueError("A Gift target branch belongs to a spell or its copy")
    promise = GiftCardPromise.from_dict(item.context.get(GIFT_CARD_PROMISE_FIELD))
    bound = promised if promise.recipient is not None else unpromised
    if "target_schema_override" in item.context and dict(item.context["target_schema_override"]) != bound:
        raise ValueError("The persisted target branch differs from the original Gift declaration")
    return bound
