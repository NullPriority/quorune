from __future__ import annotations

"""Immutable affected-set selection for fixed permanent counter effects."""

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from typing import Any, Protocol, Sequence

from .affected_permanents import (
    AffectedPermanentSetError,
    AffectedPermanentSetSpec,
    PermanentControllerRelation,
    select_affected_permanents,
)
from .counter_placement import (
    CounterPlacementError,
    CounterPlacementHost,
    CounterPlacementRequest,
    CounterPlacementResult,
    place_counters,
)
from .characteristic_read_frames import characteristic_read_method
from .creature_subtypes import canonical_creature_subtype
from .object_query import ObjectQueryResult
from .object_predicate import ObjectQuerySpec
from .util import stable_json


PAIRED_PLANESWALKER_LOYALTY_FIELD = "paired_other_planeswalker_loyalty_version"
PAIRED_PLANESWALKER_LOYALTY_CAPABILITY = "counter.producer.paired_creature_and_planeswalker"


SOURCE_AND_CONTROLLED_SUBTYPE_FIELD = "source_and_controlled_subtype_counter_version"
SOURCE_TARGET_COUNTER_BATCH_CAPABILITY = "counter.producer.target_and_source_multikind"
SOURCE_TARGET_COUNTER_BATCH_VERSION_FIELD = "source_target_counter_batch_version"
SOURCE_TARGET_COUNTER_BATCH_CARD_FIELD = "paired_source_card"
_SOURCE_TARGET_COUNTER_BATCH_FIELDS = {SOURCE_TARGET_COUNTER_BATCH_VERSION_FIELD, SOURCE_TARGET_COUNTER_BATCH_CARD_FIELD}


def uses_source_target_counter_batch(effect):
    return isinstance(effect, Mapping) and bool(_SOURCE_TARGET_COUNTER_BATCH_FIELDS.intersection(effect))


def source_target_counter_batch_projection(effect, *, bound=False):
    """Validate the paired placement and return its existing one-object form."""
    from .replacement.immutable import FrozenMap

    keys = {"op", "card", "placements", "source"} | _SOURCE_TARGET_COUNTER_BATCH_FIELDS
    # The ordinary bound handler validates its own additional runtime fields.
    fields_match = keys <= set(effect) if bound else keys == set(effect)
    if (not fields_match or effect["op"] != "place_counter_batch"
            or type(effect[SOURCE_TARGET_COUNTER_BATCH_VERSION_FIELD]) is not int
            or effect[SOURCE_TARGET_COUNTER_BATCH_VERSION_FIELD] != 1):
        raise ValueError("The paired counter batch requires its exact versioned fields")
    target, source = effect["card"], effect[SOURCE_TARGET_COUNTER_BATCH_CARD_FIELD]
    if bound:
        if (type(target) is not str or not target or target.startswith("$")
                or source is not None and (type(source) is not str or not source or source.startswith("$")
                                           or source == target or source != effect["source"])):
            raise ValueError("The paired counter batch requires the resolved other creature and optional original source")
    elif (target, source, effect["source"]) != ("$target.0", "$source.zone_object", "$source"):
        raise ValueError("The paired counter batch requires its original two subject bindings")
    expected = FrozenMap({"placements": [
        {"counter": "+1/+1", "amount": 1}, {"counter": "lifelink", "amount": 1},
    ]})
    placements = effect["placements"]
    if (not isinstance(placements, (list, tuple)) or FrozenMap({"placements": placements}) != expected
            or any(type(value["amount"]) is not int for value in placements)):
        raise ValueError("The paired placement requires exactly one +1/+1 and one lifelink counter for each subject")
    return {key: value for key, value in effect.items() if key not in _SOURCE_TARGET_COUNTER_BATCH_FIELDS}


