"""Closed chosen-source, whole-damage redirection from one battlefield activation."""

from collections.abc import Mapping

from ..compiler.direct_target import DirectPermanentTargetSpec
from ..damage_source import REPRESENTED_DAMAGE_SOURCE_ZONES
from ..object_query import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import contains_nested_key


CAPABILITY = "damage.redirection.next_event_from_chosen_source_to_self"
MECHANIC = "fixed-chosen-source-next-event-damage-redirection"
TEMPLATE = "fixed-chosen-source-next-event-damage-redirection-v1"
VERSION_FIELD = "chosen_source_redirection_version"
_PROMPT = "Choose the source whose damage will be redirected."


def chosen_redirection_source_predicate():
    return ObjectQuerySpec(zones=REPRESENTED_DAMAGE_SOURCE_ZONES, known_to_actor=True).to_dict()


def _redirection():
    return {
        "op": "create_damage_redirection",
        VERSION_FIELD: 1,
        "source": "$source",
        "subject": "$target.0",
        "destination": "$source",
        "duration": "until_end_of_turn",
    }


def chosen_damage_redirection_instruction():
    return {
        "op": "choose_damage_source",
        "prompt": _PROMPT,
        "source_predicate": chosen_redirection_source_predicate(),
        "redirection": _redirection(),
    }


def chosen_damage_redirection_target():
    return DirectPermanentTargetSpec(types_any=("creature",)).to_target_schema()


def _has_fixed_mana_price(cost):
    if not isinstance(cost, Mapping):
        return False
    expected = {
        "text": "{1}{W}",
        "mana": {"B": 0, "C": 0, "G": 0, "GENERIC": 1, "R": 0, "U": 0, "W": 1},
        "complex_symbols": [], "tap_source": False, "untap_source": False,
        "discard_source": False, "sacrifice_source": False, "exile_source": False,
        "life_payment": 0, "energy_payment": 0, "loyalty_delta": None,
        "source_counter_removal_cost": None, "choices": [], "uncompiled_costs": [],
    }
    return (
        thaw_value(cost) == expected
        and all(type(value) is int for value in cost["mana"].values())
        and all(type(cost[field]) is type(expected[field]) for field in (
            "tap_source", "untap_source", "discard_source", "sacrifice_source", "exile_source",
            "life_payment", "energy_payment",
        ))
    )


def chosen_damage_redirection_capabilities(*, effects, target_schema, mechanics, active_zone, cost_schema):
    if (
        MECHANIC not in mechanics or active_zone != "battlefield"
        or not _has_fixed_mana_price(cost_schema)
        or thaw_value(target_schema) != chosen_damage_redirection_target()
        or type(target_schema["count"]) is not int or len(effects) != 1
        or thaw_value(effects[0]) != chosen_damage_redirection_instruction()
        or type(effects[0]["redirection"][VERSION_FIELD]) is not int
    ):
        return ()
    return (CAPABILITY, "target.revalidate_resolution")


def uses_chosen_damage_redirection(effects):
    return contains_nested_key(effects, VERSION_FIELD)


def _catalog_matches(program):
    if not program.handlers:
        return True
    if len(program.handlers) != 1:
        return False
    from ..compiler.activated_costs import activated_ability_cost
    from ..semantic_runtime.activated_abilities import ActivatedAbilityCatalogHandler
    from ..semantic_runtime.context import SemanticNodeError

    try:
        ability = ActivatedAbilityCatalogHandler().validate(program.handlers[0])
    except SemanticNodeError:
        return False
    return (
        ability.builtin_semantic_key == program.key
        and not ability.mana_ability and ability.zones == ("battlefield",)
        and thaw_value(ability.target_schema) == program.target_schema
        and activated_ability_cost(ability) == program.cost_schema
    )


def chosen_damage_redirection_runtime_dependencies(program):
    if (MECHANIC not in program.coverage and CAPABILITY not in program.capability_dependencies
            and not uses_chosen_damage_redirection((*program.effects, *program.handlers))):
        return ()
    required = chosen_damage_redirection_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
        active_zone=program.active_zone, cost_schema=program.cost_schema,
    )
    if (
        not required or program.event != "activate" or not program.ability_id.startswith("ability:")
        or program.event_condition is not None or not _catalog_matches(program)
    ):
        raise ValueError("Chosen-source redirection requires its complete paid target activation")
    return required


def is_closed_chosen_damage_redirection(program):
    try:
        required = chosen_damage_redirection_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


def validate_chosen_redirection_result(effect, *, selected=False):
    fields = set(_redirection())
    if selected:
        fields.update(("chosen_source", "source_predicate"))
    if (
        not isinstance(effect, Mapping)
        or set(effect) - {"_runtime_source", "_replacement_selections", "reason"} != fields
        or effect.get("op") != "create_damage_redirection"
        or type(effect.get(VERSION_FIELD)) is not int or effect[VERSION_FIELD] != 1
        or effect.get("duration") != "until_end_of_turn"
    ):
        raise ValueError("A chosen source requires a complete whole-damage redirection")
    if selected and (
        type(effect.get("chosen_source")) is not str or not effect["chosen_source"]
        or thaw_value(effect.get("source_predicate")) != chosen_redirection_source_predicate()
    ):
        raise ValueError("A whole-damage redirection requires its selected source and exact predicate")


def chosen_damage_redirection_subjects(host, effect, *, actor):
    """Bind both permanent roles to the resolving activation and its original target."""
    from .damage_snapshots import recipient_snapshot
    from ..damage_modifier_state import DamageSubject
    from ..errors import GameRuleError
    from ..selection.target_query import target_identity_matches_snapshot

    validate_chosen_redirection_result(effect, selected=True)
    item = next((frame for frame in reversed(host.state.stack)
                 if frame.context.get("currently_resolving") is True), None)
    if (
        item is None or item.kind != "activated_ability" or item.controller != actor
        or item.context.get("targets_revalidated") is not True or list(item.targets) != [effect["subject"]]
        or effect["source"] != effect["destination"]
    ):
        raise GameRuleError("Chosen-source redirection requires its own original source and revalidated target")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not chosen_damage_redirection_runtime_dependencies(program)):
        raise GameRuleError("Chosen-source redirection requires its own trusted activation")
    destination = host.state.cards.get(item.source_object_id)
    original_source = item.context.get("source_logical_object_id")
    if type(original_source) is not str or not original_source:
        raise GameRuleError("Chosen-source redirection requires the activation's original incarnation")
    if destination is None or destination.zone != "battlefield" or destination.logical_object_id != original_source:
        return None
    if destination.ref != effect["destination"]:
        raise GameRuleError("The original redirection destination changed")
    snapshot = item.context.get("target_snapshots", {}).get(effect["subject"])
    if not isinstance(snapshot, Mapping) or not target_identity_matches_snapshot(host, effect["subject"], snapshot):
        return None
    subject = recipient_snapshot(host, effect["subject"], actor=actor)
    if "creature" not in subject.types:
        return None
    return (
        DamageSubject(subject.ref, subject.kind, subject.controller, subject.object_id, subject.logical_object_id, subject.owner),
        DamageSubject(destination.ref, "permanent", destination.controller, destination.object_id,
                      destination.logical_object_id, destination.owner),
    )
