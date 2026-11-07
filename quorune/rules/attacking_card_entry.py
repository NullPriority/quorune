from __future__ import annotations

"""Closed selected-card entry and its independently chosen combat recipient."""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from ..errors import GameRuleError


ATTACKING_CARD_ENTRY_CAPABILITY = "combat.entry.selected_card_attacking"
ATTACKING_HAND_ENTRY_VERSION = 3
SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC = "source-graveyard-attacking-entry"
SOURCE_GRAVEYARD_IDENTITY = "$context.source_logical_object_id"
_MOVE_FIELDS = {"op", "card", "from", "destination", "controller", "tapped", "attacking_entry"}
_RUNTIME_FIELDS = {"_replacement_selections", "_runtime_source", "_aura_target_ref"}


@dataclass(frozen=True, slots=True)
class AttackingEntryRecipient:
    reference: str
    logical_object_id: str | None

    def __post_init__(self):
        if type(self.reference) is not str or not self.reference:
            raise ValueError("An attacking entry requires a defending recipient reference")
        if self.logical_object_id is not None and (type(self.logical_object_id) is not str or not self.logical_object_id):
            raise ValueError("A permanent defending recipient requires its logical identity")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, Mapping) or set(value) != {"reference", "logical_object_id"}:
            raise ValueError("An attacking entry recipient has closed identity fields")
        return cls(**value)


def decode_hand_attacking_entry(effect):
    if "attacking_entry" not in effect and effect.get("schema_version") != ATTACKING_HAND_ENTRY_VERSION:
        return False
    if (type(effect.get("schema_version")) is not int or effect["schema_version"] != ATTACKING_HAND_ENTRY_VERSION
        or effect.get("attacking_entry") is not True or effect.get("tapped") is not True or "followup" in effect):
        raise ValueError("An attacking hand entry requires its complete tapped version-three instruction")
    validate_attacking_hand_query(effect.get("query"))
    return True


def validate_attacking_hand_query(query):
    from ..hand_entry_queries import decode_hand_entry_queries

    if not all("creature" in spec.types_all for spec in decode_hand_entry_queries(query)):
        raise ValueError("A private attacking entry must select a creature card")


def attacking_entry_choice(move):
    return {"op": "choose_option", "player": move["controller"],
        "attacking_card_entry": {**move, "attacking_entry": {"schema_version": 1}}}


def validate_attacking_entry_move(move, *, actor, compiled=False, selected=False):
    if not isinstance(move, Mapping):
        raise ValueError("Attacking entry requires a closed move")
    origin = move.get("from")
    source_bound = "source_identity" in move
    fields = _MOVE_FIELDS
    if source_bound:
        fields = fields | {"source_identity"}
        if "entry_counter" in move:
            fields = fields | {"entry_counter", "entry_source"}
    elif not compiled and origin == "hand":
        fields = fields | {"hand_entry_query", "expected_object_identity"}
    elif not compiled and origin == "graveyard" and move.get("card") is not None:
        fields = fields | {"target_snapshot"}
    allowed = fields if compiled or not selected else fields | _RUNTIME_FIELDS
    if (not fields <= set(move) <= allowed or origin not in ("hand", "graveyard")
        or compiled and origin != "graveyard" or move["op"] != "move"
        or move["destination"] != "battlefield" or move["controller"] != actor or move["tapped"] is not True
        or not isinstance(move.get("_replacement_selections", ()), (list, tuple))):
        raise ValueError("Attacking entry requires a tapped own-hand or graveyard battlefield move")
    card = move["card"]
    reference = "$source" if source_bound else "$target.0"
    if (compiled and card != reference or not compiled and card is not None and (type(card) is not str or not card)
        or origin == "hand" and card is None):
        raise ValueError("Attacking entry is missing its selected card")
    if source_bound:
        identity = move["source_identity"]
        if (origin != "graveyard" or type(identity) is not str or not identity
            or compiled and identity != SOURCE_GRAVEYARD_IDENTITY):
            raise ValueError("Attacking graveyard source requires its sealed source identity")
        if "entry_counter" in move and (move["entry_counter"] != "+1/+1"
            or type(move["entry_source"]) is not str or not move["entry_source"]
            or compiled and move["entry_source"] != "$stack"):
            raise ValueError("Attacking graveyard source requires a closed entry counter")
    if not compiled and origin == "hand":
        validate_attacking_hand_query(move["hand_entry_query"])
        if type(move["expected_object_identity"]) is not str or not move["expected_object_identity"]:
            raise ValueError("Attacking hand entry requires its selected hand incarnation")
    if not compiled and not source_bound and origin == "graveyard" and card is not None:
        subject = move["target_snapshot"]
        if (not isinstance(subject, Mapping) or set(subject) != {"ref", "object_id", "zone_change_counter"}
            or subject["ref"] != card or type(subject["object_id"]) is not str or not subject["object_id"]
            or type(subject["zone_change_counter"]) is not int or subject["zone_change_counter"] < 0):
            raise ValueError("Attacking entry requires its declared graveyard incarnation")
    entry = move["attacking_entry"]
    if (not isinstance(entry, Mapping) or set(entry) != ({"schema_version", "recipient"} if selected else {"schema_version"})
        or type(entry["schema_version"]) is not int or entry["schema_version"] != 1):
        raise ValueError("Attacking entry requires its closed version-one destination")
    if selected and entry["recipient"] is not None:
        return AttackingEntryRecipient.from_dict(entry["recipient"])
    return None


