"""Closed battlefield observer for spells actually countered by a controlled source."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from .optional_draw_discard import optional_draw_discard_is_closed

CONTROLLED_SPELL_COUNTER_EVENT = "spell.countered_by_effect"
CONTROLLED_SPELL_COUNTER_CAPABILITY = "trigger.event.controlled_spell_counter"
_SPELL_KINDS = frozenset({"spell", "spell_copy"})
_COUNTER_SOURCE_KINDS = _SPELL_KINDS | {"activated_ability", "triggered_ability"}


@dataclass(frozen=True, slots=True)
class SuccessfulSpellCounter:
    """Public facts captured from live stack items before the counter commits."""

    spell_ref: str
    counter_source_ref: str
    counter_source_controller: str

    def event_context(self) -> dict[str, str]:
        return {
            "stack": self.spell_ref,
            "counter_source": self.counter_source_ref,
            "counter_source_controller": self.counter_source_controller,
        }


def prepare_successful_spell_counter(stack, countered, *, countered_by):
    """Use the resolving source, never the countered object's controller."""

    if countered.kind not in _SPELL_KINDS:
        return None
    resolving = tuple(item for item in stack if item.context.get("currently_resolving") is True)
    if len(resolving) != 1:
        return None
    source, = resolving
    if source.kind not in _COUNTER_SOURCE_KINDS or source.controller != countered_by:
        return None
    return SuccessfulSpellCounter(countered.ref, source.ref, source.controller)


def controlled_spell_counter_condition() -> dict[str, str]:
    return {"field": "counter_source_controller", "op": "eq", "value": "$source.controller"}


def _one_card_optional_loot_is_closed(effects):
    if len(effects) != 1:
        return False
    wrapper = effects[0]
    if (not isinstance(wrapper, Mapping) or set(wrapper) != {"op", "player", "effects"}
            or wrapper["op"] != "offer_optional_effect"
            or wrapper["player"] != "$controller"):
        return False
    children = wrapper["effects"]
    return (optional_draw_discard_is_closed(children, player="$controller")
            and children[0]["count"] == 1 and children[1]["count"] == 1)


def controlled_spell_counter_runtime_dependencies(program, subscriptions):
    """Keep the represented condition and its exact optional loot indivisible."""

    event = program.event.removesuffix(".self")
    if event != CONTROLLED_SPELL_COUNTER_EVENT:
        if subscriptions is not None and any(
            child.event.removesuffix(".self") == CONTROLLED_SPELL_COUNTER_EVENT
            for child in subscriptions.subscriptions
        ):
            raise ValueError("A controlled spell counter requires its standalone trigger")
        return ()
    if (program.event != event or program.active_zone != "battlefield"
            or program.event_condition != controlled_spell_counter_condition()
            or program.cost_schema is not None or program.target_schema is not None or program.handlers
            or program.trigger_limit is not None or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage
            or {"intervening_condition", "one_or_more_event_batch"}.intersection(program.coverage)
            or not _one_card_optional_loot_is_closed(program.effects)):
        raise ValueError("A controlled spell counter requires the original optional draw and discard trigger")
    return (CONTROLLED_SPELL_COUNTER_CAPABILITY,)
