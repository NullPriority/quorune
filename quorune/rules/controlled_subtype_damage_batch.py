"""One controlled subtype damage occurrence per final committed batch."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..creature_subtypes import canonical_creature_subtype
from ..replacement.immutable import thaw_value
from .token_creation_capability_shapes import fixed_token_creation_node_capabilities


EVENT = "damage.dealt.controlled_subtype_opponents"
CAPABILITY = "trigger.event.controlled_subtype_damaged_opponents"
MECHANIC = "controlled-subtype-opponents-damage-batch"
TEMPLATE = "controlled-subtype-opponents-damage-batch-v1"
VARIANT = "controlled-subtype-opponents-damage-treasure"
AMOUNT_REFERENCE = "$context.amount"


def controlled_subtype_damage_condition(subtype):
    return {"all": [
        {"field": "source_controller", "op": "eq", "value": "$source.controller"},
        {"field": "source_subtypes", "op": "contains_any", "value": [subtype]},
    ]}


def _condition_subtype(condition):
    condition = thaw_value(condition)
    try:
        subtype = condition["all"][1]["value"][0]
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise ValueError("Grouped damage requires its exact controller and subtype") from exc
    if (type(subtype) is not str or canonical_creature_subtype(subtype) != subtype
            or condition != controlled_subtype_damage_condition(subtype)):
        raise ValueError("Grouped damage requires its exact controller and subtype")
    return subtype


def controlled_subtype_damage_node_capabilities(*, effects, target_schema, mechanics):
    if MECHANIC not in mechanics:
        return None
    if target_schema is not None or len(effects) != 1 or not isinstance(effects[0], Mapping):
        return ()
    effect = effects[0]
    if (effect.get("op") != "create_token" or effect.get("name") != "Treasure Token"
            or effect.get("controller") != "$controller" or effect.get("quantity") != AMOUNT_REFERENCE):
        return ()
    token = fixed_token_creation_node_capabilities(
        effects=({**thaw_value(effect), "quantity": 1},), target_schema=None,
        mechanic_ids={"cr-111-tokens"},
    )
    if not token:
        return ()
    return (CAPABILITY, "trigger.event.normalized_damage", "trigger.effect.fixed_event",
            "trigger.placement.apnap", *token)


def controlled_subtype_damage_runtime_dependencies(program):
    if program.event != EVENT and MECHANIC not in program.coverage:
        return None
    required = controlled_subtype_damage_node_capabilities(
        effects=program.effects, target_schema=program.target_schema, mechanics=program.coverage,
    )
    if (not required or (program.event, program.active_zone) != (EVENT, "battlefield")
            or program.handlers or program.cost_schema is not None or program.trigger_limit is not None):
        raise ValueError("Grouped damage requires its complete battlefield trigger")
    _condition_subtype(program.event_condition)
    return required


def is_closed_controlled_subtype_damage_program(program):
    try:
        required = controlled_subtype_damage_runtime_dependencies(program)
    except (TypeError, ValueError, KeyError):
        return False
    return bool(required) and set(required) <= set(program.capability_dependencies)


@dataclass(frozen=True, slots=True)
class ControlledSubtypeDamageOccurrence:
    event_id: str
    controller: str
    subtype: str
    hits: tuple[tuple[str, str, int], ...]

    def __post_init__(self):
        if (type(self.event_id) is not str or not self.event_id
                or type(self.controller) is not str or not self.controller
                or type(self.subtype) is not str or canonical_creature_subtype(self.subtype) != self.subtype
                or not isinstance(self.hits, tuple) or not self.hits):
            raise ValueError("Grouped damage requires one qualifying final occurrence")
        for hit in self.hits:
            if (not isinstance(hit, tuple) or len(hit) != 3
                    or any(type(value) is not str or not value for value in hit[:2])
                    or hit[1] == self.controller or type(hit[2]) is not int or hit[2] < 1):
                raise ValueError("Grouped damage requires positive opposing-player damage")

    @property
    def opponents(self):
        return tuple(sorted({hit[1] for hit in self.hits}))

    def semantic_context(self):
        return {
            "event_id": self.event_id, "source_controller": self.controller, "source_subtypes": [self.subtype],
            "amount": len(self.opponents), "damaged_opponents": list(self.opponents),
            "damage_hits": [list(hit) for hit in self.hits],
        }


def controlled_subtype_damage_occurrences(events, *, event_id, active_seats, wanted_subtypes):
    groups = {}
    for event in events:
        if (not event.was_dealt or event.target_kind != "player" or event.source_zone != "battlefield"
                or event.source_controller not in active_seats or event.target not in active_seats
                or event.target == event.source_controller):
            continue
        hit = (event.source_logical_object_id, event.target, event.dealt_amount)
        for subtype in wanted_subtypes.intersection(event.source_subtypes):
            groups.setdefault((event.source_controller, subtype), []).append(hit)
    return tuple(
        ControlledSubtypeDamageOccurrence(event_id, controller, subtype, tuple(sorted(rows)))
        for (controller, subtype), rows in sorted(groups.items())
    )


def dispatch_controlled_subtype_damage_batches(host, result, *, sources, source_zones, trigger_batch):
    registry = getattr(host, "semantics", None)
    if registry is None:
        return
    wanted = frozenset(
        _condition_subtype(program.event_condition) for program in registry.programs_for_event(EVENT)
        if host.semantic_program_is_current_trusted(program) and is_closed_controlled_subtype_damage_program(program)
    )
    if not wanted:
        return
    for occurrence in controlled_subtype_damage_occurrences(
        result.events, event_id=result.event_batch_id, active_seats=frozenset(host.active_seats), wanted_subtypes=wanted,
    ):
        host._dispatch_semantic_event(
            EVENT, occurrence.semantic_context(), sources=sources, source_zones=source_zones, trigger_batch=trigger_batch,
        )