def validate_attacking_entry_choice(effect, *, actor, compiled=False):
    fields = {"op", "player", "attacking_card_entry"}
    allowed = fields if compiled else fields | {"_replacement_selections"}
    if (not isinstance(effect, Mapping) or not fields <= set(effect) <= allowed
        or effect["op"] != "choose_option" or effect["player"] != actor
        or not isinstance(effect.get("_replacement_selections", ()), (list, tuple))):
        raise ValueError("Attacking entry choice has an invalid player or payload")
    move = effect["attacking_card_entry"]
    validate_attacking_entry_move(move, actor=actor, compiled=compiled)
    return move


def selected_attacking_entry_card(host, move, *, actor):
    if move["from"] == "hand":
        from ..hand_entry_move import resolve_selected_hand_entry_card

        return resolve_selected_hand_entry_card(host,
            {key: value for key, value in move.items() if key != "attacking_entry"}, actor=actor)
    if move["card"] is None:
        return None
    if "source_identity" in move:
        try:
            source = host._resolve_object(actor, move["card"], zones=("graveyard",), owned_only=True)
        except GameRuleError:
            return None
        return source if source.logical_object_id == move["source_identity"] else None
    from .reanimation_followup import current_linked_subject

    return current_linked_subject(host, move["target_snapshot"], zone="graveyard")


def graveyard_attacking_entry_sources(host, actor):
    """Collect the owner's graveyard cards with a registered attack return."""
    oracle_ids = {
        program.oracle_id for program in host.semantics.programs_for_event("creature.attacks")
        if program.active_zone == "graveyard" and SOURCE_GRAVEYARD_ATTACKING_ENTRY_MECHANIC in program.coverage
    }
    if not oracle_ids:
        return ()
    cards = host.state.cards
    return tuple(cards[object_id] for object_id in host.state.players[actor].zones["graveyard"]
        if cards[object_id].oracle_id in oracle_ids)


def attacking_entry_counters(move, actor):
    if "entry_counter" not in move:
        return ()
    from ..entry_counter_model import EffectEntryCounter

    return (EffectEntryCounter(move["entry_counter"], 1, actor, move["entry_source"], "122.6a"),)


def is_attacking_player(host, actor):
    return (actor in host.active_seats and host.state.phase == "combat" and host.state.combat is not None
        and host.state.active_player == actor)


def attacking_card_entry_choice_facts(host, actor, effect):
    if "attacking_card_entry" not in effect:
        return {}
    from ..entry_counters import capture_prospective_entry_characteristics
    from ..semantic_runtime.zone_replacements import prepare_zone_change_replacement

    try:
        move = validate_attacking_entry_choice(effect, actor=actor)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    card = selected_attacking_entry_card(host, move, actor=actor)
    if card is None:
        return {"attacking_card_entry_status": "missing"}
    if not is_attacking_player(host, actor):
        return {"attacking_card_entry_status": "ordinary"}
    characteristics, type_line = capture_prospective_entry_characteristics(host, card=card, enter_face=None)
    preview = prepare_zone_change_replacement(host, card, "battlefield", destination_controller=actor,
        entry_characteristics=characteristics, requested_tapped=True, error_type=GameRuleError,
        selections=tuple(effect.get("_replacement_selections", ())),
        effect_entry_counters=attacking_entry_counters(move, actor))
    copied = preview.event.payload.get("entry_copy") or {}
    current_type = str(copied.get("type_line", type_line))
    can_attack = (preview.destination == "battlefield" and preview.destination_controller == actor
        and "creature" in host._type_parts(current_type)[0])
    return {"attacking_card_entry_status": "attacking" if can_attack else "ordinary"}


def apply_attacking_entry_move(host, effect, *, actor, reason):
    from ..zone_transitions import ZoneTransitionOwner

    try:
        recipient = validate_attacking_entry_move(effect, actor=actor, selected=True)
    except ValueError as exc:
        raise GameRuleError(str(exc)) from exc
    card = selected_attacking_entry_card(host, effect, actor=actor)
    if card is None:
        return None
    attacking_entries = {card.object_id: recipient} if recipient is not None else None
    counters = attacking_entry_counters(effect, actor)
    ZoneTransitionOwner(host).move_cards_simultaneously(((card.object_id, "battlefield"),),
        reason=reason, log=True, tapped=True, destination_controllers={card.object_id: actor},
        aura_target_refs={card.object_id: effect["_aura_target_ref"]} if effect.get("_aura_target_ref") is not None else None,
        replacement_selections=tuple(effect.get("_replacement_selections", ())), attacking_entries=attacking_entries,
        effect_entry_counters={card.object_id: counters} if counters else None)
    return card


def commit_attacking_entry(host, card, recipient):
    from ..combat_relationship_state import AttackDeclarationAssignment, commit_attack_declaration

    controller = card.controller
    if (card.zone != "battlefield" or card.phased_out or not is_attacking_player(host, controller)
        or "creature" not in host._type_parts(str(host._effective_card_data(card)["type_line"]))[0]):
        return
    target = host._attack_target_details(controller, recipient.reference)
    if (target is None or target.get("logical_object_id") != recipient.logical_object_id):
        return
    assignment = AttackDeclarationAssignment(attacker_object_id=card.object_id, target=target["target"],
        target_kind=target["kind"], defending_player=target["defending_player"],
        target_logical_object_id=recipient.logical_object_id)
    commit_attack_declaration(host.state.combat, host.state.cards, controller=controller,
        assignments=(assignment,), preserve_bands=True)