def validate_counter_set_recipient_authority(intent, counter_name):
    """Validate the fixed counter-set recipient authority before resolution."""
    identity = intent.paired_loyalty_source
    if identity is not None and (
        not isinstance(identity, tuple) or len(identity) != 2
        or any(type(value) is not str or not value for value in identity)
        or intent.source_ref is None or counter_name != "+1/+1" or type(intent.amount) is not int or intent.amount != 1
    ):
        raise ValueError("Paired loyalty requires its original source and one fixed +1/+1 placement")
    source_and_subtype = intent.source_and_subtype_identity
    if source_and_subtype is not None and (
        identity is not None or not isinstance(source_and_subtype, tuple) or len(source_and_subtype) != 2
        or any(type(value) is not str or not value for value in source_and_subtype)
        or intent.source_ref is None or counter_name != "+1/+1" or type(intent.amount) is not int or intent.amount != 1
    ):
        raise ValueError("Source and subtype counters require one original source and one fixed +1/+1 placement")
    if intent.declared_attack_members is not None:
        from .rules.attack_batches import declared_attack_counter_set, validate_declared_attack_counter_members

        if (identity is not None or source_and_subtype is not None or intent.source_ref is None
                or counter_name != "+1/+1" or type(intent.amount) is not int or intent.amount != 1
                or intent.spec != declared_attack_counter_set()):
            raise ValueError("Declared attack counters need one fixed untargeted counter batch")
        return validate_declared_attack_counter_members(intent.declared_attack_members)
    return None


def controlled_counter_subtype_subjects(subtype):
    if canonical_creature_subtype(subtype) != subtype or subtype is None:
        raise CounterPlacementSetError("Controlled counter subtype must be a canonical creature subtype")
    return AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), subtypes_all=(subtype,)),
        controller_relation=PermanentControllerRelation.ACTOR,
    )


def controlled_counter_subjects(card_type, *, exclude_source=False):
    return AffectedPermanentSetSpec(
        query=ObjectQuerySpec(zones=("battlefield",), types_all=(card_type,)),
        controller_relation=PermanentControllerRelation.ACTOR, exclude_source=exclude_source,
    )


def bind_counter_receipt_source_exclusion(host, item, effect):
    """Exclude the original source of a receipt-qualified creature-set result."""
    if (not isinstance(effect, Mapping) or effect.get("op") != "place_counters_on_set"
            or effect.get("counter") != "+1/+1"
            or type(effect.get("amount")) is not int or effect["amount"] != 1
            or effect.get("set") != controlled_counter_subjects("creature", exclude_source=True).to_dict()):
        return effect
    from .public_history_query_model import SOURCE_COUNTER_RECEIPT_CAPABILITY

    program = host.semantics.get(item.semantic_key)
    if (program is None or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or SOURCE_COUNTER_RECEIPT_CAPABILITY not in program.capability_dependencies
            or not host.semantic_program_is_current_trusted(program)):
        return effect
    original = item.context.get("source_logical_object_id")
    if type(original) is not str or not original:
        raise CounterPlacementSetError("Counter receipt source exclusion requires the triggering incarnation")
    source = host.state.cards.get(item.source_object_id)
    if source is not None and source.logical_object_id == original:
        return effect
    return {**effect, "set": controlled_counter_subjects("creature").to_dict()}


SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY = "counter.producer.source_and_controlled_subtype"


SOURCE_POWER_COMBAT_COUNTER_CONDITION = {"all": [
    {"field": "player", "op": "eq", "value": "$source.controller"},
    {"field": "step", "op": "eq", "value": "beginning_combat"},
]}
_SOURCE_POWER_COMBAT_COUNTER_BINDING = "scalar:x:source-power-combat-counter-set"
SOURCE_POWER_COMBAT_TARGET_COUNTER_CAPABILITY = "counter.producer.source_power_combat_target"
SOURCE_POWER_COMBAT_TARGET_COUNTER_MECHANIC = "source-power-combat-target-counter"


def source_power_combat_counter_target_schema():
    return {
        "zones": ["battlefield"], "categories": ["permanent"], "types_any": ["creature"],
        "count": 1, "controller_relation": "you", "source_exclusion": True,
    }


def source_power_combat_counter_amount():
    from .scalar_effect_amount_model import ScalarAmountOrigin, ScalarEffectAmountSpec

    return ScalarEffectAmountSpec(
        ScalarAmountOrigin.SOURCE, characteristic="power", binding_id=_SOURCE_POWER_COMBAT_COUNTER_BINDING,
    ).to_dict()


def source_power_combat_counter_effect():
    return {
        "op": "place_counters_on_set", "source": "$source",
        "set": controlled_counter_subjects("creature").to_dict(),
        "counter": "+1/+1", "amount": source_power_combat_counter_amount(),
    }


