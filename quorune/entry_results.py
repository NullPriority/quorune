from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping, Protocol, Sequence

from .rules.amplify import amplify_hand_reveals
from .counter_placement import commit_counter_events_from_resolution
from .entry_designations import EntryDesignationKind, validate_bound_entry_card_name, validate_designation
from .semantic_runtime.intents import SetCardDesignationIntent
from .entry_keyword_grants import (
    commit_entry_keyword_grants,
    EntryKeywordGrant,
    EntryKeywordGrantError,
)


if TYPE_CHECKING:
    from .continuous_effect_state import ResolutionEffectSource


@dataclass(frozen=True, slots=True)
class EntryCharacteristicAddition:
    """One resolving effect's additive type and color result on actual arrivals."""

    source: ResolutionEffectSource
    colors: tuple[str, ...]
    creature_subtypes: tuple[str, ...]
    prospective: bool = False

    def __post_init__(self) -> None:
        from .continuous_effect_state import ResolutionEffectSource
        from .creature_subtypes import canonical_creature_subtype

        if not isinstance(self.source, ResolutionEffectSource):
            raise ValueError("Entry characteristics require their typed resolving source")
        if (type(self.colors) is not tuple or not self.colors and self.prospective is not True
                or any(type(color) is not str or color not in {"W", "U", "B", "R", "G"} for color in self.colors)
                or len(set(self.colors)) != len(self.colors)):
            raise ValueError("Entry characteristics require distinct canonical colors")
        if type(self.prospective) is not bool or self.prospective and self.colors:
            raise ValueError("Prospective entry additions require an exact subtype-only result")
        if (type(self.creature_subtypes) is not tuple or not self.creature_subtypes
                or any(type(word) is not str or not word or canonical_creature_subtype(word) != word
                       for word in self.creature_subtypes)
                or len(set(self.creature_subtypes)) != len(self.creature_subtypes)):
            raise ValueError("Entry characteristics require distinct canonical creature subtypes")


def prospective_entry_characteristics(host, *, card, enter_face, addition):
    from .entry_counters import capture_prospective_entry_characteristics

    values, type_line = capture_prospective_entry_characteristics(host, card=card, enter_face=enter_face)
    if addition is None or not addition.prospective:
        return values
    from .characteristic_evaluation import type_parts

    types, subtypes, _ = type_parts(type_line)
    if not types.intersection({"creature", "kindred"}):
        return values
    added = [subtype.title() for subtype in addition.creature_subtypes if subtype not in subtypes]
    if not added:
        return values
    separator = " " if subtypes else " \u2014 "
    return {**values, "type_line": type_line + separator + " ".join(added)}


def prepare_entry_characteristic_additions(state, changes, additions):
    """Bind the result to the next incarnation of each requested graveyard card."""
    from .errors import GameRuleError

    if additions is not None and not isinstance(additions, Mapping):
        raise GameRuleError("Entry characteristics require a map keyed by the entering cards")
    if not additions:
        return ()
    entrants = {}
    for object_id, destination in changes:
        card = state.cards.get(object_id)
        if (card is None or card.zone != "graveyard" or not card.is_card_object
                or destination != "battlefield" or object_id in entrants):
            raise GameRuleError("Entry characteristics require distinct graveyard cards entering the battlefield")
        entrants[object_id] = card.zone_change_counter + 1
    if (state.continuous_effects is None or set(additions) - entrants.keys()
            or any(not isinstance(addition, EntryCharacteristicAddition) for addition in additions.values())):
        raise GameRuleError("Entry characteristics require typed results and a continuous-effect journal")
    return tuple((object_id, entrants[object_id], addition) for object_id, addition in additions.items())


