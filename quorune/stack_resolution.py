from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .ability_resolution_ordinal import (
    ABILITY_RESOLVED_HISTORY, is_ability_resolution_ordinal, is_triggered_ability_resolution_ordinal,
    is_second_ability_scry_replacement, validate_ability_resolution_ordinal,
)
from .aura import aura_resolution_move_kwargs
from .cast_lifecycles import (
    complete_fixed_cast_lifecycle_resolution,
    fixed_cast_lifecycle_resolution_destination,
)
from .cast_face_offers import omen_resolution_binding
from .stack_resolution_authority_model import (
    INTRINSIC_COUNTER_PROHIBITION_TEMPLATE,
    INTRINSIC_EMPTY_SPELL_ADMISSION,
    stack_resolution_authority_version,
)
from .errors import GameRuleError, StateInvariantError
from .evoke import EVOKE_PAYMENT_FIELD, validate_evoke_payment_marker, evoke_payment_annotation
from .model import CardInstance, StackItem
from .rules.casting.face_queries import prospective_cast_record
from .rules.spell_self_exile import (
    SELF_BOTTOM, SELF_SHUFFLE, spell_self_bottom_runtime_dependencies, spell_self_shuffle_runtime_dependencies,
)
from .semantic_runtime.zone_replacements import PreparedZoneChange
from .stack_counter import current_intrinsic_counter_prohibitions, oracle_has_intrinsic_counter_prohibition
from .combat_entry_activations import cleanup_ninjutsu_reveal
from .effect_runtime.hand_inspection import cleanup_stack_cost_hand_reveals


_COPY_TERM = "copy"


def physical_stack_record(host: Any, item: StackItem, *, physical_card: Any = None):
    """Bind provenance to the announced physical card, including a detached source."""
    from .entry_copy import copiable_oracle_id

    card = host.state.cards.get(item.card_object_id or '')
    detached=card is None and physical_card is not None
    if detached:
        if physical_card.object_id!=item.card_object_id:
            raise StateInvariantError('Captured physical spell source belongs to another object')
        card=physical_card
    from .face_down_characteristics_model import face_down_characteristics_mode
    from .token_copy_value_model import token_copy_value_mode
    if (card is not None and item.kind=='spell_copy'
            and (face_down_characteristics_mode(host)==1 or token_copy_value_mode(host)==1)
            and card.oracle_id.startswith('custom-copy:')):return None
    if card is not None and copiable_oracle_id(card) != card.oracle_id:
        return host.card_db.by_oracle_id(card.oracle_id)
    return host.card_record(card if detached else item.card_object_id)


class GenericStackResolutionQuery(Protocol):
    state: Any
    semantics: Any

    def card_record(self, object_id: str | CardInstance) -> Any: ...

    def _trusted_generic_spell(self, record: Any) -> bool: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


class StackResolutionCompletionHost(Protocol):
    state: Any
    semantics: Any

    def move_card(
        self,
        object_id: str,
        destination: str,
        **kwargs: Any,
    ) -> CardInstance: ...

    def shuffle_library(
        self, seat: str, *, reason: str, actor: str | None = None, shuffling_player: str | None = None,
    ) -> None: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...


def prepare_resolving_stack_effects(host, item, effects):
    """Capture per-instruction subjects before any resolving effect moves them."""
    from .rules.gift_card_spells import prepare_gift_card_resolution
    from .rules.gift_occurrence import dispatch_resolving_spell_gift
    from .rules.main_phase_creature_cohort import prepare_creature_cohort_addendum
    from .target_permanent_shuffle_reveal_model import bind_target_permanent_shuffle_reveal_owner

    prepared = bind_ability_resolution_ordinals(host, item, effects)
    prepared = bind_target_permanent_shuffle_reveal_owner(host, item, prepared)
    try:
        prepared = prepare_gift_card_resolution(host, item, prepared)
        prepared = prepare_creature_cohort_addendum(host, item, prepared)
        dispatch_resolving_spell_gift(host, item)
        return prepared
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def bind_ability_resolution_ordinals(host, item, effects):
    """Seal the ordinal after the normal stack target checks."""
    from .rules.third_resolved_source_trigger import ordinary_third_source_counter

    prepared = [dict(effect) for effect in effects]
    from .rules.magecraft_rummage_ordinal import bind_magecraft_result

    magecraft = bind_magecraft_result(host,item,prepared,_record_ability_resolution_ordinal)
    if magecraft is not None:
        return magecraft
    ordinary_counter = ordinary_third_source_counter(host, item, prepared)
    if ordinary_counter is not None:
        return ordinary_counter
    found = [index for index, effect in enumerate(prepared) if is_ability_resolution_ordinal(effect)]
    if not found:
        return prepared
    instruction = prepared[found[0]]
    prefix_count = 0 if is_second_ability_scry_replacement(instruction) else 1
    if len(prepared) != prefix_count + 1 or found != [prefix_count]:
        raise GameRuleError("An ability-resolution result requires its exact instruction sequence")
    expected_kind = "triggered_ability" if is_triggered_ability_resolution_ordinal(instruction) else "activated_ability"
    if item.kind != expected_kind:
        raise GameRuleError("An ability-resolution result requires its exact stack ability kind")
    try:
        validate_ability_resolution_ordinal(instruction)
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc
    instruction["resolution_ordinal"] = _record_ability_resolution_ordinal(host, item)
    return prepared


