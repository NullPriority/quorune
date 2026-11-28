"""Closed shared-card-type sacrifice payment and settlement shapes."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..apnap_object_payment import DECLINED_LIFE_LOSS
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .effect_shape_walkers import nested_effects
from .event_optional_draw_shapes import controller_end_step_condition


SHARED_TYPE_SACRIFICE = "shared_type_sacrifice"
SHARED_TYPE_SACRIFICE_CAPABILITY = "choice.apnap.shared_type_sacrifice_payment"
SHARED_TYPE_SACRIFICE_TEMPLATE = "controller-end-step-shared-type-sacrifice-v1"
INITIAL_TYPES = ("artifact", "creature", "enchantment", "land", "planeswalker")
# Kindred can coexist with a permanent card type, including the initial price.
_SHARED_TYPES = PERMANENT_CARD_TYPES | {"kindred"}
_REQUIREMENTS = (
    SHARED_TYPE_SACRIFICE_CAPABILITY, "choice.affected_player.fixed_sacrifice",
    "choice.apnap.optional_object_payment_or_life_loss", "zone.change.destination_replacement",
    "life.change.effect", "zone.draw.library_to_hand", "trigger.effect.fixed_event", "trigger.placement.apnap",
    "zone.graveyard.ordered_simultaneous_arrival", "zone.composition.departure_order",
)


@dataclass(frozen=True, slots=True)
class SacrificedCardTypes:
    """The paid permanent's card types while it was on the battlefield."""

    types: tuple[str, ...]

    def __post_init__(self):
        if (
            not isinstance(self.types, tuple)
            or any(type(kind) is not str or kind not in _SHARED_TYPES for kind in self.types)
            or tuple(sorted(set(self.types))) != self.types
            or not set(INITIAL_TYPES).intersection(self.types)
        ):
            raise ValueError("A shared sacrifice payment requires the full canonical paid card types")


def shared_type_sacrifice_effect(*, actor="$controller", paid: SacrificedCardTypes | None = None):
    effect = {
        "op": "choose_cards_apnap", "actor": actor,
        "players": [actor] if paid is None else "opponents",
        "zone": "battlefield", "count": 1, "then": "sacrifice",
        "predicate": ObjectQuerySpec(zones=("battlefield",), types_any=(
            INITIAL_TYPES if paid is None else paid.types
        )).to_dict(),
        "prompt": ("You may sacrifice one eligible permanent." if paid is None
                   else "You may sacrifice one permanent sharing a card type."),
        SHARED_TYPE_SACRIFICE: {
            "schema_version": 1, "phase": "first" if paid is None else "opponents",
            **({"paid_types": paid.types} if paid is not None else {}),
        },
    }
    if paid is not None:
        effect[DECLINED_LIFE_LOSS] = 2
    return effect


def validate_shared_type_sacrifice(effect, *, actor="$controller", initial_only=False):
    marker = effect.get(SHARED_TYPE_SACRIFICE)
    if not isinstance(marker, Mapping) or type(marker.get("schema_version")) is not int:
        raise ValueError("Shared sacrifice requires a versioned payment stage")
    paid = None
    if marker.get("phase") == "opponents":
        raw = marker.get("paid_types")
        if initial_only or not isinstance(raw, (list, tuple)):
            raise ValueError("Shared opponent sacrifices require their paid battlefield types")
        paid = SacrificedCardTypes(tuple(raw))
    if (
        type(effect.get("count")) is not int
        or paid is not None and type(effect.get(DECLINED_LIFE_LOSS)) is not int
        or FrozenMap(effect) != FrozenMap(shared_type_sacrifice_effect(actor=actor, paid=paid))
    ):
        raise ValueError("Shared sacrifice payment changed its typed scope or result")
    return paid


def shared_type_sacrifice_node_capabilities(effect, target_schema):
    if target_schema is not None:
        return ()
    try:
        validate_shared_type_sacrifice(effect, initial_only=True)
    except (TypeError, ValueError):
        return ()
    return _REQUIREMENTS


def shared_type_sacrifice_runtime_dependencies(program):
    marked = tuple(effect for effect in nested_effects((program.effects, program.handlers))
                   if SHARED_TYPE_SACRIFICE in effect)
    if not marked and SHARED_TYPE_SACRIFICE_CAPABILITY not in program.capability_dependencies:
        return ()
    if (
        len(marked) != 1 or tuple(program.effects) != marked
        or program.cost_schema is not None or program.target_schema is not None or program.handlers
        or (program.event, program.active_zone) != ("step.begin", "battlefield")
        or not isinstance(program.event_condition, Mapping)
        or FrozenMap(program.event_condition) != FrozenMap(controller_end_step_condition())
    ):
        raise ValueError("Shared sacrifice requires the complete untargeted controller's end-step trigger")
    required = shared_type_sacrifice_node_capabilities(marked[0], program.target_schema)
    if not required:
        raise ValueError("Shared sacrifice requires the original initial payment effect")
    return required


def is_closed_shared_type_sacrifice_program(program):
    try:
        required = shared_type_sacrifice_runtime_dependencies(program)
    except ValueError:
        return False
    return bool(required) and set(required).issubset(program.capability_dependencies)
