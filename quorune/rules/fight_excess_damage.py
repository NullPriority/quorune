"""Bind a resolving fight result to the original rival's lethal threshold."""

from dataclasses import dataclass

from ..continuous_effect_state import ResolutionEffectSource
from ..deathtouch import deathtouch_damage_result_applies
from ..errors import GameRuleError
from .turn_retained_resolution_mana import FIGHT_EXCESS_RED_FIELD, fight_excess_red_program_dependencies


@dataclass(frozen=True, slots=True)
class FightExcessTarget:
    incarnation: str
    creature_lethal: int
    other_thresholds: tuple[int, ...]


def prepare_fight_excess_target(host, effect, actor, rival):
    source = ResolutionEffectSource.from_effect(effect)
    item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (type(effect.get(FIGHT_EXCESS_RED_FIELD)) is not int or effect[FIGHT_EXCESS_RED_FIELD] != 1
            or rival is None or item is None or item.controller != actor
            or item.context.get("currently_resolving") is not True or program is None
            or not host.semantic_program_is_current_trusted(program)
            or not fight_excess_red_program_dependencies(program)):
        raise GameRuleError("Excess mana requires the original resolving fight spell and its rival")
    types, _, _ = host._type_parts(host._effective_card_data(rival)["type_line"])
    thresholds = tuple(max(0, rival.counters.get(counter, 0)) for type_name, counter in (
        ("planeswalker", "loyalty"), ("battle", "defense"),
    ) if type_name in types)
    return FightExcessTarget(
        rival.logical_object_id,
        max(0, host._numeric_stat(rival.object_id, "toughness") - rival.marked_damage),
        thresholds,
    )


def settle_fight_excess_red(host, events, target, actor):
    hits = tuple(event for event in events
                 if event.target_logical_object_id == target.incarnation and event.dealt_amount > 0)
    if not hits:
        return
    lethal = target.creature_lethal
    if any(deathtouch_damage_result_applies(
        amount=event.dealt_amount, source_keywords=event.source_keywords, target_types=event.target_types,
    ) for event in hits):
        lethal = min(1, lethal)
    excess = sum(event.dealt_amount for event in hits) - min((lethal, *target.other_thresholds))
    if excess > 0:
        host._add_mana_to_pool(actor, {"R": excess})
