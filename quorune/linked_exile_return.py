from __future__ import annotations

"""Linked lifecycle coordination over canonical zone, entry and trigger owners."""

from typing import Any, Mapping, Protocol, Sequence

from .affected_permanents import AffectedPermanentSetSpec, PermanentControllerRelation, select_affected_permanents
from .continuous_effects import ContinuousOperation, Layer
from .continuous_effect_state import (
    ContinuousEffectStateError, create_resolution_continuous_effect,
    matching_battlefield_objects, resolution_effect_source,
)
from .carddb_characteristics import public_object_rules_names
from .entry_counter_model import EffectEntryCounter
from .characteristic_evaluation import type_parts
from .external_entry_state_model import PERMANENT_CARD_TYPES
from .errors import GameRuleError
from .rules.linked_loyalty_exile_model import LINKED_LOYALTY_EXILE_FIELD
from .linked_exile_return_model import (
    CONTROLLER_LIBRARY_TOP_HAND_BINDING, CONTROLLER_LIBRARY_TOP_HAND_VERSION,
    EXILED_TARGET_SEARCH_BINDING, ExiledObjectIdentity, LinkedExileReturnSpec, LINKED_ABILITY_TIMING, LINKED_RETURN_OPERATION,
    LINKED_TYPE_ENTRY_COUNTERS_VERSION, LINKED_TYPE_ENTRY_COUNTER_VERSIONS,
    LINKED_RETURN_SUBTYPE_COUNTER_PHASE, LINKED_RETURN_SUBTYPE_COUNTER_VERSION,
    LinkedPublicSetScope, SameNameConditionalExileScope, SelectedConditionalExileScope, validate_linked_instruction,
)
from .object_predicate import ObjectQuerySpec
from .trigger_processing import schedule_delayed_trigger
from .zone_transitions import ZoneTransitionOwner


class LinkedExileReturnHost(Protocol):
    state: Any
    active_seats: Sequence[str]
    semantics: Any

    def card_record(self, card: Any) -> Any: ...

    def semantic_program_is_current_trusted(self, program: Any) -> bool: ...

    def _effective_card_data(self, card: Any) -> Mapping[str, Any]: ...

    def _resolve_object(
        self, actor: str, ref: str, *, zones: set[str]
    ) -> Any: ...


def _spec(effect: Mapping[str, Any]) -> LinkedExileReturnSpec:
    try:
        return LinkedExileReturnSpec.from_dict(effect.get("spec"))
    except (TypeError, ValueError) as exc:
        raise GameRuleError(str(exc)) from exc


def _source_item(host: LinkedExileReturnHost, effect: Mapping[str, Any]) -> Any:
    source = effect.get("_runtime_source")
    if not isinstance(source, Mapping) or type(source.get("stack_ref")) is not str:
        raise GameRuleError("Linked exile/return requires authoritative stack context")
    item = next((item for item in host.state.stack if item.ref == source["stack_ref"]), None)
    if item is None:
        raise GameRuleError("Linked exile/return source stack object is unavailable")
    return item


def _eligible_exiled(host: LinkedExileReturnHost, objects: Any) -> tuple[Any, ...]:
    if not isinstance(objects, (list, tuple)):
        raise GameRuleError("Linked exile identities must be an array")
    result = []
    seen = set()
    for identity in objects:
        try:
            retained = ExiledObjectIdentity.from_dict(identity)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        if retained.object_id in seen:
            raise GameRuleError("Linked exile identities must be unique")
        seen.add(retained.object_id)
        card = host.state.cards.get(retained.object_id)
        if retained.matches_exiled_card(card):
            result.append(card)
    return tuple(result)


def transformed_return_face(record: Any) -> str | None:
    """Resolve physical face eligibility for CR 712.14a/b, not copy text."""
    if record is None or record.layout not in {"transform", "modal_dfc"} or len(record.faces) != 2:
        return None
    front, back = record.faces
    if record.layout == "modal_dfc" and not type_parts(str(front.get("type_line") or ""))[0] & PERMANENT_CARD_TYPES:
        return None
    if not type_parts(str(back.get("type_line") or ""))[0] & PERMANENT_CARD_TYPES:
        return None
    name = back.get("name")
    return name if type(name) is str and name else None


