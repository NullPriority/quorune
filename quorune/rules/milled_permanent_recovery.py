from __future__ import annotations

"""Offer one permanent card from the committed public results of a paid Mill."""

from collections.abc import Mapping
from dataclasses import dataclass

from ..continuous_effect_model import Layer
from ..errors import GameRuleError
from ..external_entry_state_model import PERMANENT_CARD_TYPES
from ..object_predicate import ObjectQuerySpec
from ..replacement.immutable import FrozenMap
from .committed_quantity_followups import CommittedQuantityFollowup

CAPABILITY = "zone.mill.food_paid_public_permanent_recovery"
RECOVERY_FIELD = "public_permanent_recovery_version"
CHOICE_FIELD = "same_mill_permanent_choice_version"
ARRIVALS_FIELD = "committed_milled_permanent_arrivals"
CONDITION = {"field": "card", "op": "eq", "value": "$source.ref"}
_TEXT = (
    "you may sacrifice a food. if you do, mill three cards. "
    "you may put a permanent card from among them into your hand."
)


def _mill_effect(player="$controller"):
    return {"op": "mill", "player": player, "count": 3, "followup": {RECOVERY_FIELD: 1}}


def _paid_mill_effect():
    from ..fixed_effect_payment import FixedEffectPaymentSpec

    payment = FixedEffectPaymentSpec("sacrifice", schema_version=2, predicate=ObjectQuerySpec(
        zones=("battlefield",), controller="$controller", types_all=("artifact",),
        subtypes_all=("food",), known_to_actor=True,
    ))
    return {"op": "offer_optional_mana_payment", "schema_version": 2, "player": "$controller",
            "payment": payment.to_dict(), "effects": [_mill_effect()]}


def food_paid_mill_result(binding, body):
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC

    if ((binding.event.value, binding.active_zone, binding.variant) != ("creature.attacks", "battlefield", "source_attacks")
            or FrozenMap(binding.event_condition or {}) != FrozenMap(CONDITION)
            or body != binding.body or " ".join(body.casefold().split()) != _TEXT):
        return None
    return "food-paid-mill-public-permanent-v1", (_paid_mill_effect(),), None, (FIXED_EFFECT_PAYMENT_MECHANIC, "mill")


def uses_milled_permanent_recovery(effect):
    followup = effect.get("followup")
    return effect.get("op") == "mill" and isinstance(followup, Mapping) and RECOVERY_FIELD in followup


def validate_recovery_mill(effect, *, actor=None):
    metadata = {"reason", "_replacement_selections"} if actor is not None else set()
    expected = _mill_effect(player=actor if actor is not None else "$controller")
    if (not uses_milled_permanent_recovery(effect)
            or FrozenMap({key: value for key, value in effect.items() if key not in metadata}) != FrozenMap(expected)
            or type(effect["count"]) is not int or type(effect["followup"][RECOVERY_FIELD]) is not int):
        raise ValueError("Permanent recovery requires the exact three-card Mill for the resolving controller")


def milled_permanent_recovery_node_capabilities(effect, target_schema):
    if target_schema is not None:
        return ()
    try:
        validate_recovery_mill(effect)
    except (KeyError, TypeError, ValueError):
        return ()
    return CAPABILITY, "zone.mill.fixed", "zone.change.destination_replacement"


def milled_permanent_recovery_runtime_dependencies(program):
    from .effect_shape_walkers import contains_nested_key
    from ..fixed_effect_payment import FIXED_EFFECT_PAYMENT_MECHANIC

    contents = (program.effects, program.target_schema, program.handlers)
    if contains_nested_key(contents, CHOICE_FIELD):
        raise ValueError("The same-Mill selection must be emitted by committed Mill")
    if CAPABILITY not in program.capability_dependencies and not contains_nested_key(contents, RECOVERY_FIELD):
        return ()
    expected = FrozenMap(_paid_mill_effect())
    if (tuple(FrozenMap(effect) for effect in program.effects) != (expected,) or program.target_schema is not None
            or (program.event, program.active_zone) != ("creature.attacks", "battlefield")
            or FrozenMap(program.event_condition or {}) != FrozenMap(CONDITION)
            or program.cost_schema is not None or program.handlers
            or not {FIXED_EFFECT_PAYMENT_MECHANIC, "mill"} <= set(program.coverage)):
        raise ValueError("Same-Mill recovery requires the complete source attack and paid Food wrapper")
    wrapper, = program.effects
    if type(wrapper["schema_version"]) is not int or type(wrapper["payment"]["schema_version"]) is not int:
        raise ValueError("Food payment requires its exact descriptor version")
    validate_recovery_mill(wrapper["effects"][0])
    return milled_permanent_recovery_node_capabilities(wrapper["effects"][0], None)