def source_power_combat_target_counter_effect():
    return {
        "op": "place_counters", "card": "$target.0", "source": "$source",
        "counter": "+1/+1", "amount": source_power_combat_counter_amount(),
    }


def _matches_source_power_combat_binding(binding):
    return (type(binding) is str and (binding == _SOURCE_POWER_COMBAT_COUNTER_BINDING
            or binding.endswith(":" + _SOURCE_POWER_COMBAT_COUNTER_BINDING)))


def is_source_power_combat_counter_amount(amount):
    from .replacement.immutable import thaw_value
    from .scalar_effect_amount_model import ScalarEffectAmountSpec

    if not isinstance(amount, Mapping) or not _matches_source_power_combat_binding(amount.get("binding_id")):
        return False
    descriptor = {**thaw_value(amount), "binding_id": _SOURCE_POWER_COMBAT_COUNTER_BINDING}
    try:
        return ScalarEffectAmountSpec.from_dict(descriptor).to_dict() == source_power_combat_counter_amount()
    except (TypeError, ValueError, KeyError):
        return False


def uses_source_power_combat_counter_set(effect):
    from .scalar_effect_amount_model import SCALAR_AMOUNT_KIND

    amount = effect.get("amount") if isinstance(effect, Mapping) else None
    return (isinstance(amount, Mapping)
            and (_matches_source_power_combat_binding(amount.get("binding_id"))
                 or effect.get("op") == "place_counters_on_set" and amount.get("kind") == SCALAR_AMOUNT_KIND))


def source_power_combat_counter_projection(effect):
    from .replacement.immutable import thaw_value

    if not uses_source_power_combat_counter_set(effect) or not is_source_power_combat_counter_amount(effect["amount"]):
        raise CounterPlacementSetError("Source-power counters require the declared source-power amount")
    projected = {**thaw_value(effect), "amount": 2}
    expected = (
        source_power_combat_target_counter_effect() if effect.get("op") == "place_counters"
        else source_power_combat_counter_effect()
    )
    if projected != {**expected, "amount": 2}:
        raise CounterPlacementSetError("Source-power counters require their exact controlled creature recipients")
    return projected


def source_power_combat_target_counter_node_capabilities(effects, target_schema):
    from .query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY
    from .scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    if (len(effects) != 1 or effects[0].get("op") != "place_counters"
            or target_schema != source_power_combat_counter_target_schema()
            or type(target_schema["count"]) is not int or target_schema["source_exclusion"] is not True):
        return ()
    try:
        source_power_combat_counter_projection(effects[0])
    except (TypeError, ValueError, KeyError):
        return ()
    return (
        SOURCE_POWER_COMBAT_TARGET_COUNTER_CAPABILITY, SCALAR_AMOUNT_CAPABILITY, DECLARED_AMOUNT_CAPABILITY,
        "counter.producer.fixed_effect", "counter.producer.fixed_event_trigger",
        "target.revalidate_resolution", "trigger.placement.apnap",
    )


def source_power_combat_counter_program_dependencies(program):
    from .replacement.immutable import thaw_value
    from .rules.effect_shape_walkers import nested_effects
    from .scalar_effect_amount_model import SCALAR_AMOUNT_CAPABILITY

    if not any(uses_source_power_combat_counter_set(effect)
               for effect in nested_effects((program.effects, program.handlers))):
        return ()
    if (len(program.effects) != 1 or (program.event, program.active_zone) != ("step.begin", "battlefield")
            or thaw_value(program.event_condition) != SOURCE_POWER_COMBAT_COUNTER_CONDITION
            or program.cost_schema is not None or program.handlers
            or not program.ability_id.startswith("trigger:")):
        raise CounterPlacementSetError("Source-power counters require the controller's beginning-of-combat trigger")
    if program.effects[0].get("op") == "place_counters":
        required = source_power_combat_target_counter_node_capabilities(program.effects, program.target_schema)
        if not required or program.trigger_limit is not None:
            raise CounterPlacementSetError("Source-power targeting requires exactly one other controlled creature")
        return required
    if program.target_schema is not None:
        raise CounterPlacementSetError("Source-power counter sets have no direct targets")
    source_power_combat_counter_projection(program.effects[0])
    from .query_effect_amount_model import DECLARED_AMOUNT_CAPABILITY

    return (SCALAR_AMOUNT_CAPABILITY, DECLARED_AMOUNT_CAPABILITY, "counter.producer.fixed_permanent_set_effect",
            "counter.producer.fixed_event_trigger", "trigger.placement.apnap")