def return_tapped_transformed_graveyard_card(host, card, intent, selections):
    """Return the physical owner's admitted back face using the normal entry transaction."""
    if not card.is_card_object or card.is_token or card.owner != intent.new_controller:
        return ""
    face = transformed_return_face(host.card_db.by_oracle_id(card.oracle_id))
    if face is None:
        return ""
    moved = host.move_card(
        card.object_id, "battlefield", controller=card.owner, putting_player=intent.actor,
        enter_face=face, tapped=True, reason=intent.reason, semantic_events=intent.semantic_events,
        replacement_selections=selections, transition_kind=intent.transition_kind,
    )
    return moved.ref if moved.zone == "battlefield" else ""


def return_linked_exiled_objects(
    host: LinkedExileReturnHost,
    effect: Mapping[str, Any],
    *, actor: str, operation: str, reason: str,
) -> list[str]:
    from .linked_library_imprint_model import uses_library_imprint_death, uses_linked_artifact_library_instruction

    if uses_linked_artifact_library_instruction(effect):
        from .effect_runtime.linked_library_imprint import apply_linked_artifact_owner_return

        return apply_linked_artifact_owner_return(host, effect, actor=actor, reason=reason)

    if uses_library_imprint_death(effect):
        from .effect_runtime.linked_library_imprint import apply_linked_library_imprint

        return apply_linked_library_imprint(host, effect, actor=actor, operation=operation, reason=reason)
    required = {"op", "objects", "spec"}
    raw_spec = effect.get("spec")
    library_top = (isinstance(raw_spec, Mapping)
                   and raw_spec.get("schema_version") == CONTROLLER_LIBRARY_TOP_HAND_VERSION)
    if library_top:
        required.add("original_owner")
    allowed = required | {"_runtime_source", "_replacement_selections", "_aura_target_refs"}
    if set(effect) - allowed or not required.issubset(effect):
        raise GameRuleError("Linked return has an invalid shape")
    spec = _spec(effect)
    if spec.schema_version == LINKED_RETURN_SUBTYPE_COUNTER_VERSION:
        raise GameRuleError("A post-return counter requires its original linked spell")
    cards = _eligible_exiled(host, effect["objects"])
    if library_top:
        item = _source_item(host, effect)
        from .replacement.immutable import thaw_value

        owner = effect["original_owner"]
        sealed = {key: thaw_value(effect[key]) for key in required}
        if (item.kind != "triggered_ability" or item.controller != actor
                or type(owner) is not str or owner not in host.state.players
                or len(effect["objects"]) > 1 or any(card.owner != owner for card in cards)
                or "_aura_target_refs" in effect or item.context.get("event") != "step.begin"
                or not item.context.get("delayed_trigger_ref")
                or thaw_value(item.context.get("dynamic_effects")) != [sealed]):
            raise GameRuleError("Private linked return requires its original controller and one exile incarnation")
        if not cards:
            return []
        card, = cards
        moved = ZoneTransitionOwner(host).move_card(
            card.object_id, "hand", reason=reason, semantic_events=True,
            replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        )
        return [moved.ref] if moved.zone == "hand" else []
    entry_faces = {}
    if spec.transformed:
        entry_faces = {card.object_id: face for card in cards
                       if (face := transformed_return_face(host.card_record(card))) is not None}
        cards = tuple(card for card in cards if card.object_id in entry_faces)
    if not cards:
        return []
    source = resolution_effect_source(host, effect)
    controllers = {
        card.object_id: actor if spec.controller == "actor" else card.owner
        for card in cards
    }
    source_ref = source.card_ref or source.stack_ref
    if spec.schema_version in LINKED_TYPE_ENTRY_COUNTER_VERSIONS:
        conditions = (("+1/+1", "creature"),)
        if spec.schema_version == LINKED_TYPE_ENTRY_COUNTERS_VERSION:
            conditions += (("loyalty", "planeswalker"),)
        counters = {
            card.object_id: tuple(
                EffectEntryCounter(name, 1, card.owner, source_ref, "400.7j", required_type)
                for name, required_type in conditions
            )
            for card in cards
        }
    else:
        counters = {
            card.object_id: tuple(
                EffectEntryCounter(name, amount, actor, source_ref, "400.7j")
                for name, amount in spec.entry_counters
            )
            for card in cards
        }
    if any(controller not in host.active_seats for controller in controllers.values()):
        raise GameRuleError("Linked return controller is unavailable")
    moved = ZoneTransitionOwner(host).move_cards_simultaneously(
        tuple((card.object_id, "battlefield") for card in cards), reason=reason,
        tapped=spec.tapped, destination_controllers=controllers,
        putting_players={card.object_id: actor for card in cards},
        effect_entry_counters=counters,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
        aura_target_refs=effect.get('_aura_target_refs'),
        entry_faces=entry_faces,
    )
    returned = tuple(card for card in moved if card.zone == "battlefield")
    if spec.keywords and returned:
        create_resolution_continuous_effect(
            host, source=source, targets=returned,
            layer=Layer.ABILITY, sublayer="6",
            operations=tuple(
                ContinuousOperation("add_ability", keyword)
                for keyword in spec.keywords
            ),
        )
    return [card.ref for card in returned]


