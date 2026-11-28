from __future__ import annotations

"""Bind one graveyard-exile occurrence to its separate source color reader."""

from collections.abc import Mapping

from ..continuous_effect_model import ContinuousObjectIdentity
from ..linked_exile_return_model import validate_linked_ability_binding
from ..replacement.immutable import FrozenMap, thaw_value


CAPABILITY = "mana.activated.linked_graveyard_colors"
MANA_HANDLER = "ability.activated.mana.linked-graveyard-colors.v1"
PAIR_FIELD = "linked_graveyard_mana_pair"
SOURCE_FIELD = "linked_graveyard_mana_source"
COLOR_RELATION = "linked_graveyard_exile"
ENTRY = "when this land enters, exile up to three target cards from graveyards."
OUTPUT = "add one mana of any of the exiled cards' colors."
PRODUCER_REQUIREMENTS = (
    CAPABILITY, "card.exile.public_graveyard", "resolution.effect.fixed_homogeneous_target_set",
    "target.revalidate_resolution", "trigger.placement.apnap", "trigger.event.normalized_zone_change",
    "zone.exile.linked_ability_pair", "zone.change.destination_replacement",
)
READER_REQUIREMENTS = (CAPABILITY, "mana.activated.color_set", "zone.exile.linked_ability_pair")


def graveyard_target_schema():
    return {"zones": ["graveyard"], "categories": ["card"], "owner_relation": "any", "up_to": 3}


def graveyard_exile_instruction():
    return {"op": "exile_public_graveyard_targets", "cards": "$targets", "maximum_targets": 3}


def linked_graveyard_exile_instruction(pair):
    validate_linked_ability_binding(pair)
    return {**graveyard_exile_instruction(), PAIR_FIELD: pair, SOURCE_FIELD: "$context." + SOURCE_FIELD}


def _producer_pair(effect):
    if (not isinstance(effect, Mapping) or type(effect.get("maximum_targets")) is not int
            or FrozenMap(effect) != FrozenMap(linked_graveyard_exile_instruction(effect.get(PAIR_FIELD)))):
        raise ValueError("Linked graveyard mana requires its complete optional target exile")
    return effect[PAIR_FIELD]


def _reader_descriptor(program):
    readers = tuple(handler for handler in program.handlers if handler.get("handler_id") == MANA_HANDLER)
    catalogs = tuple(handler for handler in program.handlers if handler.get("handler_id") == "activation.catalog.pinned.v1")
    catalog = catalogs[0].get("ability", {}).get("color_set_mana_output") if len(catalogs) == 1 else None
    has_catalog_reader = isinstance(catalog, Mapping) and catalog.get("relation") == COLOR_RELATION
    if not readers and not has_catalog_reader:
        return None
    if (len(readers) != 1 or len(catalogs) > 1 or len(program.handlers) != 1 + len(catalogs)
            or catalogs and catalog != readers[0].get("ability")
            or program.effects or program.target_schema is not None
            or (program.event, program.active_zone) != ("activate", "battlefield")):
        raise ValueError("Linked graveyard mana requires its exact non-targeted battlefield reader")
    from ..color_set_mana_abilities import ColorSetActivatedManaAbilitySpec, ColorSetRelation

    spec = ColorSetActivatedManaAbilitySpec.from_dict(readers[0]["ability"])
    if spec.relation is not ColorSetRelation.LINKED_GRAVEYARD_EXILE:
        raise ValueError("The graveyard mana reader lost its exact color source")
    return spec


def linked_graveyard_mana_program_dependencies(program):
    from .effect_shape_walkers import contains_nested_key

    marked = contains_nested_key((program.effects, program.handlers), PAIR_FIELD)
    reader = _reader_descriptor(program)
    if reader is not None and not marked:
        return READER_REQUIREMENTS
    if not marked:
        return ()
    if (reader is not None or len(program.effects) != 1 or program.handlers
            or (program.event, program.active_zone) != ("permanent.enter.self", "battlefield")
            or program.event_condition is not None or program.cost_schema is not None
            or program.trigger_limit is not None or not isinstance(program.target_schema, Mapping)
            or type(program.target_schema.get("up_to")) is not int
            or FrozenMap(program.target_schema) != FrozenMap(graveyard_target_schema())):
        raise ValueError("Linked graveyard mana requires its original three-target entry producer")
    _producer_pair(program.effects[0])
    return PRODUCER_REQUIREMENTS


def is_closed_linked_graveyard_mana_producer(program):
    if not any(PAIR_FIELD in effect for effect in program.effects):
        return False
    try:
        required = linked_graveyard_mana_program_dependencies(program)
        return bool(required) and set(required) <= set(program.capability_dependencies)
    except (TypeError, ValueError, KeyError):
        return False


def validate_linked_graveyard_mana_group(programs):
    producers, readers = [], []
    for program in programs:
        if not linked_graveyard_mana_program_dependencies(program):
            continue
        face = program.provenance.get("face_id") or "front"
        if program.event == "activate":
            readers.append((face, _reader_descriptor(program).linked_exile_pair))
        else:
            producers.append((face, _producer_pair(program.effects[0])))
    if (producers or readers) and (len(producers) != 1 or producers != readers):
        raise ValueError("Linked graveyard mana must share one compiled face and ability pair")


def linked_graveyard_mana_source_context(source, program, event):
    if not any(PAIR_FIELD in effect for effect in program.effects):
        return {}
    if event != "permanent.enter" or not is_closed_linked_graveyard_mana_producer(program):
        raise ValueError("Linked graveyard source must be sealed at its own entry")
    identity = ContinuousObjectIdentity(source.object_id, source.logical_object_id)
    return {SOURCE_FIELD: identity.to_dict(), "source_logical_object_id": identity.logical_object_id}


def bound_linked_graveyard_mana_pair(effect):
    pair = effect.get(PAIR_FIELD)
    validate_linked_ability_binding(pair)
    if any(field in effect for field in ("announced_target_count", "target_roles")):
        raise ValueError("Linked graveyard mana cannot reuse another target-set mode")
    source = ContinuousObjectIdentity.from_dict(thaw_value(effect.get(SOURCE_FIELD)))
    return source, pair


def validate_linked_graveyard_mana_exile_commit(host, intent):
    from ..errors import GameRuleError

    parent = next((item for item in host.state.stack if item.ref == intent.parent_stack_ref), None)
    if (parent is None or parent.kind != "triggered_ability" or parent.controller != intent.actor
            or parent.context.get("currently_resolving") is not True
            or parent.context.get(SOURCE_FIELD) != intent.source.to_dict()
            or tuple(ref for ref in parent.targets if ref is not None) != intent.object_refs):
        raise GameRuleError("Linked graveyard mana lost its resolving targeted entry trigger")
    program = host.semantics.get(parent.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_linked_graveyard_mana_producer(program)
            or program.effects[0].get(PAIR_FIELD) != intent.pair):
        raise GameRuleError("Linked graveyard mana requires its admitted original producer")