def _record_ability_resolution_ordinal(host, item):
    incarnation = item.context.get("source_logical_object_id")
    key = item.semantic_key
    history = host.state.turn_history
    if (history is None or history.schema_version != 1 or history.turn_sequence != host.state.turn_sequence
            or item.context.get("targets_revalidated") is not True
            or any(type(value) is not str or not value for value in (incarnation, key))):
        raise GameRuleError("An ability-resolution result requires current history and its source ability identity")
    prior = sum(
        event.object_incarnation == incarnation and event.target == key
        for event in host._current_turn_history(ABILITY_RESOLVED_HISTORY)
    )
    host._record_turn_history(
        ABILITY_RESOLVED_HISTORY, actor=item.controller, object_incarnation=incarnation,
        target=key, target_kind="ability_program",
    )
    return prior + 1


@dataclass(frozen=True, slots=True)
class EmptyStackResolution:
    destination: str | None
    note: str
    provenance: str


def _has_intrinsic_empty_admission(program: Any) -> bool:
    from .card_programs.admission import CompleteCardProgramAdmission

    admission = program.provenance.get(INTRINSIC_EMPTY_SPELL_ADMISSION)
    if (
        not isinstance(admission, Mapping)
        or program.provenance.get("template_id") != INTRINSIC_COUNTER_PROHIBITION_TEMPLATE
        or program.requires_arbiter or program.effects or program.handlers
        or any(value is not None for value in (
            program.target_schema, program.cost_schema, program.event_condition, program.destination,
        ))
    ):
        return False
    try:
        return CompleteCardProgramAdmission.from_dict(admission).admitted
    except (TypeError, ValueError):
        return False


def _intrinsic_empty_spell_provenance(host: GenericStackResolutionQuery, item: StackItem, record: Any) -> str | None:
    version = stack_resolution_authority_version(getattr(host.state, "stack_resolution_authority_version", None))
    if version is None:
        if oracle_has_intrinsic_counter_prohibition(
            host.semantics, str(record.oracle_id), current_trusted=host.semantic_program_is_current_trusted,
        ):
            return "trusted_intrinsic_counter_prohibition_spell"
        return None
    if (
        record.layout != "normal" or record.faces or not (record.is_instant or record.is_sorcery)
        or item.chosen_face is not None or item.targets
        or item.semantic_key != f"{record.oracle_id}:spell:front"
    ):
        return None
    declarations = current_intrinsic_counter_prohibitions(
        host.semantics, str(record.oracle_id), current_trusted=host.semantic_program_is_current_trusted,
    )
    if any(_has_intrinsic_empty_admission(program) for program in declarations):
        return "trusted_intrinsic_empty_spell"
    return None


