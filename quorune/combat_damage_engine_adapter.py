from __future__ import annotations

from typing import TYPE_CHECKING, Sequence
from .as_unblocked import can_assign_as_unblocked
from .as_unblocked_rule import active_as_unblocked_rule

from .combat_damage_snapshot import (
    CombatDamageParticipant,
    CombatDamageRecipient,
    CombatDamageSnapshotError,
)

if TYPE_CHECKING:
    from .engine import CommanderEngine


class EngineCombatDamageQuery:
    """Narrow compatibility adapter over authoritative CommanderEngine state."""

    def __init__(self, engine: CommanderEngine) -> None:
        self._engine = engine

    def damage_step_identity(self) -> str:
        state = self._engine.state
        sequence_id = state.combat.damage_sequence_id
        if sequence_id is not None:
            return f"{sequence_id}:step:{state.combat.damage_step_index}"
        # Compatibility for historical/manual states which reached combat
        # damage before the additive sequence identity existed.
        return (
            f"combat-damage:legacy:{state.turn_sequence}:"
            f"{state.combat.damage_step_index}:"
            f"{int(state.combat.first_strike_step)}"
        )

    def damage_step_index(self) -> int:
        return self._engine.state.combat.damage_step_index

    def first_strike_step(self) -> bool:
        return self._engine.state.combat.first_strike_step

    def active_player(self) -> str:
        active = self._engine.state.active_player
        if active is None:
            raise CombatDamageSnapshotError(
                "Combat damage requires an active player"
            )
        return active

    def participant_object_ids(self) -> Sequence[str]:
        combat = self._engine.state.combat
        object_ids = set(combat.attackers)
        for attacker_id in combat.attackers:
            object_ids.update(combat.blockers.get(attacker_id, ()))
        return tuple(sorted(object_ids))

    def participant(self, object_id: str) -> CombatDamageParticipant:
        engine = self._engine
        card = engine.state.cards.get(object_id)
        if card is None:
            raise CombatDamageSnapshotError(
                f"Unknown combat participant {object_id}"
            )
        if card.zone != "battlefield" or card.phased_out:
            raise CombatDamageSnapshotError(
                f"Combat participant {card.ref} is not on the battlefield"
            )
        data = engine._effective_card_data(card)
        card_types, _, _ = engine._type_parts(str(data.get("type_line") or ""))
        if "creature" not in card_types or "battle" in card_types:
            raise CombatDamageSnapshotError(
                f"Combat participant {card.ref} is not a creature"
            )
        return CombatDamageParticipant(
            object_id=card.object_id,
            logical_object_id=card.logical_object_id,
            reference=card.ref,
            controller=card.controller,
            power=engine._numeric_stat(card.object_id, "power"),
            toughness=engine._numeric_stat(card.object_id, "toughness"),
            marked_damage=card.marked_damage,
            keywords=engine._combat_keywords(card),
            assigns_damage=engine._assigns_combat_damage_this_step(card),
            assign_as_unblocked=can_assign_as_unblocked(engine._effective_ability_fragments(card,error_type=CombatDamageSnapshotError)),
            must_assign_as_unblocked=active_as_unblocked_rule(engine.state,card),
        )

    def attacker_object_ids(self) -> Sequence[str]:
        return tuple(self._engine.state.combat.attackers)

    def attack_recipient(self, attacker_object_id: str) -> CombatDamageRecipient:
        engine = self._engine
        combat = engine.state.combat
        target = combat.attackers.get(attacker_object_id)
        if not isinstance(target, str) or not target:
            raise CombatDamageSnapshotError(
                f"Attacker {attacker_object_id} has no attacked recipient"
            )
        context = combat.attack_target_context.get(attacker_object_id)
        if context is None:
            context = engine._attack_target_details(engine.state.active_player, target)
        if not isinstance(context, dict):
            raise CombatDamageSnapshotError(
                f"Attacker {attacker_object_id} has no recipient context"
            )
        kind = context.get("kind")
        defender = context.get("defending_player")
        if not isinstance(kind, str) or not isinstance(defender, str):
            raise CombatDamageSnapshotError(
                f"Attacker {attacker_object_id} has malformed recipient context"
            )
        if context.get("target") != target:
            raise CombatDamageSnapshotError(
                f"Attacker {attacker_object_id} recipient context is stale"
            )
        if kind == "player":
            return CombatDamageRecipient(
                reference=target,
                logical_object_id=f"player:{target}",
                controller=defender,
                kind=kind,
                legal=engine._combat_damage_target_exists(
                    target, attacker_id=attacker_object_id
                ),
            )
        card = next(
            (value for value in engine.state.cards.values() if value.ref == target),
            None,
        )
        if card is None:
            raise CombatDamageSnapshotError(
                f"Attacked permanent {target} has no physical identity"
            )
        return CombatDamageRecipient(
            reference=target,
            object_id=card.object_id,
            logical_object_id=str(
                context.get("logical_object_id") or card.logical_object_id
            ),
            controller=card.controller,
            kind=kind,
            legal=engine._combat_damage_target_exists(
                target, attacker_id=attacker_object_id
            ),
        )

    def blocker_object_ids(self, attacker_object_id: str) -> Sequence[str]:
        return tuple(
            self._engine.state.combat.blockers.get(attacker_object_id, ())
        )

    def was_blocked(self, attacker_object_id: str) -> bool:
        return attacker_object_id in self._engine.state.combat.blockers


__all__ = ["EngineCombatDamageQuery"]