def source_and_controlled_subtype_dependencies(effect, *, source_ref="$source"):
    if (not isinstance(effect, Mapping) or set(effect) != {
            "op", "source", "set", "counter", "amount", SOURCE_AND_CONTROLLED_SUBTYPE_FIELD,
        } or effect["op"] != "place_counters_on_set" or effect["source"] != source_ref
        or effect["counter"] != "+1/+1" or type(effect["amount"]) is not int or effect["amount"] != 1
        or type(effect[SOURCE_AND_CONTROLLED_SUBTYPE_FIELD]) is not int
        or effect[SOURCE_AND_CONTROLLED_SUBTYPE_FIELD] != 1):
        raise CounterPlacementSetError("Source and subtype require one closed fixed counter instruction")
    spec = AffectedPermanentSetSpec.from_dict(effect["set"])
    subtypes = spec.query.subtypes_all
    if len(subtypes) != 1 or spec != controlled_counter_subtype_subjects(subtypes[0]):
        raise CounterPlacementSetError("Source and subtype require one current controlled subtype")
    return ("counter.producer.fixed_permanent_set_effect", SOURCE_AND_CONTROLLED_SUBTYPE_CAPABILITY)


def _has_source_and_subtype_counter(value):
    if isinstance(value, Mapping):
        return SOURCE_AND_CONTROLLED_SUBTYPE_FIELD in value or any(
            _has_source_and_subtype_counter(child) for child in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_source_and_subtype_counter(child) for child in value)
    return False


def source_and_controlled_subtype_program_dependencies(program):
    if _has_source_and_subtype_counter((program.target_schema, program.cost_schema, program.handlers)):
        raise ValueError("Source and subtype counters belong only to the fixed triggered result")
    if not _has_source_and_subtype_counter(program.effects):
        return ()
    if (len(program.effects) != 1 or program.event != "artifact.graveyard"
            or program.active_zone != "battlefield" or program.event_condition is not None
            or program.target_schema is not None or program.cost_schema is not None or program.handlers
            or "one_or_more_event_batch" in program.coverage):
        raise ValueError("Source and subtype counters require one ordinary original artifact departure")
    return source_and_controlled_subtype_dependencies(program.effects[0])


def paired_planeswalker_loyalty_dependencies(effect, *, source_ref="$source"):
    if (not isinstance(effect, Mapping) or set(effect) != {
            "op", "source", "set", "counter", "amount", PAIRED_PLANESWALKER_LOYALTY_FIELD,
        } or effect["op"] != "place_counters_on_set" or effect["source"] != source_ref
        or effect["counter"] != "+1/+1" or type(effect["amount"]) is not int or effect["amount"] != 1
        or type(effect[PAIRED_PLANESWALKER_LOYALTY_FIELD]) is not int
        or effect[PAIRED_PLANESWALKER_LOYALTY_FIELD] != 1
        or AffectedPermanentSetSpec.from_dict(effect["set"]) != controlled_counter_subjects("creature")):
        raise CounterPlacementSetError("Paired loyalty requires both fixed controlled sets and the original source")
    return ("counter.producer.fixed_permanent_set_effect", PAIRED_PLANESWALKER_LOYALTY_CAPABILITY)


class CounterPlacementSetError(ValueError):
    """A fixed affected-set counter instruction or snapshot is invalid."""


def _nonempty(value: Any, *, field: str) -> str:
    if type(value) is not str or not value:
        raise CounterPlacementSetError(f"{field} must be a nonempty string")
    return value


@dataclass(frozen=True, slots=True)
class CounterPlacementSetPermanent:
    object_id: str
    logical_object_id: str
    ref: str
    controller: str

    def __post_init__(self) -> None:
        for field, value in (
            ("object ID", self.object_id),
            ("logical object ID", self.logical_object_id),
            ("reference", self.ref),
            ("controller", self.controller),
        ):
            _nonempty(value, field=f"Counter-set permanent {field}")

    def to_dict(self) -> dict[str, str]:
        return {
            "object_id": self.object_id,
            "logical_object_id": self.logical_object_id,
            "ref": self.ref,
            "controller": self.controller,
        }