def _same_name_exile_cards(host, cards, *, actor, scope):
    if len(cards) != 1 or cards[0].controller == actor:
        raise GameRuleError("Same-name conditional exile requires one opposing nonland target")
    anchor, = cards
    controller = anchor.controller if scope is SameNameConditionalExileScope.ANCHOR_CONTROLLER else None
    query = ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",),
                            controller=controller, excluded_controllers=() if controller is not None else (actor,))
    try:
        eligible = matching_battlefield_objects(host, query)
    except ContinuousEffectStateError as exc:
        raise GameRuleError(str(exc)) from exc
    if not any(card is anchor for card in eligible):
        raise GameRuleError("Same-name conditional exile target is no longer legal")
    names = set(public_object_rules_names(anchor, host._effective_card_data(anchor)))
    if not names:
        return (anchor,)
    return tuple(card for card in eligible if card is anchor or not names.isdisjoint(
        public_object_rules_names(card, host._effective_card_data(card))))


def _public_exile_set_cards(host, scope, player, actor):
    if type(player) is not str or player not in host.active_seats:
        raise GameRuleError("Linked public exile requires an active player bound by its source")
    if scope is LinkedPublicSetScope.TARGET_PLAYER_NONLANDS:
        query = ObjectQuerySpec(zones=("battlefield",), excluded_types=("land",))
        spec = AffectedPermanentSetSpec(
            query, controller_relation=PermanentControllerRelation.TARGET_PLAYER, target_controller=player,
        )
    else:
        if player != actor:
            raise GameRuleError("A creature-set exile must use its resolving controller")
        query = ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))
        if scope is LinkedPublicSetScope.ALL_CREATURES:
            spec = AffectedPermanentSetSpec(query)
        else:
            spec = AffectedPermanentSetSpec(
                query, controller_relation=PermanentControllerRelation.TARGET_PLAYER, target_controller=player,
            )
    rows = select_affected_permanents(
        host.affected_permanent_object_rows(actor), spec, actor=actor,
        active_seats=host.affected_permanent_active_seats(), apnap_order=host.affected_permanent_apnap_order(),
    )
    cards = tuple(host._resolve_object(actor, row.ref, zones={"battlefield"}) for row in rows)
    if any(
        card.object_id != row.object_id or card.logical_object_id != row.logical_object_id
        for card, row in zip(cards, rows, strict=True)
    ):
        raise GameRuleError("Linked public exile requires the queried battlefield incarnations")
    return cards