def arbiter_resolution_effects(raw: Any) -> list[dict[str, Any]]:
    """Validate the raw arbiter effects before registration or resolution."""
    from .next_turn_attack_restriction_model import ATTACK_RESTRICTION_OPERATION
    from .counter_bound_attack_model import COUNTER_BOUND_ATTACK_OPERATION
    from .rules.counter_marked_keep import KEEP_COUNTER_FIELD
    from .extra_combat_model import uses_other_creature_extra_combat, uses_targeted_main_combat
    from .creature_power_damage_model import uses_trample_bite
    from .rules.announced_x_damage_sets import SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD
    from .rules.secret_number_choices import OPERATION as PRIVATE_NUMBER_OPERATION
    from .rules.announced_permanent_reveal import OPERATION as ANNOUNCED_REVEAL_OPERATION
    from .rules.artifact_mana_tap_control import FIELD as ARTIFACT_MANA_TAP_CONTROL
    from .rules.discarded_card_exile import DISCARD_PLAY_VERSION
    from .rules.effect_shape_walkers import nested_effects
    from .chosen_attack_turn_model import (
        CHOOSE as CHOSEN_ATTACK_CHOICE, COMMIT as CHOSEN_ATTACK_COMMIT, DESTROY as CHOSEN_ATTACK_DESTROY,
    )
    from .rules.apnap_counter_attack_truce import COMMIT as COUNTER_TRUCE_COMMIT, FIELD as COUNTER_TRUCE_FIELD
    from .rules.event_optional_draw_shapes import (
        ACCEPTED_TOKEN_CREATORS_FIELD, EVENT_OPTIONAL_DRAW_FIELD, TABLE_DRAW_ATTACK_RESTRICTION_VERSION,
        TEMPTING_DRAW_TOKEN_VERSION,
    )
    from .rules.target_entry_draw import uses_target_entry_draw
    from .rules.targeted_return_spell_copy import LAND_PAYMENT_FIELD, RETURN_TARGET_FIELD

    effects = [dict(effect) for effect in raw or ()]
    for effect in nested_effects(effects):
        if effect["op"].casefold() in (CHOSEN_ATTACK_CHOICE, CHOSEN_ATTACK_COMMIT, CHOSEN_ATTACK_DESTROY):
            raise GameRuleError("This resolution requires the original chosen-attacker activation")
        if (effect["op"].startswith("$")
                or EVENT_OPTIONAL_DRAW_FIELD in effect and type(effect[EVENT_OPTIONAL_DRAW_FIELD]) is not int):
            raise GameRuleError("Raw effect operations and optional draw versions must be literal")
        if KEEP_COUNTER_FIELD in effect or effect["op"] == COUNTER_BOUND_ATTACK_OPERATION:
            raise GameRuleError("Counter-marked retention requires server-issued choices")
        if uses_targeted_main_combat(effect):
            raise GameRuleError("Targeted additional combat requires its registered spell")
        if uses_other_creature_extra_combat(effect):
            raise GameRuleError("The extra combat requires its committed linked Exert trigger")
        if uses_trample_bite(effect):
            raise GameRuleError("Creature-power excess damage requires the originating two-target spell")
        if SCALED_ANNOUNCED_TARGET_DAMAGE_FIELD in effect:
            raise GameRuleError("Equal announced-X damage requires the original announced spell targets")
        if (effect["op"] == ATTACK_RESTRICTION_OPERATION
                or ACCEPTED_TOKEN_CREATORS_FIELD in effect
                or effect.get(EVENT_OPTIONAL_DRAW_FIELD) in (TABLE_DRAW_ATTACK_RESTRICTION_VERSION, TEMPTING_DRAW_TOKEN_VERSION)
                or uses_target_entry_draw(effect)):
            raise GameRuleError("This resolution requires server-issued optional draw choices")
        if LAND_PAYMENT_FIELD in effect or RETURN_TARGET_FIELD in effect:
            raise GameRuleError("A return and copy resolution requires a bound card program")
        if effect["op"] == PRIVATE_NUMBER_OPERATION:
            raise GameRuleError("Secret numbers require an exact registered spell instruction")
        if effect["op"] == ANNOUNCED_REVEAL_OPERATION:
            raise GameRuleError("An announced permanent reveal requires its exact registered stack instruction")
        if ARTIFACT_MANA_TAP_CONTROL in effect:
            raise GameRuleError("Mana-tap artifact control requires a bound card program")
        if DISCARD_PLAY_VERSION in effect:
            raise GameRuleError("Discarded-card play requires a bound card program")
        if effect["op"] == COUNTER_TRUCE_COMMIT or COUNTER_TRUCE_FIELD in effect:
            raise GameRuleError("This resolution requires server-issued APNAP counter choices")
    return effects


def arbiter_resolution_program_key(
    host: GenericStackResolutionQuery, item: StackItem, *, action: str, requested_key: Any,
) -> str | None:
    """Identify the registered program, if any, used by a scoped arbiter action."""
    if action == "register_and_resolve":
        return str(requested_key or item.semantic_key or "")
    key = item.semantic_key
    version = stack_resolution_authority_version(getattr(host.state, "stack_resolution_authority_version", None))
    if (
        version is not None and action == "resolve" and key
        and not key.startswith("builtin:") and host.semantics.get(key) is None
    ):
        # The accepted arbiter command and its continuation own these effects.
        return None
    return key


