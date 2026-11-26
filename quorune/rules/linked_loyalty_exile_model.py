"""Closed descriptors for a linked set of ordinary loyalty results."""

from collections.abc import Mapping

from ..linked_exile_return_model import ExiledObjectIdentity


LOYALTY_X_FIELD = "loyalty_x"
LOYALTY_X_CAPABILITY = "activation.loyalty.announced_x"
LINKED_LOYALTY_EXILE_FIELD = "linked_loyalty_exile"
LINKED_LOYALTY_EXILE_PAIR = "loyalty-linked-exile-entry"
LINKED_LOYALTY_CHOICE_OFFERED = "_linked_loyalty_choice_offered"
LINKED_LOYALTY_TOP_EXILE_CAPABILITY = "zone.exile.linked_loyalty_opponent_top_three"
LINKED_LOYALTY_OPPONENT_ZONES_CAPABILITY = "zone.exile.linked_loyalty_opponent_hand_graveyard"
LINKED_LOYALTY_CREATURE_ENTRY_CAPABILITY = "zone.move.linked_loyalty_creature_entry"


def is_loyalty_x_cost(text: str) -> bool:
    return text in ("-X", "\u2212X")


def validate_loyalty_x_ability(ability) -> None:
    if type(ability.loyalty_x) is not bool:
        raise ValueError("The variable loyalty marker must be boolean")
    if not ability.loyalty_x:
        return
    if (
        not is_loyalty_x_cost(ability.cost_text) or type(ability.loyalty_delta) is not int
        or ability.loyalty_delta != 0 or ability.zones != ("battlefield",) or ability.mana_ability
        or any(ability.mana.values()) or any((
            ability.complex_symbols, ability.mana_cost_options, ability.choices,
            ability.tap_source, ability.untap_source, ability.discard_source, ability.sacrifice_source,
            ability.exile_source, ability.life_payment, ability.energy_payment, ability.waterbend,
            ability.source_counter_placement_cost, ability.source_counter_removal_cost,
            ability.random_discard_payment, ability.mill_library_cost,
            ability.announced_x_symbols, ability.variable_colored_reveal_price,
        ))
    ):
        raise ValueError("Variable loyalty requires an exact single battlefield minus-X cost")


def linked_loyalty_exile_instruction(phase: str) -> dict:
    if phase not in {"target_top", "opponent_zones", "choose"}:
        raise ValueError("Unsupported linked loyalty result phase")
    effect = {
        "op": "choose_objects" if phase == "choose" else "linked_exile_return",
        "schema_version": 1,
        LINKED_LOYALTY_EXILE_FIELD: phase,
    }
    if phase == "target_top":
        effect["player"] = "$target.0"
    return effect


def validate_linked_loyalty_exile_instruction(effect, *, actor=None) -> str:
    if not isinstance(effect, Mapping) or type(effect.get("schema_version")) is not int:
        raise ValueError("Linked loyalty requires a closed versioned instruction")
    phase = effect.get(LINKED_LOYALTY_EXILE_FIELD)
    if type(phase) is not str:
        raise ValueError("Linked loyalty requires a typed resolution phase")
    expected = linked_loyalty_exile_instruction(phase)
    if phase == "target_top" and actor is not None:
        player = effect.get("player")
        if (type(actor) is not str or not actor or actor.startswith("$")
                or type(player) is not str or not player or player.startswith("$") or actor == player):
            raise ValueError("Linked top exile requires the resolving target opponent")
        expected["player"] = player
    if dict(effect) != expected:
        raise ValueError("Linked loyalty requires its exact target and resolution phase")
    return phase


def linked_loyalty_entry_instruction(identity: ExiledObjectIdentity) -> dict:
    if not isinstance(identity, ExiledObjectIdentity):
        raise ValueError("Linked loyalty entry requires the selected exile incarnation")
    return {
        "op": "linked_exile_return",
        "schema_version": 1,
        LINKED_LOYALTY_EXILE_FIELD: "enter",
        "chosen": identity.to_dict(),
    }


def linked_loyalty_entry_identity(instruction) -> ExiledObjectIdentity:
    if not isinstance(instruction, Mapping) or type(instruction.get("schema_version")) is not int:
        raise ValueError("Linked loyalty entry requires a closed versioned instruction")
    identity = ExiledObjectIdentity.from_dict(instruction.get("chosen"))
    if dict(instruction) != linked_loyalty_entry_instruction(identity):
        raise ValueError("Linked loyalty entry differs from its selected exile incarnation")
    return identity