def prepare_simultaneous_entries(state, changes, additions, tapped_by_object):
    """Validate characteristic and tappedness requests before preparing the batch."""
    from .errors import GameRuleError

    entry_additions = prepare_entry_characteristic_additions(state, changes, additions)
    tapped_entries = dict(tapped_by_object or {})
    entering = {object_id for object_id, destination in changes if destination == "battlefield"}
    if set(tapped_entries) - entering or any(type(value) is not bool for value in tapped_entries.values()):
        raise GameRuleError("Per-object tappedness must describe entering battlefield objects")
    return entry_additions, tapped_entries


def commit_entry_characteristic_additions(host, prepared):
    """Commit only actual arrivals before their entry occurrences are sealed."""
    if not prepared:
        return
    from .continuous_effect_state import ResolutionContinuousComponent, create_resolution_continuous_effect_components
    from .continuous_effect_model import ContinuousEffectDuration, ContinuousOperation, Layer
    from .continuous_type_dependencies import CREATURE_SUBTYPE_BATTLEFIELD_QUERY

    recipients = {}
    for object_id, expected_counter, addition in prepared:
        card = host.state.cards.get(object_id)
        if card is not None and card.zone == "battlefield" and card.zone_change_counter == expected_counter:
            recipients.setdefault(addition, []).append(card)
    for addition, cards in recipients.items():
        subtypes = tuple(word.title() for word in addition.creature_subtypes)
        components = (
            ResolutionContinuousComponent(
                Layer.TYPE, "4", (ContinuousOperation("add_types", subtypes, field="subtypes"),),
                applies=CREATURE_SUBTYPE_BATTLEFIELD_QUERY,
            ),
            *((ResolutionContinuousComponent(Layer.COLOR, "5", (ContinuousOperation("add_colors", addition.colors),)),)
              if addition.colors else ()),
        )
        create_resolution_continuous_effect_components(
            host, source=addition.source, targets=cards, components=components,
            duration=ContinuousEffectDuration.ZONE_OBJECT,
        )


class PreparedEntryResult(Protocol):
    event: Any
    effects: Sequence[Any]
    journal: Sequence[Any]
    keyword_grants: Sequence[EntryKeywordGrant]


def prospective_zone_entry(host, card, enter_face, face_down, battlefield_entry, *, cloaked, type_setting):
    from .entry_counters import capture_prospective_entry_characteristics
    from .rules.entry_enchantment_setting import enchantment_only_entry_characteristics

    if face_down:
        from .manifest import cloak_characteristics

        mask = cloak_characteristics() if cloaked else {}
        return ({"name": "", "type_line": "Creature", "mana_cost": "", "mana_value": 0, "text": "", "colors": [],
                 "keywords": mask.get("abilities", []), "ability_fragments": mask.get("ability_fragments", []),
                 "power": "2", "toughness": "2"}, "Creature")
    characteristics, type_line = capture_prospective_entry_characteristics(
        host, card=card, enter_face=enter_face, battlefield_entry=battlefield_entry,
    )
    if type_setting is not None:
        characteristics = enchantment_only_entry_characteristics(characteristics)
        type_line = characteristics["type_line"]
    return characteristics, type_line


def with_copy_entry_preview(host, card, replacement, characteristics, type_line, enchant_spec, type_setting):
    from .entry_copy import copied_entry_preview

    copied = copied_entry_preview(card, replacement)
    if copied is None:
        return None, characteristics, type_line, enchant_spec
    from .rules.room_copy_values import room_copy_characteristics
    from .rules.entry_enchantment_setting import enchantment_only_entry_characteristics

    characteristics = room_copy_characteristics(copied, replacement.event.payload["entry_copy"])
    if type_setting is not None:
        characteristics = enchantment_only_entry_characteristics(characteristics)
    return copied, characteristics, str(characteristics["type_line"]), host._compiled_enchant_spec(copied)