def trusted_generic_empty_resolution(
    host: GenericStackResolutionQuery,
    item: StackItem,
    program: Any,
    *, physical_card: Any = None,
) -> EmptyStackResolution | None:
    """Plan an empty resolution only for an exact spell without an effect program."""

    if program is not None:
        return None
    provenance: str | None = None
    if item.kind == "spell" and item.card_object_id:
        record = physical_stack_record(host, item, physical_card=physical_card)
        from .face_down_characteristics_model import face_down_characteristics_mode
        if face_down_characteristics_mode(host)==1:
            card=host.state.cards.get(item.card_object_id)
            if card is not None and card.face_down:
                from .compiled_morph import current_face_down_spell_is_trusted
                if not current_face_down_spell_is_trusted(host,item,card):return None
                return EmptyStackResolution(item.default_destination,
                    'Trusted exact spell resolved with no executable resolution effects','trusted_generic_face_down_spell')
        face=next((f for f in record.faces if f.get('name')==item.chosen_face),None) if record is not None and record.faces else None
        if record is not None:record=prospective_cast_record(record,face)
        if record and host._trusted_generic_spell(record):
            provenance = "trusted_generic_permanent_spell"
        elif record:
            provenance = _intrinsic_empty_spell_provenance(host, item, record)
    elif item.kind == "spell_copy":
        if item.context.get("copy_permanent_spell"):
            provenance = "trusted_generic_permanent_spell_copy"
    if provenance is None:
        return None
    return EmptyStackResolution(
        destination=item.default_destination,
        note="Trusted exact spell resolved with no executable resolution effects",
        provenance=provenance,
    )


def _printed_shuffle_owner(host: StackResolutionCompletionHost, item: StackItem, destination: str | None) -> str | None:
    if (item.kind not in {"spell", "spell_copy"} or item.semantic_key is None
            or destination not in {None, SELF_SHUFFLE.destination}):
        return None
    program = host.semantics.get(item.semantic_key)
    if program is None or SELF_SHUFFLE.mechanic not in program.coverage:
        return None
    required = spell_self_shuffle_runtime_dependencies(program)
    if (not set(required).issubset(program.capability_dependencies)
            or not host.semantic_program_is_current_trusted(program)):
        raise StateInvariantError("Resolving self shuffle requires its trusted program")
    card = host.state.cards.get(item.card_object_id)
    if card is None:
        raise StateInvariantError("Resolving self shuffle requires its original or copied spell object")
    return card.owner


def _printed_library_position(host, item, destination):
    if (item.kind not in {"spell", "spell_copy"} or item.semantic_key is None
            or destination not in {None, SELF_BOTTOM.destination}):
        return None
    program = host.semantics.get(item.semantic_key)
    if program is None or SELF_BOTTOM.mechanic not in program.coverage:
        return None
    required = spell_self_bottom_runtime_dependencies(program)
    if (not set(required).issubset(program.capability_dependencies)
            or not host.semantic_program_is_current_trusted(program)):
        raise StateInvariantError("Resolving spell placement requires its trusted program")
    return "bottom"


def complete_stack_resolution(host: StackResolutionCompletionHost,*,item: StackItem,
        destination: str | None,prepared_replacement: PreparedZoneChange | None) -> None:
    from .stack_resolution_inputs import observe_stack_resolution
    observe_stack_resolution(host, item, host.state.cards.get(item.card_object_id))
    from .rules.entry_cast_information import resolving_entry_cast_information
    from .rules.ravenous import resolving_ravenous_entry
    from .rules.paid_alternative_cast_facts import resolving_paid_alternative_entry
    from .rules.paradigm import schedule_paradigm_resolution
    schedule_paradigm_resolution(host, item)
    from .rules.third_resolved_source_trigger import complete_third_source_trigger

    complete_third_source_trigger(host, item, record_ordinal=_record_ability_resolution_ordinal)
    shuffle_owner = _printed_shuffle_owner(host, item, destination)
    library_position = _printed_library_position(host, item, destination)
    with resolving_entry_cast_information(host,item),resolving_ravenous_entry(host,item),resolving_paid_alternative_entry(host,item):
        _complete_stack_resolution(host, item=item, destination=destination,
            prepared_replacement=prepared_replacement, library_position=library_position)
        if shuffle_owner is not None:
            host.shuffle_library(shuffle_owner, reason="Resolving spell shuffled into library",
                                 actor=item.controller, shuffling_player=item.controller)
        from .spell_resolution_names import finish_spell_resolution_names
        finish_spell_resolution_names(host, item)
    from .saga_progression import dispatch_resolved_final_saga_chapter

    dispatch_resolved_final_saga_chapter(host, item)