def _selected_conditional_cards(host, selection, *, scope, actor, source):
    from .public_zone_moves import PublicZoneMoveObject

    if not isinstance(selection, (list, tuple)) or not selection:
        raise GameRuleError("Conditional exile requires a sealed nonempty selection")
    try:
        identities = tuple(PublicZoneMoveObject.from_dict(value) for value in selection)
    except (ValueError, TypeError) as exc:
        raise GameRuleError(str(exc)) from exc
    if len({identity.object_id for identity in identities}) != len(identities):
        raise GameRuleError("Conditional exile selection may not contain repeated objects")
    cards = []
    for identity in identities:
        card = host.state.cards.get(identity.object_id)
        if (
            card is None or card.zone != "battlefield" or card.phased_out or card.controller != actor
            or identity.controller != actor or card.ref != identity.ref or card.owner != identity.owner
            or card.logical_object_id != identity.logical_object_id
        ):
            raise GameRuleError("A selected conditional exile incarnation changed")
        types, subtypes, _ = type_parts(str(host._effective_card_data(card).get("type_line") or ""))
        if not scope.matches(card_object_id=card.object_id, source_object_id=source.object_id,
                             types=types, subtypes=subtypes, token=card.is_token):
            raise GameRuleError("A selected conditional exile object is outside its printed scope")
        cards.append(card)
    return tuple(cards)


def _move_controller_library_top_to_exile(host, actor, effect, reason):
    library = host.state.players[actor].zones["library"]
    if not library:
        return ()
    card = ZoneTransitionOwner(host).move_card(
        library[-1], "exile", reason=reason, semantic_events=True, unviewed_library_exile=True,
        replacement_selections=tuple(effect.get("_replacement_selections") or ()),
    )
    return (card,)


def _linked_exile_cards(host, selection, spec, actor):
    if spec.public_set_scope is not None:
        return _public_exile_set_cards(host, spec.public_set_scope, selection, actor)
    refs = [] if selection is None else [selection] if isinstance(selection, str) else selection
    if not isinstance(refs, (list, tuple)) or any(type(ref) is not str or not ref for ref in refs) or len(set(refs)) != len(refs):
        raise GameRuleError("Linked exile targets must be unique references")
    return tuple(host._resolve_object(actor, ref, zones={"battlefield"}) for ref in refs)


def committed_conditional_exile_count(item, binding_id):
    scope = SelectedConditionalExileScope.OTHER_NONLAND_PERMANENTS_WITH_SOLDIERS
    journal = item.context.get("linked_exile_returns")
    entry = journal.get(binding_id) if isinstance(journal, Mapping) else None
    if (binding_id != scope.binding_id or not isinstance(entry, Mapping)
            or set(entry) != {"spec", "objects", "completed", "committed_exile_count"}
            or entry["completed"] is not True
            or LinkedExileReturnSpec.from_dict(entry["spec"]).selected_departure_scope is not scope
            or type(entry["committed_exile_count"]) is not int or entry["committed_exile_count"] < 0):
        raise ValueError("A token result requires its own completed conditional exile")
    return entry["committed_exile_count"]


_UNRESOLVED_LINKED_EXILE_DISPATCH = object()


def _resolve_special_linked_exile(host, effect, *, actor, reason):
    from .rules.graveyard_exchange_model import GRAVEYARD_EXCHANGE_FIELD

    if GRAVEYARD_EXCHANGE_FIELD in effect:
        from .effect_runtime.graveyard_exchange import resolve_graveyard_exchange_phase

        return resolve_graveyard_exchange_phase(host, _source_item(host, effect), effect, actor=actor, reason=reason)
    from .rules.linked_creature_settlement import FIELD, apply_linked_creature_settlement

    if FIELD in effect:
        return apply_linked_creature_settlement(host, _source_item(host, effect), effect, actor=actor, reason=reason)
    from .rules.linked_death_upkeep import FIELD as DEATH_UPKEEP_FIELD, apply_linked_death_upkeep

    if DEATH_UPKEEP_FIELD in effect:
        return apply_linked_death_upkeep(host, _source_item(host, effect), effect, actor=actor, reason=reason)
    from .meld_model import uses_meld_pair

    if uses_meld_pair(effect):
        from .meld_resolution import resolve_meld_instruction

        return resolve_meld_instruction(host, _source_item(host, effect), effect, actor=actor, reason=reason)
    if LINKED_LOYALTY_EXILE_FIELD in effect:
        from .effect_runtime.linked_loyalty_exile import apply_linked_loyalty_exile

        return apply_linked_loyalty_exile(host, _source_item(host, effect), effect, actor=actor, reason=reason)
    return _UNRESOLVED_LINKED_EXILE_DISPATCH


