"""Settle an original source's linked exiled creatures and remaining cards."""
from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..errors import GameRuleError
from ..linked_exile_return_model import ExiledObjectIdentity, LinkedExileReturnSpec, validate_linked_ability_binding
from ..replacement.immutable import FrozenMap, thaw_value

FIELD = "linked_creature_settlement"
VERSION = 17
CAPABILITY = "zone.move.linked_exile_creature_and_remainder"
MECHANIC = "linked-exile-return"
DEPENDENCIES = (CAPABILITY, "zone.exile.linked_ability_pair", "zone.linked_exile_return.fixed",
                "zone.change.destination_replacement", "activation.source_zone_change.fixed")
CHOICE_OFFERED = "_linked_creature_settlement_offered"


def owned_target_schema():
    from ..compiler.direct_target import DirectPermanentTargetSpec

    return {**DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema(), "owner_relation": "you"}


def instruction(pair, phase, selected=None):
    validate_linked_ability_binding(pair)
    if phase not in {"exile", "choose", "enter", "return_others"}:
        raise ValueError("Linked creature settlement phase is unsupported")
    effect = {"op": "choose_objects" if phase == "choose" else "linked_exile_return",
              "schema_version": VERSION, FIELD: phase, "binding_id": pair}
    if phase == "exile":
        effect["card"] = "$target.0"
    if phase in {"enter", "return_others"}:
        if selected is None and phase == "enter" or selected is not None and not isinstance(selected, ExiledObjectIdentity):
            raise ValueError("Linked creature settlement lost the selected exile")
        effect["selected"] = selected.to_dict() if selected is not None else None
    return effect


def activation_capabilities(*, effects, target_schema, active_zone, cost_schema):
    from .linked_exile_return_shapes import _FADE_COST

    if len(effects) != 1 or active_zone != "battlefield" or not isinstance(cost_schema, Mapping):
        return ()
    operation = effects[0]
    phase = operation.get(FIELD)
    if phase not in {"exile", "choose"} or type(operation.get("schema_version")) is not int:
        return ()
    if FrozenMap(operation) != FrozenMap(instruction(operation.get("binding_id"), phase)):
        return ()
    expected = {**_FADE_COST, "source_counter_removal_cost": None, "tap_source": True,
                "sacrifice_source": phase == "choose"}
    expected["mana"] = {**_FADE_COST["mana"], "GENERIC": 1 if phase == "exile" else 2}
    cost = {key: thaw_value(value) for key, value in cost_schema.items() if key != "text"}
    if type(cost_schema.get("text")) is not str or not cost_schema["text"] or cost != expected:
        return ()
    if any(type(amount) is not int for amount in cost_schema["mana"].values()):
        return ()
    if phase == "exile":
        if not isinstance(target_schema, Mapping) or FrozenMap(target_schema) != FrozenMap(owned_target_schema()):
            return ()
        return (*DEPENDENCIES, "permanent.exile.effect", "target.revalidate_resolution")
    return DEPENDENCIES if target_schema is None else ()


def uses_instruction(program):
    from .effect_shape_walkers import nested_effects

    return (CAPABILITY in program.capability_dependencies
            or any(FIELD in effect for effect in nested_effects((program.effects, program.handlers, program.target_schema))))


def program_dependencies(program):
    from .linked_loyalty_exile_shapes import has_plain_loyalty_catalog

    if not uses_instruction(program):
        return ()
    required = activation_capabilities(effects=program.effects, target_schema=program.target_schema,
                                       active_zone=program.active_zone, cost_schema=program.cost_schema)
    if not required or not has_plain_loyalty_catalog(program):
        raise ValueError("Linked creature settlement requires its exact paid activation")
    return required


