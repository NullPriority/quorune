"""Discover controller Gift triggers from an admitted resolving spell."""

from dataclasses import dataclass

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..gift_card_model import GIFT_CARD_PROMISE_FIELD
from .event_subscriptions import event_has_capability_subscriber


GIFT_GIVEN_EVENT = "gift.given"
GIFT_GIVEN_CAPABILITY = "trigger.event.given_gift"
GIFT_GIVEN_VARIANT = "controller_gives_gift"


def gift_giver_condition():
    return {"field": "player", "op": "eq", "value": "$source.controller"}


@dataclass(frozen=True, slots=True)
class SpellGiftOccurrence:
    """A declared recipient and the actual controller giving a Gift."""

    giver: str
    recipient: str
    stack_ref: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.giver, self.recipient, self.stack_ref)):
            raise ValueError("A resolving Gift requires a giver, declared recipient, and stack object")

    def to_context(self):
        return {"player": self.giver, "gift_recipient": self.recipient, "gift_stack_ref": self.stack_ref}


def dispatch_resolving_spell_gift(host, item):
    """Capture present observers once while leaving placement to normal priority."""
    if GIFT_CARD_PROMISE_FIELD not in item.context or not event_has_capability_subscriber(
        host, GIFT_GIVEN_EVENT, GIFT_GIVEN_CAPABILITY,
    ):
        return
    from .casting.gift_card_costs import resolving_spell_gift_promise

    promise = resolving_spell_gift_promise(host, item)
    if promise.recipient is not None:
        occurrence = SpellGiftOccurrence(item.controller, promise.recipient, item.ref)
        host._dispatch_semantic_event(
            GIFT_GIVEN_EVENT, occurrence.to_context(), sources=host._semantic_event_sources(zones={"battlefield"}),
        )


def gift_observer_runtime_dependencies(program, subscriptions):
    nested = subscriptions is not None and subscriptions.subscription_for(GIFT_GIVEN_EVENT) is not None
    if (program.event != GIFT_GIVEN_EVENT and not nested
            and GIFT_GIVEN_CAPABILITY not in program.capability_dependencies):
        return ()
    if (
        program.event != GIFT_GIVEN_EVENT or program.active_zone != "battlefield"
        or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
        or program.event_condition != gift_giver_condition() or program.trigger_limit is not None
        or program.target_schema is not None or program.handlers or program.cost_schema is not None
        or tuple(program.effects) != ({"op": "draw", "player": "$controller", "count": 1, "private": True},)
        or type(program.effects[0]["count"]) is not int or program.effects[0]["private"] is not True
    ):
        raise ValueError("A Gift observer requires its exact battlefield controller trigger and private draw")
    return (GIFT_GIVEN_CAPABILITY,)
