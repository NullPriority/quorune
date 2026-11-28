"""Airbend a current creature set and bind each arriving card's exile price."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_model import ContinuousObjectIdentity
from ..continuous_effect_state import ResolutionEffectSource, matching_battlefield_objects
from ..errors import GameRuleError
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from ..target_cardinality import ANY_NUMBER_TARGET_CAPABILITY, AnnouncedTargetCardinality
from ..temporary_cast_rule_model import MECHANIC as CAST_RULE_MECHANIC, opponents_nonhand_until_next_turn_effect
from ..util import mana_cost_to_vector
from .effect_shape_walkers import nested_effects


FIELD = "airbend_other_creatures"
TARGETED_FIELD = "airbend_selected_permanents"
CAPABILITY = "keyword_action.airbend_creature_set"
TARGETED_CAPABILITY = "keyword_action.airbend_targeted_permanent_set"
TARGETED_TEMPLATE = "airbend-targeted-controlled-nonlands-entry-v1"
CAST_CAPABILITY = "casting.lifecycle.airbend_exile_incarnation"
PERMISSION_FIELD = "airbend_exile_permission"
AIRBEND_EVENT = "player.airbends"
TEMPLATE = "airbend-except-optional-creature-next-turn-cast-rule-v1"
_EXCEPTED = "airbend_excepted_incarnation"
_CREATURES = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))


@dataclass(frozen=True, slots=True)
class AirbendCastPermission:
    owner: str
    logical_object_id: str

    def __post_init__(self):
        if any(type(value) is not str or not value for value in (self.owner, self.logical_object_id)):
            raise ValueError("Airbend cast permission requires an owner and exact exile incarnation")

    def to_dict(self):
        return {"schema_version": 1, "owner": self.owner, "logical_object_id": self.logical_object_id}

    @classmethod
    def from_dict(cls, value):
        if (not isinstance(value, Mapping) or set(value) != {"schema_version", "owner", "logical_object_id"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError("Airbend exile permission has a closed identity record")
        return cls(value["owner"], value["logical_object_id"])


def current_airbend_cast_permission(card, actor):
    if card.zone != "exile" or card.owner != actor or card.face_down or not card.is_card_object:
        return None
    try:
        permission = AirbendCastPermission.from_dict(card.annotations.get(PERMISSION_FIELD))
    except (TypeError, ValueError):
        return None
    return permission if (permission.owner, permission.logical_object_id) == (actor, card.logical_object_id) else None


def airbend_cast_option(card, actor):
    if current_airbend_cast_permission(card, actor) is None:
        return None
    requirements, _ = mana_cost_to_vector("{2}")
    return {"id": "airbend", "kind": "alternate", "label": "Airbend {2}", "requirements": requirements,
            "source_zone": "exile", "x_value_policy": "zero"}


def airbend_optional_creature_schema():
    return {"groups": [{"id": "target", "zones": ["battlefield"], "categories": ["permanent"],
                        "types_any": ["creature"], "min": 0, "max": 1}]}


def airbend_excluding_optional_creature_effect():
    return {"op": "exile_all", FIELD: 1}


def airbend_targeted_nonland_schema():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_none": ["land"],
        "controller_relation": "you", "source_exclusion": True,
        "count_parameter": AnnouncedTargetCardinality(selection="up_to", parameter="any_number", schema_version=2).to_dict(),
    }


def airbend_targeted_nonland_effect():
    return {"op": "exile_all", TARGETED_FIELD: 1, "cards": "$targets"}


def uses_airbend_creature_set(effect):
    return isinstance(effect, Mapping) and (FIELD in effect or TARGETED_FIELD in effect)


def airbend_effect_payload_dependencies(effect):
    if not uses_airbend_creature_set(effect):
        return ()
    targeted = TARGETED_FIELD in effect
    field = TARGETED_FIELD if targeted else FIELD
    expected = airbend_targeted_nonland_effect() if targeted else airbend_excluding_optional_creature_effect()
    if type(effect[field]) is not int or FrozenMap(effect) != FrozenMap(expected):
        raise ValueError("Airbend requires an exact closed set instruction")
    capability = TARGETED_CAPABILITY if targeted else CAPABILITY
    return capability, CAST_CAPABILITY, "zone.move.fixed_public_set", "zone.change.destination_replacement"


def _targeted_airbend_dependencies(effect, target_schema, cost_schema):
    from .permanent_predicate_capability_shapes import direct_target_predicate_capabilities

    if (cost_schema is not None or not isinstance(target_schema, Mapping)
            or FrozenMap(target_schema) != FrozenMap(airbend_targeted_nonland_schema())
            or target_schema["source_exclusion"] is not True):
        return ()
    try:
        AnnouncedTargetCardinality.from_dict(target_schema["count_parameter"])
        required = airbend_effect_payload_dependencies(effect)
        singular = {key: value for key, value in target_schema.items() if key != "count_parameter"}
        singular["count"] = 1
        predicates = direct_target_predicate_capabilities(singular)
    except (TypeError, ValueError, KeyError):
        return ()
    return (*required, ANY_NUMBER_TARGET_CAPABILITY, "target.revalidate_resolution", *predicates)


def airbend_node_capabilities(effects, target_schema, cost_schema):
    if not any(uses_airbend_creature_set(effect) for effect in effects):
        return None
    if any(TARGETED_FIELD in effect for effect in effects):
        return _targeted_airbend_dependencies(effects[0], target_schema, cost_schema) if len(effects) == 1 else ()
    if (len(effects) != 2 or cost_schema is not None
            or FrozenMap(target_schema or {}) != FrozenMap(airbend_optional_creature_schema())
            or any(type(target_schema["groups"][0][key]) is not int for key in ("min", "max"))
            or not uses_airbend_creature_set(effects[0])
            or FrozenMap(effects[1]) != FrozenMap(opponents_nonhand_until_next_turn_effect())):
        return ()
    from .temporary_cast_restrictions import temporary_cast_node_capabilities

    try:
        body = airbend_effect_payload_dependencies(effects[0])
        restriction = temporary_cast_node_capabilities(effects=(effects[1],), target_schema=None, mechanic_ids=(CAST_RULE_MECHANIC,))
    except (TypeError, ValueError, KeyError):
        return ()
    return (*body, "target.revalidate_resolution", *restriction) if restriction else ()


def airbend_creature_set_program_dependencies(program):
    if not any(uses_airbend_creature_set(effect) for effect in nested_effects((program.effects, program.handlers))):
        return ()
    required = airbend_node_capabilities(program.effects, program.target_schema, program.cost_schema)
    expected = ("permanent.enter.self", "battlefield") if TARGETED_CAPABILITY in required else ("resolve", "stack")
    if (not required or (program.event, program.active_zone) != expected
            or program.event_condition is not None or program.handlers):
        raise ValueError("Airbend requires the exact resolution kind and announced target shape")
    return required


def is_closed_airbend_creature_set_program(program):
    try:
        required = airbend_creature_set_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


def _excluded_incarnation(host, item, actor):
    if _EXCEPTED not in item.context:
        card = host._resolve_object(actor, item.targets[0], zones={"battlefield"}) if item.targets else None
        item.context[_EXCEPTED] = (ContinuousObjectIdentity(card.object_id, card.logical_object_id).to_dict()
                                   if card is not None else None)
    value = item.context[_EXCEPTED]
    if bool(item.targets) != (value is not None):
        raise ValueError("Airbend's captured exclusion disagrees with the original target choice")
    if value is None:
        return None
    identity = ContinuousObjectIdentity.from_dict(value)
    card = host.state.cards.get(identity.object_id)
    if card is not None and card.ref != item.targets[0]:
        raise ValueError("Airbend's captured exclusion changed its original object")
    return identity


def _targeted_airbend_cards(host, item, body, *, actor):
    if item.kind != "triggered_ability" or item.context.get("targets_revalidated") is not True:
        raise ValueError("Selected Airbend requires its resolving trigger and revalidated targets")
    refs = tuple(target for target in item.targets if target is not None)
    if not isinstance(body.get("cards"), (tuple, list)) or tuple(body["cards"]) != refs:
        raise ValueError("Selected Airbend requires exactly its still-legal chosen objects")
    return tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)


def _airbend_resolution_cards(host, effect, *, actor):
    body = {key: value for key, value in effect.items()
            if key not in {"reason", "_runtime_source", "_replacement_selections"}}
    targeted = TARGETED_FIELD in body
    required = airbend_effect_payload_dependencies({**body, "cards": "$targets"} if targeted else body)
    source = ResolutionEffectSource.from_effect(effect)
    item = host.state.stack[-1] if host.state.stack else None
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.ref != source.stack_ref or item.controller != actor
            or item.context.get("currently_resolving") is not True or program is None
            or not host.semantic_program_is_current_trusted(program)
            or not is_closed_airbend_creature_set_program(program)
            or not set(required) <= set(airbend_creature_set_program_dependencies(program))):
        raise ValueError("Airbend lost its original trusted resolving stack frame")
    if targeted:
        return _targeted_airbend_cards(host, item, body, actor=actor)
    if item.kind not in {"spell", "spell_copy"} or len(item.targets) > 1:
        raise ValueError("Creature-set Airbend requires its resolving spell and one optional target")
    excluded = _excluded_incarnation(host, item, actor)
    return tuple(card for card in matching_battlefield_objects(host, _CREATURES)
                 if ContinuousObjectIdentity(card.object_id, card.logical_object_id) != excluded)


def apply_airbend_creature_set(host, effect, *, actor, reason):
    from ..zone_object_state import mark_airbend_exile_permission
    from ..zone_transitions import ZoneTransitionOwner

    try:
        cards = _airbend_resolution_cards(host, effect, actor=actor)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    changes = [(card.object_id, "exile") for card in cards]
    if not changes:
        return ()
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        changes, reason=reason, log=True, replacement_selections=tuple(effect.get("_replacement_selections", ())),
    )
    exiled = tuple(card for card in moved if card.zone == "exile")
    for card in exiled:
        if card.is_card_object and not card.face_down:
            mark_airbend_exile_permission(card)
    if exiled:
        refs = tuple(card.ref for card in exiled)
        host._log(actor, AIRBEND_EVENT, f"{actor} airbent {len(refs)} object(s).",
                  {"player": actor, "objects": list(refs), "count": len(refs)}, changed_objects=[card.object_id for card in exiled])
        host._dispatch_semantic_event(AIRBEND_EVENT, {"player": actor, "objects": list(refs), "count": len(refs)})
        return refs
    return ()