def _complete_stack_resolution(
    host: StackResolutionCompletionHost,
    *,
    item: StackItem,
    destination: str | None,
    prepared_replacement: PreparedZoneChange | None,
    library_position: str | None = None,
) -> None:
    """Commit the final physical stack-object transition once choices end."""

    item.context.pop("currently_resolving", None)
    if item.card_object_id:
        from .zone_object_state import mark_resolving_cast_facts
        from .beheld_exile_state import mark_resolving_beheld_exile

        mark_resolving_cast_facts(host.state.cards[item.card_object_id], item)
        mark_resolving_beheld_exile(host, host.state.cards[item.card_object_id], item)
    from .rules.event_spell_copy import pin_event_spell_copy_departure
    pin_event_spell_copy_departure(host,item)
    host.state.stack.remove(item)
    cleanup_ninjutsu_reveal(host, item)
    cleanup_stack_cost_hand_reveals(host,item)
    if item.context.get("copy_permanent_spell"):
        if not item.card_object_id:
            raise StateInvariantError(
                "A permanent spell copy requires a copy object"
            )
        card = host.state.cards[item.card_object_id]
        if not card.is_spell_copy or card.zone != "stack":
            raise StateInvariantError(
                "Permanent spell-copy object left the stack early"
            )
        characteristics = copy.deepcopy(
            dict(item.context.get("copy_permanent_characteristics", {}))
        )
        card.printed_name = str(
            item.context.get("copy_permanent_name")
            or item.label.removesuffix(" " + _COPY_TERM)
        )
        card.annotations["copy_overrides"] = characteristics
        # CR 608.3f/707.10f: this same spell-copy object becomes a token
        # permanent. It is not a newly created token.
        card.object_kind = "token"
        card.is_token = True
        host.move_card(
            card.object_id,
            "battlefield",
            controller=item.controller,
            putting_player=item.controller,
            **aura_resolution_move_kwargs(item),
            prepared_replacement=prepared_replacement,
            reason="permanent spell copy resolved",
            log=False,
            semantic_events=True,
        )
        return
    if not item.card_object_id:
        return
    card = host.state.cards[item.card_object_id]
    if card.zone != "stack":
        return
    resolved_logical_object_id = card.logical_object_id
    applied_replacement_effect_ids = (
        prepared_replacement.event.applied_effects
        if prepared_replacement is not None
        and prepared_replacement.event is not None
        else ()
    )
    evoked = validate_evoke_payment_marker(
        item.context.get(EVOKE_PAYMENT_FIELD)
    )
    if evoked:
        card.annotations["evoked"] = evoke_payment_annotation(item.context[EVOKE_PAYMENT_FIELD])
    final_destination = fixed_cast_lifecycle_resolution_destination(item, destination) or "graveyard"
    host.move_card(
        card.object_id,
        final_destination,
        controller=item.controller,
        **({"putting_player": item.controller} if final_destination == "battlefield" else {}),
        **({"position": library_position} if library_position is not None and final_destination == "library" else {}),
        enter_face=item.chosen_face if final_destination == 'battlefield' else None,
        tapped=(
            True
            if isinstance(
                item.context.get("fixed_cast_lifecycle"), dict
            )
            and item.context["fixed_cast_lifecycle"].get("kind")
            == "sneak"
            else None
        ),
        **{**aura_resolution_move_kwargs(item),
            **({'aura_target_ref': item.context['copied_entry_aura_target']}
                if 'copied_entry_aura_target' in item.context else {})},
        prepared_replacement=prepared_replacement,
        reason="spell resolved",
        log=False,
        semantic_events=True,
    )
    complete_fixed_cast_lifecycle_resolution(
        host,
        item=item,
        card=card,
        resolved_logical_object_id=resolved_logical_object_id,
        applied_replacement_effect_ids=applied_replacement_effect_ids,
    )
    from .adventure import complete_adventure_resolution
    complete_adventure_resolution(item,card)
    if final_destination == "library" and omen_resolution_binding(item) is not None:
        host.shuffle_library(card.owner, reason="Omen resolved", actor=item.controller,
                             shuffling_player=item.controller)
    if evoked and card.zone != "battlefield":
        card.annotations.pop("evoked", None)


__all__ = [
    "arbiter_resolution_effects",
    "arbiter_resolution_program_key",
    "complete_stack_resolution",
    "EmptyStackResolution",
    "GenericStackResolutionQuery",
    "StackResolutionCompletionHost",
    "trusted_generic_empty_resolution",
]