def commit_entry_designations(
    host: Any,
    card: Any,
    *,
    color: str | None,
    creature_type: str | None,
    card_name: str | None = None,
    faction: str | None = None,
    basic_land_type: str | None = None,
    jeskai_temur_faction: str | None = None,
) -> None:
    """Retain entry choices through the canonical card-designation intent owner."""
    choices = (
        (EntryDesignationKind.COLOR, color),
        (EntryDesignationKind.CREATURE_TYPE, creature_type),
        (EntryDesignationKind.BASIC_LAND_TYPE, basic_land_type),
        (EntryDesignationKind.FACTION, faction),
        (EntryDesignationKind.JESKAI_TEMUR_FACTION, jeskai_temur_faction),
    )
    for kind, value in choices:
        if value is not None:
            host.set_card_designation_intent(
                SetCardDesignationIntent(
                    object_ref=card.ref,
                    designation=kind.annotation,
                    value=validate_designation(kind, value),
                    actor=card.controller,
                    reason="intrinsic battlefield-entry choice",
                )
            )
    if card_name is not None:
        host.set_card_designation_intent(SetCardDesignationIntent(
            object_ref=card.ref, designation="chosen_name", value=validate_bound_entry_card_name(card_name),
            actor=card.controller, reason="intrinsic battlefield-entry choice",
        ))


def commit_prepared_copy_values(prepared,card,*,error_type,preserve_physical_face=False):
    """Commit one validated copied-value result before entry observers."""
    from .replacement.face_status_copy import ENTRY_COPY_HISTORY_FIELD, ScopedCopyValues

    copy_values = prepared.event.payload.get('entry_copy')
    retained = prepared.event.payload.get(ENTRY_COPY_HISTORY_FIELD)
    if retained is not None:
        try:
            history = ScopedCopyValues.from_dict(retained)
            if not history.ordered or history.effective.values != copy_values:
                raise ValueError('The prepared copy and copy history disagree')
        except ValueError as exc:
            raise error_type(str(exc)) from exc
        install_copy_history(card, history.timestamp_effective(card.zone_timestamp))
    elif copy_values is not None:
        install_copiable_values(card, copy_values, error_type=error_type, preserve_physical_face=preserve_physical_face)


def install_copiable_values(card, values, *, error_type, preserve_physical_face):
    """Install validated copiable characteristics on the same physical object."""
    from .entry_copy import COPIABLE_FACE_FIELD, COPIABLE_ORACLE_FIELD
    from .defined_token_faces import defined_token_definition
    from .replacement.immutable import thaw_value
    from .shared_type_doors import SharedTypeDoors, copied_room_door_ids

    if not isinstance(values, Mapping) or COPIABLE_ORACLE_FIELD not in values:
        raise error_type('Prepared copy values are malformed')
    card.annotations['copy_overrides'] = thaw_value(values)
    copied_doors = copied_room_door_ids(values)
    if copied_doors is not None:
        printed = card.shared_type_doors is not None and card.shared_type_doors.printed
        card.shared_type_doors = SharedTypeDoors(copied_doors, printed=printed)
    if (defined_token_definition(card) is None
            and not (preserve_physical_face and card.object_kind == "card" and not card.is_token)):
        card.active_face=card.annotations['copy_overrides'].get(COPIABLE_FACE_FIELD)


_TRACK_LINKED_COPY_FORMS = "retain_linked_copy_forms"


def retain_linked_copy_forms(card):
    """Preserve the ability origin of this source's future copy forms."""
    from .replacement.face_status_copy import current_copy_history

    if card.annotations.get(_TRACK_LINKED_COPY_FORMS) is not True:
        history = current_copy_history(card)
        card.annotations[_TRACK_LINKED_COPY_FORMS] = True
        install_copy_history(card, history)


def install_copy_history(card, history):
    from .replacement.face_status_copy import COPY_HISTORY_FIELD
    from .replacement.immutable import thaw_value

    form = history.effective
    if form.values is None:
        card.annotations.pop("copy_overrides", None)
    else:
        card.annotations["copy_overrides"] = thaw_value(form.values)
    card.shared_type_doors = form.doors
    if history.ordered or card.annotations.get(_TRACK_LINKED_COPY_FORMS) is True:
        card.annotations[COPY_HISTORY_FIELD] = history.to_dict()
    else:
        card.annotations.pop(COPY_HISTORY_FIELD, None)
    legacy = card.annotations.get("until_end_of_turn")
    if legacy is not None:
        legacy.pop("copy_overrides_previous", None)


