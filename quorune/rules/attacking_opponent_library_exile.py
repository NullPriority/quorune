"""Read the actual declaration for a defending player's top-exile trigger."""

from ..ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from ..attack_transition_model import AttackRecipientKind, AttackTransitionEvent
from ..errors import GameRuleError


ATTACKED_PLAYER_OR_WALKER_EVENT = "combat.player_or_planeswalker_attacked"
ATTACKED_PLAYER_OR_WALKER_VARIANT = "opponent_attacks_controller_or_their_planeswalker"


def attack_top_exile_condition():
    return {"field": "defender", "op": "eq", "value": "$source.controller"}


def player_or_walker_defenders(event):
    return tuple(sorted({
        assignment.recipient.defending_player for assignment in event.assignments
        if assignment.recipient.kind in {AttackRecipientKind.PLAYER, AttackRecipientKind.PLANESWALKER}
        and assignment.recipient.defending_player != event.active_player
    }))


def dispatch_player_or_walker_attacked(host, event, sources, trigger_batch):
    if not host.semantics.programs_for_event(ATTACKED_PLAYER_OR_WALKER_EVENT):
        return []
    refs = []
    declaration = event.to_dict()
    for defender in player_or_walker_defenders(event):
        refs.extend(host._dispatch_semantic_event(
            ATTACKED_PLAYER_OR_WALKER_EVENT, {
                "player": event.active_player, "defender": defender, "attack_transition": declaration,
            }, sources=sources, trigger_batch=trigger_batch,
        ))
    return refs


def validate_attack_top_exile_program(program):
    if (program.event != ATTACKED_PLAYER_OR_WALKER_EVENT or program.event_condition != attack_top_exile_condition()
            or CURRENT_ABILITY_FRAGMENT_COVERAGE not in program.coverage):
        raise ValueError("Attacking-opponent top exile requires its original defending-player declaration.")


def validate_attack_top_exile_source(host, effect, actor):
    from ..continuous_effect_state import ResolutionEffectSource
    from ..opponent_library_exile_model import (
        ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY, ATTACKING_OPPONENT_TOP_EXILE_VERSION,
    )
    from .opponent_library_exile_triggers import library_exile_trigger_runtime_dependencies

    try:
        source = ResolutionEffectSource.from_effect(effect)
        item = next((item for item in host.state.stack if item.ref == source.stack_ref), None)
        if (item is None or item.kind != "triggered_ability" or item.controller != actor
                or item.context.get("event") != ATTACKED_PLAYER_OR_WALKER_EVENT
                or item.context.get("currently_resolving") is not True or item.targets
                or item.source_object_id != source.object_id
                or item.context.get("source_logical_object_id") != source.logical_object_id):
            raise ValueError("Attacking-opponent top exile requires one original resolving ability.")
        program = host.semantics.get(item.semantic_key)
        if (program is None or not host.semantic_program_is_current_trusted(program)
                or ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY not in library_exile_trigger_runtime_dependencies(program)
                or program.effects[0]["schema_version"] != ATTACKING_OPPONENT_TOP_EXILE_VERSION):
            raise ValueError("Attacking-opponent top exile requires its admitted original instruction.")
        attack = AttackTransitionEvent.from_dict(item.context["attack_transition"])
        attacker = effect["library_owner"]
        if (attacker != attack.active_player or item.context.get("player") != attacker
                or item.context.get("defender") != actor or actor not in player_or_walker_defenders(attack)):
            raise ValueError("Attacking-opponent top exile lost its original attacker or defender.")
    except (ValueError, TypeError, KeyError) as exc:
        raise GameRuleError(str(exc)) from exc
    return attacker
