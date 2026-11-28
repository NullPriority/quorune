from __future__ import annotations

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..creature_subtypes import canonical_creature_subtype
from ..effect_contracts import REANIMATE_OPERATION
from ..entry_results import EntryCharacteristicAddition
from ..errors import GameRuleError
from ..replacement.immutable import thaw_value
from ..zone_transitions import ZoneTransitionOwner
from .effect_shape_walkers import nested_effects


FIELD = "reanimation_entry_subtype"
VERSION = 23
CAPABILITY = "zone.reanimate.fixed_entry_subtype"
DEPENDENCIES = (
    CAPABILITY, "zone.reanimate.fixed_target", "target.revalidate_resolution", "zone.change.destination_replacement",
    "trigger.placement.apnap",
)
TARGET = {"zones": ["graveyard"], "categories": ["card"], "types_any": ["creature"], "count": 1}
_UPKEEP = {"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "upkeep"},
]}
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections", "reason"}


def entry_subtype_reanimation_effect(subtype, *, card="$target.0", controller="$controller"):
    if type(subtype) is not str or canonical_creature_subtype(subtype) != subtype:
        raise ValueError("An entering creature requires its canonical added subtype")
    return {
        "op": REANIMATE_OPERATION, "card": card, "controller": controller,
        "tapped": False, "schema_version": VERSION, FIELD: subtype,
    }


def uses_entry_subtype_reanimation(effect):
    return isinstance(effect, Mapping) and effect.get("op") == REANIMATE_OPERATION and (
        FIELD in effect or effect.get("schema_version") == VERSION
    )


def entry_subtype_reanimation_node_capabilities(effects, target_schema):
    if len(effects) != 1 or thaw_value(target_schema) != TARGET:
        return ()
    effect = effects[0]
    try:
        expected = entry_subtype_reanimation_effect(effect.get(FIELD))
    except (TypeError, ValueError):
        return ()
    if thaw_value(effect) != expected or type(effect.get("schema_version")) is not int or effect.get("tapped") is not False:
        return ()
    return DEPENDENCIES


def entry_subtype_reanimation_program_dependencies(program):
    if not any(uses_entry_subtype_reanimation(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = entry_subtype_reanimation_node_capabilities(program.effects, program.target_schema)
    if (not required or program.handlers or program.cost_schema is not None
            or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != _UPKEEP):
        raise ValueError("The subtype return requires a complete targeted upkeep ability")
    return required


def _require_original_source(host, effect, actor):
    try:
        source = ResolutionEffectSource.from_effect(effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = host.state.stack[-1] if host.state.stack else None
    if (item is None or item.ref != source.stack_ref or item.kind != "triggered_ability" or item.controller != actor
            or item.context.get("event") != "step.begin" or item.context.get("step") != "upkeep"
            or item.context.get("player") != actor or item.context.get("currently_resolving") is not True
            or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (effect["card"],)):
        raise GameRuleError("The subtype return lost its original resolving upkeep target")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or CAPABILITY not in program.capability_dependencies or program.effects[0][FIELD] != effect[FIELD]):
        raise GameRuleError("The subtype return requires its original complete program")
    return source


def apply_entry_subtype_reanimation(host, effect, *, actor, reason):
    try:
        expected = entry_subtype_reanimation_effect(effect.get(FIELD), card=effect.get("card"), controller=actor)
        descriptor = {key: value for key, value in effect.items() if key not in _RUNTIME_FIELDS}
        if (thaw_value(descriptor) != expected or type(effect.get("schema_version")) is not int
                or effect.get("tapped") is not False or type(effect.get("card")) is not str
                or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
            raise ValueError("The subtype return has an invalid materialized shape")
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    source = _require_original_source(host, effect, actor)
    card = host._resolve_object(actor, effect["card"], zones={"graveyard"})
    types, _, _ = host._type_parts(host._effective_card_data(card)["type_line"])
    if "creature" not in types or not card.is_card_object:
        raise GameRuleError("The subtype return requires its current physical creature card")
    addition = EntryCharacteristicAddition(source, colors=(), creature_subtypes=(effect[FIELD],), prospective=True)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        [(card.object_id, "battlefield")], reason=reason, destination_controllers={card.object_id: actor},
        putting_players={card.object_id: actor}, entry_characteristic_additions={card.object_id: addition},
        replacement_selections=tuple(effect.get("_replacement_selections", ())),
    )
    return next((entrant for entrant in moved if entrant.zone == "battlefield"), None)