def begin_face_status_copy(card, values):
    from .replacement.face_status_copy import CopyExpiry, current_copy_history, scoped_copy_form

    history = current_copy_history(card)
    install_copy_history(card, history.apply(CopyExpiry.FACE_DOWN, scoped_copy_form(values,timestamp=card.zone_timestamp)))


def record_temporary_copy(card, values, *, timestamp=0):
    from copy import deepcopy
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry, current_copy_history, scoped_copy_form

    if COPY_HISTORY_FIELD in card.annotations:
        history = current_copy_history(card)
        install_copy_history(card, history.apply(CopyExpiry.END_OF_TURN,scoped_copy_form(values,timestamp=timestamp)))
    else:
        legacy = card.annotations.setdefault("until_end_of_turn", {})
        if "copy_overrides_previous" not in legacy:
            legacy["copy_overrides_previous"] = deepcopy(card.annotations.get("copy_overrides"))
        card.annotations["copy_overrides"] = values


def record_persistent_copy(card, values, *, host):
    """Install the new copy for the rest of the current incarnation."""
    from .replacement.face_status_copy import ScopedCopyValues, scoped_copy_form

    from .abilities import ActivatedAbility
    from .activation_usage import reset_copied_activation_usage
    from .linked_ability_exile_state import expire_linked_graveyard_entry_source

    from .replacement.immutable import thaw_value

    copied_abilities = tuple(
        ActivatedAbility.from_dict(thaw_value(raw)) for raw in values.get("activated_abilities", ())
    )
    reset_copied_activation_usage(card, ability_ids=(
        ability.ability_id for ability in copied_abilities if ability.activation_limit is not None
    ))
    expire_linked_graveyard_entry_source(card)
    timestamp = host._next_zone_timestamp() if card.annotations.get(_TRACK_LINKED_COPY_FORMS) is True else 0
    install_copy_history(card, ScopedCopyValues(scoped_copy_form(values, timestamp=timestamp)))


def expire_copy_scope(card, scope):
    from copy import deepcopy
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry, current_copy_history

    from .linked_ability_exile_state import expire_linked_graveyard_entry_source

    expire_linked_graveyard_entry_source(card, scope)
    if COPY_HISTORY_FIELD in card.annotations:
        history = current_copy_history(card)
        if any(known == scope for known, _ in history.ordered):
            install_copy_history(card, history.expire(scope))
    elif scope == CopyExpiry.END_OF_TURN:
        legacy = card.annotations.get("until_end_of_turn") or {}
        if "copy_overrides_previous" in legacy:
            previous = legacy["copy_overrides_previous"]
            if previous is None:
                card.annotations.pop("copy_overrides", None)
            else:
                card.annotations["copy_overrides"] = deepcopy(previous)


def reset_scoped_copy_on_departure(card):
    from .replacement.face_status_copy import COPY_HISTORY_FIELD, ScopedCopyValues

    card.annotations.pop(_TRACK_LINKED_COPY_FORMS, None)
    raw = card.annotations.get(COPY_HISTORY_FIELD)
    if raw is not None:
        history = ScopedCopyValues.from_dict(raw)
        install_copy_history(card, ScopedCopyValues(history.baseline))


