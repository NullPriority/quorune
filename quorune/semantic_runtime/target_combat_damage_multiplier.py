"""Resolve a creature-specific combat multiplier through ordinary damage replacement."""

from dataclasses import dataclass

from ..continuous_effect_model import ContinuousEffectDuration, ContinuousObjectIdentity
from ..continuous_effect_state import commit_continuous_effect
from ..replacement import MultiplyAmount, ReplacementClass, ReplacementEffect
from ..target_combat_damage_multiplier_model import (
    MULTIPLIER_CAPABILITY, MULTIPLIER_OPERATION, ResolvedTargetCombatDamageMultiplier,
    TURN_MULTIPLIER_CAPABILITY, TargetCombatDamageMultiplierIntent, turn_multiplier_instruction,
)
from .context import ReadOnlyHandlerContext, SemanticNodeError
from .intents import IntentPlan


@dataclass(frozen=True, slots=True)
class TargetCombatDamageMultiplierHandler:
    operation: str = MULTIPLIER_OPERATION
    handler_id: str = "generic.target-combat-damage-multiplier.v1"
    schema_version: int = 1
    family: str = MULTIPLIER_CAPABILITY
    rule_references: tuple[str, ...] = ("120.2a", "611.2a", "614.1a", "614.5", "616.1", "800.4m")
    capability_dependencies: tuple[str, ...] = (MULTIPLIER_CAPABILITY,)

    def lower(self, effect, context: ReadOnlyHandlerContext):
        version = effect.get("schema_version")
        legacy = (version == self.schema_version and set(effect) == {"op", "schema_version", "card"}
                  and type(effect.get("card")) is str)
        turn_scope = version == 2 and effect == turn_multiplier_instruction()
        if (not (legacy or turn_scope) or effect.get("op") != self.operation
                or type(version) is not int or context.source is None):
            raise SemanticNodeError("Combat damage multiplier requires its owned resolving source")
        try:
            intent = TargetCombatDamageMultiplierIntent(context.actor, effect.get("card"), context.source.stack_ref)
        except ValueError as exc:
            raise SemanticNodeError(str(exc)) from exc
        return IntentPlan(operation=self.operation, handler_id=self.handler_id, intents=(intent,))


def apply_target_combat_damage_multiplier(host, intent):
    host._require_seat(intent.actor, in_game=True)
    from ..rules.loyalty_damage import loyalty_damage_runtime_dependencies
    if intent.card is None:
        item = next((row for row in host.state.stack if row.ref == intent.source_id
                     and row.controller == intent.actor and row.kind in {"spell", "spell_copy"}
                     and row.context.get("currently_resolving") is True), None)
        program = host.semantics.get(item.semantic_key) if item is not None else None
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or TURN_MULTIPLIER_CAPABILITY not in loyalty_damage_runtime_dependencies(program)):
            raise SemanticNodeError("Turn combat damage multiplier requires its admitted resolving spell")
        effect = ResolvedTargetCombatDamageMultiplier(
            host._next_ref("CDM"), intent.source_id, host._next_zone_timestamp(), None, None,
            ContinuousEffectDuration.UNTIL_END_OF_TURN,
        )
        commit_continuous_effect(host.state, effect)
        host._log(intent.actor, "damage.multiplier.created", "Creature combat damage to creatures will be doubled.",
                  {"effect_id": effect.effect_id}, importance=1)
        return effect.effect_id

    item = next((item for item in host.state.stack if item.ref == intent.source_id
                 and item.controller == intent.actor and item.kind in {"activated_ability", "activated_ability_copy"}
                 and item.context.get("currently_resolving") is True), None)
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.context.get("targets_revalidated") is not True or tuple(item.targets) != (intent.card,)
            or program is None or not host.semantic_program_is_current_trusted(program)
            or MULTIPLIER_CAPABILITY not in loyalty_damage_runtime_dependencies(program)):
        raise SemanticNodeError("Combat damage multiplier requires its admitted resolving activation and target")
    card = next((card for card in host.state.cards.values() if card.ref == intent.card), None)
    if card is None or card.zone != "battlefield" or card.phased_out:
        raise SemanticNodeError("Combat damage multiplier requires the surviving battlefield target")
    types = host._type_parts(str(host._effective_card_data(card).get("type_line") or ""))[0]
    if "creature" not in types:
        raise SemanticNodeError("The multiplier target must still be a creature")
    effect = ResolvedTargetCombatDamageMultiplier(
        host._next_ref("CDM"), intent.source_id, host._next_zone_timestamp(), intent.actor,
        ContinuousObjectIdentity(card.object_id, card.logical_object_id),
    )
    commit_continuous_effect(host.state, effect)
    host._log(intent.actor, "damage.multiplier.created", "A creature's combat damage will be tripled.",
              {"effect_id": effect.effect_id, "card": card.ref}, importance=1)
    return effect.effect_id


def _creature_combat_multiplier(effect):
    return ReplacementEffect(
        effect_id=f"combat-damage-multiplier:{effect.effect_id}",
        source_id=effect.source_id,
        event_kind="damage",
        replacement_class=ReplacementClass.OTHER,
        conditions={"combat": {"eq": True}, "source_types": {"contains_all": ["creature"]},
                    "target_types": {"contains_all": ["creature"]}, "amount": {"not_in": [0]}},
        operations=(MultiplyAmount("amount", 2),),
        label="Double combat damage dealt by a creature to a creature",
    )


def collect_target_combat_damage_multipliers(host):
    return tuple(_creature_combat_multiplier(effect) if effect.target is None else
        ReplacementEffect(
            effect_id=f"combat-damage-multiplier:{effect.effect_id}",
            source_id=effect.source_id,
            event_kind="damage",
            replacement_class=ReplacementClass.OTHER,
            conditions={
                "source_object_id": {"eq": effect.target.object_id},
                "source_logical_object_id": {"eq": effect.target.logical_object_id},
                "combat": {"eq": True}, "target_kind": {"eq": "player"},
                "target": {"not_in": [effect.expires_on_next_turn_of]}, "amount": {"not_in": [0]},
            },
            operations=(MultiplyAmount("amount", 3),),
            label="Triple the creature's combat damage",
        )
        for effect in host.state.continuous_effects or ()
        if isinstance(effect, ResolvedTargetCombatDamageMultiplier)
    )
