"""Return the live top creature and bind the actual entry to its delayed exile."""

from collections.abc import Mapping

from ..continuous_effect_state import ResolutionEffectSource
from ..effect_contracts import REANIMATE_OPERATION
from ..errors import GameRuleError
from .reanimation_followup import BattlefieldEntryFollowup, commit_battlefield_entry_followup


ORDERED_GRAVEYARD_REANIMATION_VERSION = 20
ORDERED_GRAVEYARD_REANIMATION_CAPABILITY = "zone.reanimate.ordered_creature_haste_and_departure"
ORDERED_GRAVEYARD_REANIMATION_MECHANIC = "ordered-graveyard-reanimation-haste-and-departure"

_SELECTION = "controller_graveyard_top_creature"
_FOLLOWUP = BattlefieldEntryFollowup("until_end_of_turn", "exile", "next")
_RUNTIME_FIELDS = {"_runtime_source", "_replacement_selections", "_aura_target_ref", "reason"}


def ordered_graveyard_reanimation_effect():
    return {
        "op": REANIMATE_OPERATION,
        "schema_version": ORDERED_GRAVEYARD_REANIMATION_VERSION,
        "controller": "$controller",
        "selection": _SELECTION,
        "tapped": False,
        "followup": _FOLLOWUP.to_dict(),
    }


def is_ordered_graveyard_reanimation(effect):
    return effect.get("op") == REANIMATE_OPERATION and (
        effect.get("schema_version") == ORDERED_GRAVEYARD_REANIMATION_VERSION
        or effect.get("selection") == _SELECTION
    )


def validate_ordered_graveyard_reanimation(effect, *, resolved=False):
    expected = ordered_graveyard_reanimation_effect()
    fields = set(expected)
    allowed = fields | (_RUNTIME_FIELDS if resolved else set())
    if (
        not isinstance(effect, Mapping) or not fields <= set(effect) <= allowed
        or effect["op"] != REANIMATE_OPERATION
        or type(effect["schema_version"]) is not int
        or effect["schema_version"] != ORDERED_GRAVEYARD_REANIMATION_VERSION
        or effect["selection"] != _SELECTION or effect["tapped"] is not False
    ):
        raise ValueError("Ordered reanimation requires its exact untapped top creature instruction")
    controller = effect["controller"]
    if resolved:
        if type(controller) is not str or not controller or controller.startswith("$"):
            raise ValueError("Ordered reanimation requires the resolving controller's graveyard")
    elif controller != expected["controller"]:
        raise ValueError("Ordered reanimation requires the resolving controller's graveyard")
    if not isinstance(effect.get("_replacement_selections", ()), (list, tuple)):
        raise ValueError("Ordered reanimation replacements must be an ordered sequence")
    attachment = effect.get("_aura_target_ref")
    if attachment is not None and (type(attachment) is not str or not attachment):
        raise ValueError("Ordered reanimation attachment is malformed")
    followup = BattlefieldEntryFollowup.from_dict(effect["followup"])
    if followup != _FOLLOWUP:
        raise ValueError("Ordered reanimation requires temporary haste and the next end step exile")
    return followup


def _current_top_creature(host, controller):
    owner_zone = host.state.players[controller].zones["graveyard"]
    for object_id in reversed(owner_zone):
        card = host.state.cards.get(object_id)
        if card is None or card.zone != "graveyard" or card.owner != controller:
            raise GameRuleError("Ordered reanimation encountered invalid graveyard membership")
        if not card.is_card_object:
            continue
        types, _, _ = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        if "creature" in types:
            return card
    return None


def apply_ordered_graveyard_reanimation(host, effect, *, actor, reason):
    try:
        followup = validate_ordered_graveyard_reanimation(effect, resolved=True)
        source = ResolutionEffectSource.from_effect(effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = host.state.stack[-1] if host.state.stack else None
    if (
        effect["controller"] != actor or item is None or item.ref != source.stack_ref
        or item.controller != actor or item.kind not in {"spell", "spell_copy"}
    ):
        raise GameRuleError("Ordered reanimation requires its own resolving spell and controller")
    host._require_seat(actor, in_game=True)
    card = _current_top_creature(host, actor)
    entered = None
    previous_zone_counter = None
    if card is not None:
        previous_zone_counter = card.zone_change_counter
        entered = host.move_card(
            card.object_id, "battlefield", controller=actor, putting_player=actor, tapped=False,
            reason=reason, semantic_events=True, replacement_selections=tuple(effect.get("_replacement_selections", ())),
            aura_target_ref=effect.get("_aura_target_ref"),
        )
    commit_battlefield_entry_followup(
        host, entered, previous_zone_counter=previous_zone_counter, actor=actor, source=source,
        followup=followup, schedule_without_arrival=True,
    )
    return entered