@dataclass(frozen=True, slots=True)
class CounterPlacementSetSnapshot:
    spec: AffectedPermanentSetSpec
    permanents: tuple[CounterPlacementSetPermanent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.spec, AffectedPermanentSetSpec):
            raise CounterPlacementSetError(
                "Counter-set snapshots require a typed affected set"
            )
        permanents = tuple(self.permanents)
        if any(
            not isinstance(value, CounterPlacementSetPermanent)
            for value in permanents
        ):
            raise CounterPlacementSetError(
                "Counter-set snapshots require typed permanents"
            )
        object_ids = tuple(value.object_id for value in permanents)
        logical_ids = tuple(value.logical_object_id for value in permanents)
        if len(object_ids) != len(set(object_ids)) or len(logical_ids) != len(
            set(logical_ids)
        ):
            raise CounterPlacementSetError(
                "Counter-set snapshots require unique permanent identities"
            )
        object.__setattr__(self, "permanents", permanents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "permanents": [value.to_dict() for value in self.permanents],
        }

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(
            stable_json(self.to_dict()).encode("utf-8")
        ).hexdigest()


class CounterPlacementSetQuery(Protocol):
    def affected_permanent_active_seats(self) -> tuple[str, ...]: ...

    def affected_permanent_apnap_order(self) -> tuple[str, ...]: ...

    def affected_permanent_object_rows(
        self, actor: str
    ) -> tuple[ObjectQueryResult, ...]: ...


class CounterPlacementSetHost(
    CounterPlacementHost,
    CounterPlacementSetQuery,
    Protocol,
):
    pass


@characteristic_read_method
def snapshot_counter_placement_set(
    query: CounterPlacementSetQuery,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    source_ref: str | None = None,
) -> CounterPlacementSetSnapshot:
    """Freeze the exact public affected set before counter preflight."""

    try:
        selected = select_affected_permanents(
            query.affected_permanent_object_rows(actor),
            spec,
            actor=actor,
            active_seats=query.affected_permanent_active_seats(),
            apnap_order=query.affected_permanent_apnap_order(),
            source_ref=source_ref,
        )
    except AffectedPermanentSetError as exc:
        raise CounterPlacementSetError(str(exc)) from exc
    return CounterPlacementSetSnapshot(
        spec=spec,
        permanents=tuple(
            CounterPlacementSetPermanent(
                object_id=row.object_id,
                logical_object_id=row.logical_object_id,
                ref=row.ref,
                controller=row.controller,
            )
            for row in selected
        ),
    )


def _set_counter_requests(snapshot, *, actor, counter_name, amount, source_ref):
    return tuple(CounterPlacementRequest(
        subject_kind="permanent", subject_id=current.object_id,
        counter_name=counter_name, amount=amount, placing_player=actor, source_ref=source_ref,
    ) for current in snapshot.permanents)


def source_and_controlled_subtype_counter_requests(snapshot, current_source, *, actor, source_ref):
    """Seal the source and subtype contributions through the shared request type."""
    if not isinstance(snapshot, CounterPlacementSetSnapshot):
        raise CounterPlacementSetError("Controlled subtype requires a counter set snapshot")
    if current_source is not None and not isinstance(current_source, CounterPlacementSetPermanent):
        raise CounterPlacementSetError("The source contribution needs a current scoped permanent")
    subtypes = snapshot.spec.query.subtypes_all
    if len(subtypes) != 1 or snapshot.spec != controlled_counter_subtype_subjects(subtypes[0]):
        raise CounterPlacementSetError("The subtype contribution requires an exact controlled subtype set")

    selected = {permanent.object_id: permanent for permanent in snapshot.permanents}
    if current_source is not None:
        candidate = selected.get(current_source.object_id)
        if candidate is not None and candidate != current_source:
            raise CounterPlacementSetError("Counter contributions disagree about the current source")
        selected = {current_source.object_id: current_source, **selected}
    members = {permanent.object_id for permanent in snapshot.permanents}
    return tuple(CounterPlacementRequest(
        subject_kind="permanent", subject_id=object_id, counter_name="+1/+1",
        amount=int(object_id in members) + int(permanent == current_source),
        placing_player=actor, source_ref=source_ref,
    ) for object_id, permanent in selected.items())