def _source(host, item, actor, pair, phase):
    if (item is None or item.kind not in {"activated_ability", "activated_ability_copy"} or item.controller != actor
            or item.context.get("currently_resolving") is not True):
        raise GameRuleError("Linked creature settlement requires its resolving activation")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or tuple(program.effects) != (instruction(pair, phase),)):
        raise GameRuleError("Linked creature settlement lost its original ability pair")
    try:
        return ContinuousObjectIdentity(item.source_object_id, item.context.get("source_logical_object_id"))
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc


def _linked(host, item, actor, pair):
    from ..linked_ability_exile_state import current_linked_ability_exile

    source = _source(host, item, actor, pair, "choose")
    return current_linked_ability_exile(host, source=source, ability_pair=pair)


def _can_enter(host, identity):
    card = host.state.cards[identity.object_id]
    return not card.face_down and "creature" in host._type_parts(
        str(host._effective_card_data(card).get("type_line") or ""),
    )[0]


def choice_refs(host, item, actor, effect):
    pair = effect.get("binding_id")
    literal = {key: value for key, value in effect.items() if key != CHOICE_OFFERED}
    if FrozenMap(literal) != FrozenMap(instruction(pair, "choose")):
        raise GameRuleError("Linked creature settlement has a malformed choice")
    return tuple(row.ref for row in _linked(host, item, actor, pair) if _can_enter(host, row))


_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections"}


def resolved_instruction(effect):
    phase, pair = effect.get(FIELD), effect.get("binding_id")
    literal = {key: value for key, value in effect.items() if key not in _RUNTIME_FIELDS}
    if type(effect.get("schema_version")) is not int or phase not in {"exile", "enter", "return_others"}:
        raise ValueError("Linked creature settlement requires a versioned resolved phase")
    selected = ExiledObjectIdentity.from_dict(effect["selected"]) if effect.get("selected") is not None else None
    expected = instruction(pair, phase, selected)
    if phase == "exile":
        ref = effect.get("card")
        if type(ref) is not str or not ref or ref.startswith("$"):
            raise ValueError("Linked creature exile requires its original target reference")
        expected["card"] = ref
    if FrozenMap(literal) != FrozenMap(expected):
        raise ValueError("Linked creature settlement lost its exact resolved instruction")
    return phase, pair, selected


def apply_linked_creature_settlement(host, item, effect, *, actor, reason):
    from ..linked_ability_exile_state import exile_for_linked_ability
    from ..linked_exile_return import return_linked_exiled_objects
    from ..zone_transitions import ZoneTransitionOwner

    try:
        phase, pair, selected = resolved_instruction(effect)
    except (TypeError, ValueError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    selections = tuple(effect.get("_replacement_selections", ()))
    if phase == "exile":
        source = _source(host, item, actor, pair, "exile")
        if item.context.get("targets_revalidated") is not True or tuple(item.targets) != (effect["card"],):
            raise GameRuleError("Linked creature exile lost its revalidated target")
        card = host._resolve_object(actor, effect["card"], zones={"battlefield"}, owned_only=True)
        if card.phased_out or "creature" not in host._effective_type_parts(card)[0]:
            raise GameRuleError("Linked creature exile requires its owned creature")
        return list(exile_for_linked_ability(host, card=card, source=source, ability_pair=pair,
                                            reason=reason, replacement_selections=selections))
    current = _linked(host, item, actor, pair)
    if phase == "enter":
        if selected not in current or not _can_enter(host, selected):
            return []
        return return_linked_exiled_objects(host, {
            "op": "return_linked_exiled_objects", "objects": [selected.to_dict()],
            "spec": LinkedExileReturnSpec(controller="actor").to_dict(),
            **{key: effect[key] for key in _RUNTIME_FIELDS if key in effect},
        }, actor=actor, operation="return_linked_exiled_objects", reason=reason)
    remaining = tuple((value.object_id, "hand") for value in current if value != selected)
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        remaining, reason=reason, replacement_selections=selections,
    ) if remaining or selections else ()
    return [card.ref for card in moved if card.zone == "hand"]
