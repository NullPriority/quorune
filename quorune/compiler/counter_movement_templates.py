"""Lower printed counter movement through its complete target roles."""

from ..fixed_counter_movement_model import (
    CHOSEN_CONTROLLED_PERMANENT_QUANTITIES, CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED,
    CHOSEN_PERMANENT_TARGETS, CONTROLLED_TARGET_TO_SOURCE, OPTIONAL_TARGET_TO_SOURCE,
    OPTIONAL_CHOSEN_CONTROLLED_CREATURES, TWO_DISTINCT_TARGETS, FixedCounterMovementSpec,
)
from ..replacement.immutable import FrozenMap
from ..rules.cumulative_object_upkeep import CONTROLLER_UPKEEP
from ..rules.departure_counter_transfer import (
    DEPARTURE_COUNTER_EVENT, DEPARTURE_COUNTER_MECHANICS, DEPARTURE_COUNTER_TRANSFER_CAPABILITY,
    DEPARTURE_COUNTER_TRANSFER_COVERAGE, departure_counter_condition, departure_counter_effect,
    departure_counter_target_schema,
)
from .fixed_public_event_trigger_bindings import FixedPublicEventBindingSpec
from ..rules.fixed_counter_movement import (
    CONTROLLER_BEGINNING_COMBAT, FIXED_COUNTER_MOVEMENT_MECHANICS,
    chosen_counter_movement_price_is_closed, fixed_counter_movement_target_schema,
)


_DEPARTURE_BODY = "put those counters on target permanent you control."
_DEPARTURE_PREFIX = "whenever a permanent you control leaves the battlefield, if it had counters on it, "
_DEPARTURE_TEMPLATE = "fixed-controlled-departure-counter-transfer-v1"
_OPTIONAL_BODY = "you may move a +1/+1 counter from target creature onto this creature."
_SOURCE_DISTRIBUTION_BODY = "you may move any number of +1/+1 counters from this creature onto other creatures."
_SOURCE_TRANSFER_BODY = "you may move any number of +1/+1 counters from this creature onto another target creature."
_OPTIONAL_COMBAT_BODY = "you may move a counter from target creature you control onto a second target creature you control."
_CHOSEN_QUANTITIES_BODY = "move any number of counters from target permanent you control onto a second target permanent you control."
_CHOSEN_BODY = "move a counter from target permanent you control onto a second target permanent."
_CHOSEN_OPPONENT_BODY = (
    "move a counter from target creature an opponent controls onto target creature you control."
)
_ACTIVATION_BODIES = {
    "move a +1/+1 counter from target creature you control onto this creature.": CONTROLLED_TARGET_TO_SOURCE,
    "move a +1/+1 counter from target creature onto a second target creature.": TWO_DISTINCT_TARGETS,
}


def _compiled(form):
    versions = {
        CHOSEN_PERMANENT_TARGETS: 2, OPTIONAL_CHOSEN_CONTROLLED_CREATURES: 3,
        CHOSEN_CONTROLLED_PERMANENT_QUANTITIES: 4, CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED: 5,
    }
    spec = FixedCounterMovementSpec(form, schema_version=versions.get(form, 1))
    template = f"chosen-one-counter-movement-v{spec.schema_version}" if spec.chooses_counter_kind else "fixed-one-counter-movement-v1"
    if spec.chooses_counter_quantities:
        template = "chosen-controlled-counter-quantities-v1"
    return (template, (spec.effect(),), fixed_counter_movement_target_schema(spec), FIXED_COUNTER_MOVEMENT_MECHANICS)


def departure_counter_transfer_binding(material_line):
    prefix_length = len(_DEPARTURE_PREFIX)
    if material_line.casefold() != _DEPARTURE_PREFIX + _DEPARTURE_BODY:
        return None
    return FixedPublicEventBindingSpec(
        event=DEPARTURE_COUNTER_EVENT, variant=DEPARTURE_COUNTER_TRANSFER_COVERAGE,
        body=material_line[prefix_length:], template_id=_DEPARTURE_TEMPLATE,
        mechanic="trigger-event-normalized-zone-change", condition=departure_counter_condition(),
        capabilities=(DEPARTURE_COUNTER_TRANSFER_CAPABILITY,),
    )


def fixed_counter_movement_trigger_result(binding, body):
    if binding.variant == DEPARTURE_COUNTER_TRANSFER_COVERAGE:
        if (binding.event.value != DEPARTURE_COUNTER_EVENT or binding.active_zone != "battlefield"
                or body.casefold() != _DEPARTURE_BODY
                or FrozenMap(binding.event_condition or {}) != FrozenMap(departure_counter_condition())):
            return None
        return (_DEPARTURE_TEMPLATE, (departure_counter_effect(),), departure_counter_target_schema(),
                DEPARTURE_COUNTER_MECHANICS)
    if (binding.event.value, binding.active_zone) != ("step.begin", "battlefield") or body != binding.body:
        return None
    normalized = " ".join(body.split()).casefold()
    condition = FrozenMap(binding.event_condition or {})
    if normalized == _SOURCE_DISTRIBUTION_BODY and condition == FrozenMap(CONTROLLER_UPKEEP):
        from ..rules.source_counter_distribution import (
            SOURCE_COUNTER_DISTRIBUTION_MECHANICS, source_counter_distribution_effect,
        )

        return ("source-upkeep-counter-distribution-v1", (source_counter_distribution_effect(),), None,
                SOURCE_COUNTER_DISTRIBUTION_MECHANICS)
    if normalized == _SOURCE_TRANSFER_BODY and condition == FrozenMap(CONTROLLER_UPKEEP):
        from ..rules.source_counter_distribution import (
            SOURCE_COUNTER_TRANSFER_MECHANICS, source_counter_transfer_effect, source_counter_transfer_target_schema,
        )

        return ("source-upkeep-targeted-counter-transfer-v2", (source_counter_transfer_effect(),),
                source_counter_transfer_target_schema(), SOURCE_COUNTER_TRANSFER_MECHANICS)
    if normalized == _OPTIONAL_BODY and condition == FrozenMap(CONTROLLER_UPKEEP):
        return _compiled(OPTIONAL_TARGET_TO_SOURCE)
    if normalized == _OPTIONAL_COMBAT_BODY and condition == CONTROLLER_BEGINNING_COMBAT:
        return _compiled(OPTIONAL_CHOSEN_CONTROLLED_CREATURES)
    return None


def fixed_counter_movement_activation_result(ability, text):
    body = " ".join(text.split()).casefold()
    if body == _CHOSEN_QUANTITIES_BODY and chosen_counter_movement_price_is_closed(ability, quantities=True):
        return _compiled(CHOSEN_CONTROLLED_PERMANENT_QUANTITIES)
    chosen = {
        _CHOSEN_BODY: CHOSEN_PERMANENT_TARGETS,
        _CHOSEN_OPPONENT_BODY: CHOSEN_OPPONENT_CREATURE_TO_CONTROLLED,
    }.get(body.removesuffix(" activate only as a sorcery."))
    if chosen is not None:
        return _compiled(chosen) if chosen_counter_movement_price_is_closed(ability) else None
    form = _ACTIVATION_BODIES.get(body)
    if form is None or tuple(ability.zones) != ("battlefield",) or ability.mana_ability or ability.uncompiled_costs:
        return None
    return _compiled(form)
