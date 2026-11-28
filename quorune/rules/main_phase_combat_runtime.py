from __future__ import annotations

from ..affected_permanents import AffectedPermanentSetSpec, select_affected_permanents
from ..continuous_effect_state import ResolutionEffectSource
from ..errors import GameRuleError
from .main_phase_combat_model import (
    MainPhaseCombatInstruction, attack_untap_effect, main_phase_combat_effect, main_phase_combat_instruction,
)
from ..object_predicate import ObjectQuerySpec
from ..public_tap_state_sets import commit_public_tap_state_snapshot, snapshot_public_tap_state
from ..replacement.immutable import thaw_value
from .main_phase_combats import is_closed_main_phase_combat_program
from ..turn_history import current_turn_history_events


_EVENT = "step.begin"
_CONDITION = {"phase": "combat", "step": "beginning_combat"}
_SPELL_REF = "turn_attacker_untap_spell_ref"
_LABEL = "Untap all creatures that attacked this turn"
_CREATURES = AffectedPermanentSetSpec(ObjectQuerySpec(zones=("battlefield",), types_all=("creature",)))


def _attack_history(state):
    history = state.turn_history
    if history is None or history.schema_version != 1 or history.turn_sequence != state.turn_sequence:
        raise GameRuleError("Turn-attacker untap requires the current declared attack history")
    return history


def schedule_main_phase_combats_and_untap(host, effect, *, actor):
    from ..trigger_processing import schedule_delayed_trigger

    try:
        instruction = main_phase_combat_instruction({
            key: val for key, val in effect.items() if key not in {"reason", "_runtime_source"}
        })
        source = ResolutionEffectSource.from_effect(effect)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    item = host.state.stack[-1] if host.state.stack else None
    program = host.semantics.get(item.semantic_key) if item is not None else None
    if (item is None or item.ref != source.stack_ref or item.controller != actor or actor not in host.active_seats
            or item.kind not in {"spell", "spell_copy"} or item.targets
            or item.context.get("currently_resolving") is not True
            or program is None or not host.semantic_program_is_current_trusted(program)
            or not is_closed_main_phase_combat_program(program)
            or thaw_value(program.effects[0]) != main_phase_combat_effect(instruction)):
        raise GameRuleError("Main-phase combats require their original resolving untargeted spell")
    _attack_history(host.state)
    source_ref = source.card_ref or source.stack_ref
    if instruction is MainPhaseCombatInstruction.COMBAT_THEN_MAIN_IMMEDIATE_UNTAP:
        _untap_current_declared_attackers(host, actor=actor, reason=effect.get("reason") or "untap turn attackers")
        host.turn_steps.schedule_fixed_combats_after_main(actor, source=source_ref, instruction=instruction)
        return None
    host.turn_steps.schedule_fixed_combats_after_main(actor, source=source_ref, instruction=instruction)
    return schedule_delayed_trigger(
        host, controller=actor, label=_LABEL, event_kind=_EVENT, condition=_CONDITION,
        stack_template={"context": {"dynamic_effects": [attack_untap_effect()], _SPELL_REF: source.stack_ref}},
        source_object_id=source.object_id, source_logical_object_id=source.logical_object_id,
        once=False, expires_turn_sequence=host.state.turn_sequence,
    ).ref


def _is_attacker_untap(trigger):
    context = trigger.stack_template.get("context", {})
    return (
        trigger.event_kind == _EVENT and trigger.condition == _CONDITION and not trigger.once
        and context.get("dynamic_effects") == [attack_untap_effect()]
        and type(context.get(_SPELL_REF)) is str and bool(context[_SPELL_REF])
    )


def expires_attack_untap_in_cleanup(trigger, turn_sequence):
    return trigger.expires_turn_sequence == turn_sequence and _is_attacker_untap(trigger)


def resolve_untap_turn_attackers(host, intent):
    state = host.state
    item = state.stack[-1] if state.stack else None
    if (item is None or item.ref != intent.stack_ref or item.controller != intent.actor or item.targets
            or item.kind != "triggered_ability" or item.context.get("currently_resolving") is not True
            or item.context.get("event") != _EVENT or item.context.get("dynamic_effects") != [attack_untap_effect()]
            or (state.phase, state.step) != ("combat", "beginning_combat")):
        raise GameRuleError("Turn-attacker untap requires the original delayed beginning-of-combat occurrence")
    trigger = next((row for row in state.delayed_triggers if row.ref == item.context.get("delayed_trigger_ref")), None)
    if (trigger is None or trigger.controller != intent.actor or trigger.source_object_id != item.source_object_id
            or trigger.created_turn_sequence != state.turn_sequence or not expires_attack_untap_in_cleanup(trigger, state.turn_sequence)
            or trigger.stack_template["context"][_SPELL_REF] != item.context.get(_SPELL_REF)):
        raise GameRuleError("The delayed untap lost its original turn and spell source")
    return _untap_current_declared_attackers(host, actor=intent.actor, reason=intent.reason)


def _untap_current_declared_attackers(host, *, actor, reason):
    state = host.state
    attacks = current_turn_history_events(_attack_history(state), turn_sequence=state.turn_sequence, kind="creature_attacked")
    identities = {event.object_incarnation for event in attacks}
    rows = select_affected_permanents(
        (row for row in host.affected_permanent_object_rows(actor) if row.logical_object_id in identities),
        _CREATURES, actor=actor, active_seats=host.affected_permanent_active_seats(),
        apnap_order=host.affected_permanent_apnap_order(),
    )
    return commit_public_tap_state_snapshot(
        host, snapshot_public_tap_state(rows), actor=actor, tapped=False, reason=reason,
    )
