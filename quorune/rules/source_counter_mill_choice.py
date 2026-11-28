"""End-step source count, private scry and a target opponent's committed Mill choice."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..counter_names import normalized_counter_name
from ..errors import GameRuleError
from ..replacement.immutable import FrozenMap, thaw_value
from ..scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec
from .effect_shape_walkers import nested_effects
from .event_optional_draw_shapes import controller_end_step_condition


END_STEP_MILL_MECHANIC = "target-opponent-draw-or-source-counter-mill"
END_STEP_MILL_CAPABILITY = "zone.mill.target_opponent_draw_or_source_counters"
COUNTER_FIELD = "end_step_mill_source_counter"
OFFER_FIELD = "end_step_mill_draw_offer"
COMMIT_FIELD = "_end_step_mill_commit"


def opponent_schema():
    return {"zones": ["player"], "categories": ["player"], "player_relation": "opponent", "count": 1}


@dataclass(frozen=True, slots=True)
class SourceCounterMillChoice:
    counter: str
    scry: int

    def __post_init__(self):
        if (type(self.counter) is not str or not self.counter or normalized_counter_name(self.counter) != self.counter
                or type(self.scry) is not int or not 1 <= self.scry <= 9):
            raise ValueError("The source-counter Mill choice needs an exact counter name and scry count")

    @property
    def amount(self):
        return ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE, counter_name=self.counter, schema_version=2).to_dict()

    def effects(self):
        return (
            {"op": "counter", "card": "$source", "counter": self.counter, "amount": 1, COUNTER_FIELD: 1},
            {"op": "scry", "player": "$controller", "count": self.scry},
            {"op": "offer_draw", "player": "$target.0", "drawer": "$controller", "count": 1, "private": True,
             OFFER_FIELD: {"schema_version": 1, "source_count": self.amount}},
        )

    @property
    def capabilities(self):
        return (END_STEP_MILL_CAPABILITY, "zone.mill.fixed", "zone.mill.committed_public_cards",
                "zone.change.destination_replacement", "zone.draw.library_to_hand",
                "library.scry.fixed_controller", "life.change.effect", "counter.placement.quantity_replacement",
                "quantity_expression.scalar_effect_amount", "trigger.placement.apnap",
                "target.player.static_hexproof_shroud", "target.revalidate_resolution")


def source_counter_mill_node_capabilities(*, effects, target_schema, mechanic_ids):
    if END_STEP_MILL_MECHANIC not in mechanic_ids or target_schema != opponent_schema() or len(effects) != 3:
        return ()
    try:
        spec = SourceCounterMillChoice(effects[0]["counter"], effects[1]["count"])
        if FrozenMap({"effects": effects}) == FrozenMap({"effects": spec.effects()}):
            return spec.capabilities
    except (KeyError, TypeError, ValueError):
        pass
    return ()


def source_counter_mill_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if set(effect) & {COUNTER_FIELD, OFFER_FIELD, COMMIT_FIELD})
    if not marked and END_STEP_MILL_CAPABILITY not in program.capability_dependencies:
        return ()
    required = source_counter_mill_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
    )
    if not (required and len(marked) == 2 and not program.handlers and program.cost_schema is None
            and (program.event, program.active_zone) == ("step.begin", "battlefield")
            and FrozenMap(program.event_condition or {}) == FrozenMap(controller_end_step_condition())):
        raise ValueError("The source-counter Mill choice requires its original targeted end-step ability")
    return required


def is_closed_source_counter_mill_program(program):
    try:
        required = source_counter_mill_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def bind_source_counter_mill(host, item, effect):
    if not any(field in effect for field in (COUNTER_FIELD, OFFER_FIELD, COMMIT_FIELD)):
        return effect
    program = host.semantics.get(item.semantic_key) if item.semantic_key else None
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_source_counter_mill_program(program) or item.kind != "triggered_ability"
            or item.context.get("currently_resolving") is not True or item.context.get("step") != "end_step"
            or item.context.get("player") != item.controller or len(item.targets) != 1
            or item.targets[0] == item.controller or item.targets[0] not in host.state.players):
        raise ValueError("The counter or Mill instruction lost its original end-step trigger")
    if COUNTER_FIELD in effect:
        if FrozenMap(effect) != FrozenMap(program.effects[0]):
            raise ValueError("The source counter instruction changed its exact recipe")
        source = host.state.cards.get(item.source_object_id)
        if (source is None or source.zone != "battlefield" or source.phased_out
                or source.logical_object_id != item.context.get("source_logical_object_id")):
            return None
        return {key: thaw_value(value) for key, value in effect.items() if key != COUNTER_FIELD}
    if OFFER_FIELD in effect:
        if FrozenMap(effect) != FrozenMap(program.effects[2]):
            raise ValueError("The opponent draw choice changed its exact recipe")
        return {"op": "offer_draw", "player": item.targets[0], "drawer": item.controller,
                "count": 1, "private": True, OFFER_FIELD: {"schema_version": 1, "stack": item.ref}}
    expected = declined_mill_effect(item.controller, item.targets[0], item.ref)
    raw = {key: value for key, value in effect.items() if key not in {"count", "_replacement_selections", "_runtime_source"}}
    if FrozenMap(raw) != FrozenMap(expected):
        raise ValueError("The Mill instruction changed its exact origin or opponent")
    from ..scalar_effect_amounts import resolve_scalar_effect_amount

    source_amount = program.effects[2][OFFER_FIELD]["source_count"]
    count = resolve_scalar_effect_amount(host, source_amount, item, use_phased_source_lki=True)
    if "count" in effect and (type(effect["count"]) is not int or effect["count"] != count):
        raise ValueError("Replacement continuation changed the actual source counter count")
    return {**effect, "count": count}


def declined_mill_effect(controller, opponent, stack):
    return {"op": "mill", "player": controller, COMMIT_FIELD: {
        "schema_version": 1, "opponent": opponent, "stack": stack,
    }}


def commit_source_counter_mill(host, effect, *, actor, reason):
    from ..milling import MillRequest, current_public_milled_cards, mill_cards

    marker = effect[COMMIT_FIELD]
    item = next((item for item in host.state.stack if item.ref == marker["stack"]), None)
    if item is None or actor != item.controller:
        raise GameRuleError("Committed Mill requires its original resolving trigger")
    raw = {key: value for key, value in effect.items() if key not in {"count", "_runtime_source"}}
    try:
        bound = bind_source_counter_mill(host, item, raw)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    if bound["count"] != effect["count"]:
        raise GameRuleError("Committed Mill lost its actual source counter count")
    result = mill_cards(host, MillRequest(actor, actor, bound["count"], reason),
                        replacement_selections=tuple(effect.get("_replacement_selections") or ()))
    amount = sum(_milled_card_mana_value(host, card) for card in current_public_milled_cards(host, result))
    if amount:
        host.apply_effect({"op": "lose_life", "player": marker["opponent"], "amount": amount, "reason": reason},
                          actor=actor, as_cost=False)


def _milled_card_mana_value(host, card):
    record = host.card_record(card)
    value = record.mana_value if record is not None else None
    if type(value) not in {int, float} or value < 0 or value != int(value):
        raise GameRuleError("Public Mill result requires the card's supported mana value")
    return int(value)