def _commit_linked_entry_opponent(prepared, card, *, host) -> None:
    from .entry_designations import (
        BoundEntryOpponentChoice, COUNTED_ENTRY_OPPONENT_TEMPLATE, EntryOpponentChoice,
        ENTRY_OPPONENT_FIELD, LINKED_ENTRY_OPPONENT_FIELD, LINKED_ENTRY_PLAYER_TEMPLATE,
    )
    from .rules.linked_entry_opponent import entry_opponent_copy_form

    raw = prepared.event.payload.get(ENTRY_OPPONENT_FIELD)
    if raw is None:
        return
    if card.zone != "battlefield":
        raise ValueError("An entry opponent choice requires its completed battlefield entry")
    choice = EntryOpponentChoice.from_dict(raw)
    provider = host.semantics.get(choice.entry_program_key) if host is not None else None
    if choice.any_player:
        if (
            host is None or choice.seat not in host.active_seats or provider is None
            or provider.provenance.get("template_id") != LINKED_ENTRY_PLAYER_TEMPLATE
            or not host.semantic_program_is_current_trusted(provider)
        ):
            raise ValueError("The selected player requires its original admitted entry component")
    else:
        if provider is not None and provider.provenance.get("template_id") == COUNTED_ENTRY_OPPONENT_TEMPLATE:
            return
        if choice.seat == card.controller:
            raise ValueError("The initial entry choice requires an opponent")
    retain_linked_copy_forms(card)
    card.annotations[LINKED_ENTRY_OPPONENT_FIELD] = BoundEntryOpponentChoice(
        choice, card.logical_object_id, card.active_face, entry_opponent_copy_form(card),
    ).to_dict()


def _commit_linked_entry_color(host, prepared, card):
    from .rules.chosen_color_target_shapes import (
        LINKED_ENTRY_COLOR_FIELD, linked_color_entry_binding, linked_color_entry_programs,
    )

    color = prepared.event.payload.get("entry_chosen_color")
    if host is None or color is None or card.zone != "battlefield":
        return
    keys = linked_color_entry_programs(host, card)
    if keys:
        retain_linked_copy_forms(card)
        card.annotations[LINKED_ENTRY_COLOR_FIELD] = linked_color_entry_binding(card, color, keys)


def _commit_linked_entry_card_type(host, prepared, card):
    from .card_program_faces import program_matches_face
    from .entry_copy import copiable_oracle_id
    from .linked_card_type import (
        ANNOTATION, ENTRY_FIELD, LinkedCardTypeChoice,
    )
    from .rules.linked_entry_opponent import entry_opponent_copy_form

    raw = prepared.event.payload.get(ENTRY_FIELD)
    if raw is None:
        return
    if host is None or card.zone != "battlefield":
        raise ValueError("The linked card type requires its completed battlefield entry")
    choice = LinkedCardTypeChoice.from_dict(raw)
    record = host.card_db.by_oracle_id(copiable_oracle_id(card))
    programs = tuple(
        program for program in host.semantics.programs_for_oracle(record.oracle_id)
        if program_matches_face(record, program, card) and host.semantic_program_is_current_trusted(program)
    )
    entry_template, consumer_template = choice.templates
    if not any(program.key + ":0" == choice.component_id
               and program.provenance.get("template_id") == entry_template for program in programs):
        return
    consumers = [program.key for program in programs if program.provenance.get("template_id") == consumer_template]
    if len(consumers) != 1:
        return
    retain_linked_copy_forms(card)
    card.annotations[ANNOTATION] = {
        "choice": choice.to_dict(), "logical_object_id": card.logical_object_id,
        "face": card.active_face, "copy_form": entry_opponent_copy_form(card),
        "consumer": consumers[0],
    }


def commit_prepared_entry_identity_results(prepared,card,*,error_type,preserve_physical_face=False,host=None):
    """Commit copied values and incarnation-bound choices before entry observers."""
    commit_prepared_copy_values(prepared,card,error_type=error_type,preserve_physical_face=preserve_physical_face)
    from .day_night import commit_initial_day_entry

    if host is not None and card.zone == "battlefield":
        commit_initial_day_entry(host, prepared, reason="It becomes day as this permanent enters")
    from .linked_ability_exile_state import commit_linked_graveyard_entry
    from .rules.linked_graveyard_entry import LINKED_GRAVEYARD_RESULTS_FIELD

    if LINKED_GRAVEYARD_RESULTS_FIELD in prepared.event.payload:
        if host is None:
            raise error_type("Linked graveyard entry requires its zone owner")
        commit_linked_graveyard_entry(host, prepared, card)
    from .tribute_entry_results import commit_tribute_entry_outcomes
    from .zone_devour import commit_devour_entry_receipt
    try:
        _commit_linked_entry_opponent(prepared, card, host=host)
        _commit_linked_entry_color(host, prepared, card)
        _commit_linked_entry_card_type(host, prepared, card)
        commit_tribute_entry_outcomes(prepared,card)
        if host is not None:
            commit_devour_entry_receipt(host, prepared, card)
    except (ValueError,TypeError) as exc:raise error_type(str(exc)) from exc