def _choice(actor):
    return {"op": "choose_objects", "player": actor, CHOICE_FIELD: 1, "minimum": 0, "maximum": 1,
            "prompt": "You may put a permanent card from among the milled cards into your hand.",
            "then": [{"op": "move_selected", "destination": "hand"}]}


def validate_milled_permanent_choice(effect, actor):
    transient = {"_choice_actor", "_legal_refs", "_minimum", "_maximum", "_stack_label"}
    public = {key: value for key, value in effect.items() if key not in transient}
    if (FrozenMap(public) != FrozenMap(_choice(actor))
            or any(type(effect.get(field)) is not int for field in (CHOICE_FIELD, "minimum", "maximum"))):
        raise ValueError("The public Mill choice requires its original optional hand move")


def _current_public_permanent(host, actor, object_id, ref, zone, logical_id):
    from ..zone_transition_model import PUBLIC_ZONES

    card = host.state.cards.get(object_id)
    if (card is None or not card.is_card_object or card.face_down or card.owner != actor
            or card.ref != ref or zone not in PUBLIC_ZONES or card.zone != zone or card.logical_object_id != logical_id):
        return None
    types, _, _ = host._type_parts(host._effective_card_data(card, maximum_layer=Layer.TYPE)["type_line"])
    return card if types & PERMANENT_CARD_TYPES else None


def _require_resolving_attack(host, item, actor):
    if (item is None or item.controller != actor or item.kind != "triggered_ability"
            or item.context.get("event") != "creature.attacks" or item.context.get("currently_resolving") is not True):
        raise ValueError("The paid Mill requires its original resolving attack")
    program = host.semantics.get(item.semantic_key)
    if (program is None or not host.semantic_program_is_current_trusted(program)
            or not milled_permanent_recovery_runtime_dependencies(program)):
        raise ValueError("The paid Mill requires its closed attack program")


@dataclass(frozen=True, slots=True)
class MilledPermanentRecovery:
    stack_ref: str

    def __post_init__(self):
        if type(self.stack_ref) is not str or not self.stack_ref:
            raise ValueError("Committed permanent recovery requires the original stack reference")

    def from_committed_result(self, host, result):
        from ..milling import MillResult

        try:
            if not isinstance(result, MillResult) or result.requested_count != 3:
                raise ValueError("Permanent recovery requires its exact committed Mill")
            item = next((row for row in host.state.stack if row.ref == self.stack_ref), None)
            _require_resolving_attack(host, item, result.player)
            if ARRIVALS_FIELD in item.context:
                raise ValueError("The attack has already recorded its Mill")
            arrivals = []
            for row in result.cards:
                identity = row.identity
                fields = (identity.object_id, identity.ref, row.destination, row.destination_logical_object_id)
                if _current_public_permanent(host, result.player, *fields) is not None:
                    arrivals.append(list(fields))
            item.context[ARRIVALS_FIELD] = arrivals
            return CommittedQuantityFollowup((FrozenMap(_choice(result.player)),) if arrivals else ())
        except (KeyError, TypeError, ValueError) as exc:
            raise GameRuleError(str(exc)) from exc


def milled_permanent_choice_refs(host, item, actor, effect):
    validate_milled_permanent_choice(effect, actor)
    _require_resolving_attack(host, item, actor)
    arrivals = item.context.get(ARRIVALS_FIELD)
    if not isinstance(arrivals, (tuple, list)) or not 1 <= len(arrivals) <= 3:
        raise ValueError("The same-Mill choice lost its committed public arrivals")
    refs = []
    for row in arrivals:
        if not isinstance(row, (tuple, list)) or len(row) != 4 or any(type(value) is not str or not value for value in row):
            raise ValueError("The same-Mill choice requires exact arrival identities")
        if _current_public_permanent(host, actor, *row) is not None:
            refs.append(row[1])
    if len(set(refs)) != len(refs):
        raise ValueError("The same-Mill choice cannot repeat a physical arrival")
    return tuple(refs)