def resolve_linked_exile_return(
    host: LinkedExileReturnHost,
    effect: Mapping[str, Any],
    *, actor: str, operation: str, reason: str,
) -> list[str]:
    del operation
    resolved = _resolve_special_linked_exile(host, effect, actor=actor, reason=reason)
    if resolved is not _UNRESOLVED_LINKED_EXILE_DISPATCH:
        return resolved
    spec = _spec(effect)
    if spec.timing == LINKED_ABILITY_TIMING:
        from .linked_ability_exile_state import resolve_linked_ability_instruction
        instruction = {key: value for key, value in effect.items() if key != "_runtime_source"}
        try:
            validate_linked_instruction(instruction)
        except ValueError as exc:
            raise GameRuleError(str(exc)) from exc
        return resolve_linked_ability_instruction(
            host, _source_item(host, effect), instruction, actor=actor, reason=reason, spec=spec,
        )
    phase = effect.get("phase")
    required = {"op", "phase", "binding_id", "spec", *(("cards",) if phase == "exile" else ())}
    permitted = {"_runtime_source", "_replacement_selections"}
    binding_id = effect.get("binding_id")
    combined_entry = binding_id == EXILED_TARGET_SEARCH_BINDING
    if combined_entry and phase == "return":
        permitted.add("_aura_target_refs")
    subtype_counter = spec.schema_version == LINKED_RETURN_SUBTYPE_COUNTER_VERSION
    phases = {"exile", "return", LINKED_RETURN_SUBTYPE_COUNTER_PHASE} if subtype_counter else {"exile", "return"}
    if phase not in phases or set(effect) - (required | permitted) or not required.issubset(effect):
        raise GameRuleError("Linked exile/return instruction is malformed")
    if type(binding_id) is not str or not binding_id.startswith("blink:"):
        raise GameRuleError("Linked exile/return binding is malformed")
    item = _source_item(host, effect)
    library_top = spec.schema_version == CONTROLLER_LIBRARY_TOP_HAND_VERSION
    if subtype_counter:
        from .rules.linked_return_subtype_counter import require_linked_subtype_counter_source

        require_linked_subtype_counter_source(host, item, effect, actor)
    if library_top and (
        binding_id != CONTROLLER_LIBRARY_TOP_HAND_BINDING or item.kind != "activated_ability"
        or actor != item.controller
        or actor not in host.active_seats or (phase == "exile" and effect["cards"] != actor)
    ):
        raise GameRuleError("Private linked exile requires the resolving controller's library")
    if combined_entry:
        from .rules.exiled_target_search import require_combined_entry_program

        require_combined_entry_program(host, item)
        targets = [target for target in item.targets if target is not None]
        if actor != item.controller or (phase == "exile" and effect["cards"] != targets):
            raise GameRuleError("Combined entry changed its original linked exile targets")
    journal = item.context.setdefault("linked_exile_returns", {})
    if not isinstance(journal, dict):
        raise GameRuleError("Linked exile/return continuation is malformed")
    selected_scope = spec.selected_departure_scope
    if selected_scope is not None and (binding_id != selected_scope.binding_id or actor != item.controller):
        raise GameRuleError("Conditional exile selection does not belong to this resolving instruction")
    if phase == "exile":
        if binding_id in journal:
            raise GameRuleError("Linked exile instruction has already committed")
        if spec.timing == 'source_departure':
            from .conditional_exile_return import conditional_exile_source
            from .conditional_exile_model import conditional_source_remains
            source = conditional_exile_source(effect)
            if not conditional_source_remains(source, host.state.cards):
                journal[binding_id] = {"spec": spec.to_dict(), "objects": [], "completed": False}
                if selected_scope is not None:
                    journal[binding_id]["committed_exile_count"] = 0
                return []
        if library_top:
            moved = _move_controller_library_top_to_exile(host, actor, effect, reason)
        else:
            cards = (_selected_conditional_cards(host, effect["cards"], scope=selected_scope, actor=actor, source=source)
                     if selected_scope is not None else _linked_exile_cards(host, effect["cards"], spec, actor))
            if any(card.phased_out for card in cards):
                raise GameRuleError("Linked exile target is phased out")
            if spec.same_name_scope is not None:
                cards = _same_name_exile_cards(host, cards, actor=actor, scope=spec.same_name_scope)
            moved = (
                ZoneTransitionOwner(host).move_cards_simultaneously(
                    tuple((card.object_id, "exile") for card in cards), reason=reason,
                    replacement_selections=tuple(effect.get("_replacement_selections") or ()),
                )
                if cards else ()
            )
        objects = [{"object_id": card.object_id, "ref": card.ref, "logical_object_id": card.logical_object_id} for card in moved if card.zone == "exile" and not card.is_token and not card.is_spell_copy]
        journal[binding_id] = {"spec": spec.to_dict(), "objects": objects, "completed": False}
        if selected_scope is not None:
            journal[binding_id]["committed_exile_count"] = sum(card.zone == "exile" for card in moved)
        return [] if library_top else [card.ref for card in moved]
    binding = journal.get(binding_id)
    if subtype_counter and phase == LINKED_RETURN_SUBTYPE_COUNTER_PHASE:
        from .rules.linked_return_subtype_counter import commit_linked_subtype_counter

        return commit_linked_subtype_counter(host, item, effect, binding, actor=actor, reason=reason)
    expected_fields = {"spec", "objects", "completed"}
    if selected_scope is not None:
        expected_fields.add("committed_exile_count")
    if not isinstance(binding, Mapping) or set(binding) != expected_fields or binding["spec"] != spec.to_dict() or binding["completed"] is not False:
        raise GameRuleError("Linked return continuation changed or already completed")
    objects = binding["objects"]
    if spec.timing == 'source_departure':
        from .conditional_exile_return import retain_or_return_conditional_exile
        result=retain_or_return_conditional_exile(host,item,binding_id,objects,effect,selected_scope=selected_scope)
    elif combined_entry:
        from .rules.exiled_target_search import return_combined_entry

        result = return_combined_entry(host, item, effect, _eligible_exiled(host, objects), reason=reason)
    elif spec.timing == "immediate":
        result = return_linked_exiled_objects(
            host,
            {"op": LINKED_RETURN_OPERATION, "objects": objects,
             "spec": (LinkedExileReturnSpec() if subtype_counter else spec).to_dict(),
             "_runtime_source": effect["_runtime_source"],
             "_replacement_selections": effect.get("_replacement_selections", ())},
            actor=actor, operation=LINKED_RETURN_OPERATION, reason=reason,
        )
    else:
        result = []
        if objects or spec.public_set_scope is not None or library_top:
            condition = {"phase": "combat", "step": "beginning_combat"} if spec.timing == "next_combat" else {"step": "end_step"}
            if spec.timing == "controller_next_end_step":
                condition["player"] = actor
            source = effect["_runtime_source"]
            schedule_delayed_trigger(
                host, controller=actor, label="Return linked exiled objects",
                event_kind="step.begin", condition=condition,
                stack_template={"context": {"dynamic_effects": [
                    {"op": LINKED_RETURN_OPERATION, "objects": objects, "spec": spec.to_dict(),
                     **({"original_owner": actor} if library_top else {})}
                ]}},
                source_object_id=source.get("object_id"),
                source_logical_object_id=item.context.get("source_logical_object_id") if library_top else None,
                referred_object_ids=tuple(identity["object_id"] for identity in objects),
                once=True,
            )
    if subtype_counter:
        from .rules.linked_return_subtype_counter import record_linked_subtype_counter_arrival

        record_linked_subtype_counter_arrival(host, binding, result)
    binding["completed"] = True
    return result