def _paired_planeswalker_requests(host, actor, source_ref, identity):
    object_id, original_incarnation = identity
    source = host.state.cards.get(object_id)
    source_is_current = (source is not None and source.zone == "battlefield"
                         and source.logical_object_id == original_incarnation)
    walkers = snapshot_counter_placement_set(
        host, actor=actor, source_ref=source_ref,
        spec=controlled_counter_subjects("planeswalker", exclude_source=source_is_current),
    )
    return _set_counter_requests(walkers, actor=actor, counter_name="loyalty", amount=1, source_ref=source_ref)


def resolve_counter_placement_set(
    host: CounterPlacementSetHost,
    *,
    actor: str,
    spec: AffectedPermanentSetSpec,
    counter_name: str,
    amount: int,
    reason: str,
    source_ref: str | None = None,
    replacement_selections: Sequence[str | dict[str, Any]] = (),
    paired_loyalty_source: tuple[str, str] | None = None,
    source_and_subtype_identity: tuple[str, str] | None = None,
    declared_attack_members: tuple[tuple[str, str], ...] | None = None,
) -> tuple[CounterPlacementResult, ...]:
    """Resolve one fixed affected set through the canonical counter owner."""

    _nonempty(actor, field="Counter-set actor")
    normalized_counter = " ".join(
        _nonempty(counter_name, field="Counter-set counter").casefold().split()
    )
    _nonempty(normalized_counter, field="Counter-set counter")
    _nonempty(reason, field="Counter-set reason")
    if type(amount) is not int or amount <= 0:
        raise CounterPlacementSetError(
            "Counter-set amount must be a positive exact integer"
        )
    if source_ref is not None:
        _nonempty(source_ref, field="Counter-set source")
    if spec.exclude_source and source_ref is None:
        raise CounterPlacementSetError(
            "Source-excluding counter sets require a source"
        )
    if declared_attack_members is not None:
        from .rules.attack_batches import current_declared_attack_counter_recipients, declared_attack_counter_set

        if (source_and_subtype_identity is not None or paired_loyalty_source is not None
                or normalized_counter != "+1/+1" or amount != 1 or spec != declared_attack_counter_set()):
            raise CounterPlacementSetError("Declared attack counters require the fixed recorded group")
        public = {row.ref: row for row in host.affected_permanent_object_rows(actor)}
        recipients = current_declared_attack_counter_recipients(declared_attack_members, public.get)
        snapshot = CounterPlacementSetSnapshot(spec, tuple(
            CounterPlacementSetPermanent(row.object_id, row.logical_object_id, row.ref, row.controller)
            for row in recipients
        ))
    else:
        snapshot = snapshot_counter_placement_set(host, actor=actor, spec=spec, source_ref=source_ref)
    if source_and_subtype_identity is None:
        requests = _set_counter_requests(
            snapshot, actor=actor, counter_name=normalized_counter, amount=amount, source_ref=source_ref,
        )
    else:
        if paired_loyalty_source is not None or normalized_counter != "+1/+1" or amount != 1:
            raise CounterPlacementSetError("Source and subtype counters require one fixed +1/+1 result")
        source_id, original_incarnation = source_and_subtype_identity
        source = host.state.cards.get(source_id)
        current_source = None
        if (source is not None and source.zone == "battlefield" and not source.phased_out
                and source.logical_object_id == original_incarnation):
            current_source = CounterPlacementSetPermanent(
                source.object_id, source.logical_object_id, source.ref, source.controller,
            )
        requests = source_and_controlled_subtype_counter_requests(
            snapshot, current_source, actor=actor, source_ref=source_ref,
        )
    if paired_loyalty_source is not None:
        requests += _paired_planeswalker_requests(host, actor, source_ref, paired_loyalty_source)
    try:
        return place_counters(host, requests, selections=replacement_selections, reason=reason)
    except CounterPlacementError as exc:
        raise CounterPlacementSetError(str(exc)) from exc


__all__ = [
    "CounterPlacementSetError",
    "CounterPlacementSetHost",
    "CounterPlacementSetPermanent",
    "CounterPlacementSetQuery",
    "CounterPlacementSetSnapshot",
    "resolve_counter_placement_set",
    "snapshot_counter_placement_set",
]