def commit_prepared_entry_results(
    host: Any,
    prepared: PreparedEntryResult,
    card: Any,
    *,
    reason: str,
    log: bool,
    error_type: type[Exception],
) -> None:
    """Commit typed nested-counter and layer-6 results after one entry."""

    from .entry_reveal import ENTRY_REVEAL_FIELD
    from .effect_runtime.hand_inspection import commit_entry_hand_reveal, validate_entry_hand_reveal
    payload = prepared.event.payload
    amplify_selections = amplify_hand_reveals(payload)
    for selection in amplify_selections:
        validate_entry_hand_reveal(host, selection)
    for selection in (payload.get(ENTRY_REVEAL_FIELD), *amplify_selections):
        commit_entry_hand_reveal(host, selection, source_ref=card.ref)

    from .token_copy_value_model import token_copy_value_mode
    commit_prepared_entry_identity_results(prepared,card,error_type=error_type,
        preserve_physical_face=token_copy_value_mode(host)==1,host=host)
    from .rules.entry_subtype_grants import entry_subtype_grants,commit_entry_subtype_grants
    from .rules.entry_enchantment_setting import commit_resolution_entry_enchantment

    try:
        commit_entry_subtype_grants(host, card, entry_subtype_grants(prepared.event))
        commit_resolution_entry_enchantment(host, card, prepared.event)
    except (ValueError, TypeError) as exc:
        raise error_type(str(exc)) from exc

    commit_counter_events_from_resolution(
        host,
        prepared,
        reason=reason,
        log=log,
        error_type=error_type,
        dispatch_events=False,
    )
    from .preparation_model import PREPARE_ENTRY_FIELD,PrepareSpellSpec
    if card.zone=='battlefield' and PREPARE_ENTRY_FIELD in prepared.event.payload:
        from .preparation import become_prepared
        become_prepared(host,card,spell=PrepareSpellSpec.from_dict(prepared.event.payload[PREPARE_ENTRY_FIELD]))
    try:
        commit_entry_keyword_grants(host, card, prepared.keyword_grants)
    except EntryKeywordGrantError as exc:
        raise error_type(str(exc)) from exc


def validate_pending_amplify_reveals(host, batch, effects, selection, *, error_type):
    from .effect_runtime.hand_inspection import validate_entry_hand_reveal
    from .replacement.amplify import AmplifyEntryOperation
    from .semantic_runtime.amplify import hand_creature_types

    for event in batch.events:
        for row in amplify_hand_reveals(event.payload):
            validate_entry_hand_reveal(host, row)
    if not isinstance(selection, Mapping) or not selection.get("objects"):
        return
    effect = next((effect for effect in effects if effect.effect_id == selection["effect_id"]), None)
    if effect is None:
        return
    for operation in effect.operations:
        if not isinstance(operation, AmplifyEntryOperation):
            continue
        captured = {row.ref: row for row in operation.candidates}
        for ref in selection["objects"]:
            row = captured.get(ref)
            if row is None:
                raise error_type("Amplify selected an uncaptured hand card")
            card = validate_entry_hand_reveal(host, row.reveal())
            if hand_creature_types(host, card) != row.creature_types:
                raise error_type("Amplify selected card's creature types changed during the choice")


__all__ = ["PreparedEntryResult", "commit_prepared_entry_results", "validate_pending_amplify_reveals"]
