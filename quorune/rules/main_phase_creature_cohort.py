"""Prepare one incarnation-locked creature cohort for a cast-history Addendum."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..cast_origin_replacement import COHORT_ADDENDUM_FIELD
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import thaw_value
from .effect_shape_walkers import nested_effects

COHORT_GRANT_FIELD = "resolution_locked_creature_cohort"
COHORT_ADDENDUM_CAPABILITY = "resolution.effect.main_phase_creature_cohort"
COHORT_ADDENDUM_TEMPLATE = "own-main-phase-creature-cohort-v1"
COHORT_ADDENDUM_DEPENDENCIES = (
    COHORT_ADDENDUM_CAPABILITY, "continuous.resolution.fixed_characteristics_until_end_of_turn",
    "counter.producer.fixed_permanent_target_set_effect", "permanent.indestructible.ordinary",
    "combat.attack.vigilance", "resolution.effect.fixed_cast_fact",
)
_OPERATION = "modify_all_matching_permanents_until_end_of_turn"
_GRANT_OPERATION = "apply_source_characteristics_until_end_of_turn"


def creature_cohort_addendum_effect():
    return {"op": _OPERATION, COHORT_ADDENDUM_FIELD: 1}


def uses_creature_cohort_addendum(effect):
    return isinstance(effect, Mapping) and COHORT_ADDENDUM_FIELD in effect


def creature_cohort_addendum_node_capabilities(*, effects, target_schema, mechanic_ids=()):
    del mechanic_ids
    if len(effects) != 1 or target_schema is not None:
        return ()
    effect, = effects
    return (
        COHORT_ADDENDUM_DEPENDENCIES
        if (thaw_value(effect) == creature_cohort_addendum_effect()
            and type(effect.get(COHORT_ADDENDUM_FIELD)) is int) else ()
    )


def creature_cohort_addendum_program_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if uses_creature_cohort_addendum(effect) or COHORT_GRANT_FIELD in effect)
    template = (program.provenance or {}).get("template_id")
    if not marked and template != COHORT_ADDENDUM_TEMPLATE and COHORT_ADDENDUM_CAPABILITY not in program.capability_dependencies:
        return ()
    required = creature_cohort_addendum_node_capabilities(effects=program.effects, target_schema=program.target_schema)
    if (not required or marked != tuple(program.effects) or program.handlers or program.cost_schema is not None
            or program.event_condition is not None or (program.event, program.active_zone) != ("resolve", "stack")):
        raise ValueError("The Addendum cohort requires its exact untargeted spell")
    return required


def is_closed_creature_cohort_addendum_program(program):
    try:
        required = creature_cohort_addendum_program_dependencies(program)
    except (TypeError, ValueError):
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class CreatureCohortMember:
    ref: str
    zone_change_counter: int

    def __post_init__(self):
        if (type(self.ref) is not str or not self.ref or self.ref.startswith("$")
                or type(self.zone_change_counter) is not int or self.zone_change_counter < 0):
            raise ValueError("Cohort membership requires a public reference and zone incarnation")

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"ref", "zone_change_counter"}:
            raise ValueError("Cohort membership has a closed shape")
        return cls(value["ref"], value["zone_change_counter"])

    def to_dict(self):
        return {"ref": self.ref, "zone_change_counter": self.zone_change_counter}


def _grant(members, keyword):
    return {"op": _GRANT_OPERATION, COHORT_GRANT_FIELD: 1,
            "members": [member.to_dict() for member in members], "keywords": [keyword]}


def prepare_creature_cohort_addendum(host, item, effects):
    if not any(uses_creature_cohort_addendum(effect) for effect in effects):
        return effects
    program = host.semantics.get(item.semantic_key)
    if (program is None or not is_closed_creature_cohort_addendum_program(program)
            or not creature_cohort_addendum_node_capabilities(effects=effects, target_schema=None)
            or not host.semantic_program_is_current_trusted(program)):
        raise ValueError("Addendum cohort requires the trusted resolving spell")
    from ..cast_origin_replacement import was_cast_during_own_main_phase
    from ..continuous_effect_state import matching_battlefield_objects

    addendum = was_cast_during_own_main_phase(item)
    query = ObjectQuerySpec(zones=("battlefield",), controller=item.controller, types_all=("creature",))
    cards = matching_battlefield_objects(host, query)
    members = tuple(CreatureCohortMember(card.ref, card.zone_change_counter) for card in cards)
    if not members:
        return []
    effects = [_grant(members, "Indestructible")]
    if addendum:
        effects.extend((
            {"op": "place_counters_on_targets", "cards": [member.ref for member in members],
             "maximum_targets": len(members), "counter": "+1/+1", "amount": 1, "source": "$source"},
            _grant(members, "Vigilance"),
        ))
    return effects


def current_creature_cohort_grant(host, effect, *, actor):
    fields = {"op", COHORT_GRANT_FIELD, "members", "keywords"}
    instruction = {key: value for key, value in effect.items() if key not in {"reason", "_runtime_source"}}
    if (set(instruction) != fields or type(instruction[COHORT_GRANT_FIELD]) is not int
            or instruction[COHORT_GRANT_FIELD] != 1 or instruction["op"] != _GRANT_OPERATION
            or list(instruction["keywords"]) not in [["Indestructible"], ["Vigilance"]]
            or not isinstance(instruction["members"], (list, tuple))):
        raise ValueError("Locked creature grant needs its exact cohort and keyword")
    members = tuple(CreatureCohortMember.from_dict(row) for row in instruction["members"])
    if (not members or len({member.ref for member in members}) != len(members)
            or len(members) > len(host.state.cards)):
        raise ValueError("Cohort membership is empty, duplicated, or outside the battlefield")
    item = host.state.stack[-1] if host.state.stack else None
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.controller != actor or item.context.get("currently_resolving") is not True
            or program is None or not is_closed_creature_cohort_addendum_program(program)
            or not host.semantic_program_is_current_trusted(program)):
        raise ValueError("Locked creature grant requires the originating closeable resolution")
    by_ref = {card.ref: card for card in host.state.cards.values() if card.zone == "battlefield" and not card.phased_out}
    affected = tuple(by_ref[member.ref] for member in members if member.ref in by_ref
                     and by_ref[member.ref].zone_change_counter == member.zone_change_counter)
    return affected, tuple(instruction["keywords"])
