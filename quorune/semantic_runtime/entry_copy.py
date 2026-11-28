from __future__ import annotations

"""Intrinsic entry-copy choices through the existing replacement journal."""

from dataclasses import dataclass, replace
import hashlib
from ..util import stable_json
from typing import Any, Mapping

from ..rules.amplify import AMPLIFY_HANDLER
from ..entry_copy import (EntryCopySpec, ENTRY_COPY_HANDLER, ENTRY_COPY_CAPABILITY,
    ENTRY_COPY_QUERY_HANDLER, ENTRY_COPY_QUERY_CAPABILITIES, ENTRY_COPY_VALUES_HANDLER, ENTRY_COPY_VALUES_CAPABILITY,
    ENTRY_COPY_TAPPED_HANDLER, ENTRY_COPY_TAPPED_CAPABILITIES, CONTROLLED_COPY_SHIELD_HANDLER,
    CONTROLLED_COPY_SHIELD_CAPABILITIES, ANNOUNCED_X_COPY_COUNTER_HANDLER, ANNOUNCED_X_COPY_COUNTER_CAPABILITIES,
    TYPE_DIRECTED_COPY_COUNTER_HANDLER, TYPE_DIRECTED_COPY_COUNTER_CAPABILITIES,
    LEGENDARY_SNOW_COPY_HANDLER, LEGENDARY_SNOW_COPY_CAPABILITIES,
    CONDITIONAL_VANISHING_COPY_HANDLER, CONDITIONAL_VANISHING_COPY_CAPABILITIES,
    FACE_UP_OR_ENTRY_COPY_HANDLER, FACE_UP_OR_ENTRY_COPY_CAPABILITIES,
    GRAVEYARD_PAIR_COPY_HANDLER, GRAVEYARD_PAIR_COPY_CAPABILITIES,
    TEMPORARY_HASTE_COPY_VERSION, TEMPORARY_HASTE_COPY_HANDLER, TEMPORARY_HASTE_COPY_CAPABILITIES,
    COPIABLE_ORACLE_FIELD, COPIABLE_FACE_FIELD, copiable_oracle_id)
from ..object_query import object_matches_query, object_query_result
from ..replacement_effects import CreateAffectedObjectCounter, ReplacementClass, ReplacementEffect, SetField
from ..token_copy_recipes import apply_copy_exception
from .component_registry import exact_fields
from .context import SemanticNodeError
from ..rules.color_retaining_copy import (
    RETAINED_COLOR_ENTRY_HANDLER, RETAINED_COLOR_COPY_CAPABILITY, apply_color_retaining_copy, retained_color_flip_values,
)
from ..conditional_copy_model import CONDITIONAL_COPY_HANDLER,CONDITIONAL_COPY_CAPABILITY,EMBALMED_DESIGNATION
from ..entry_copy_predicates import ENTRY_COPY_PREDICATE_HANDLER,ENTRY_COPY_PREDICATE_CAPABILITY,entry_copy_predicate_matches

_COPY_VERSIONS = {ENTRY_COPY_HANDLER: 1, ENTRY_COPY_QUERY_HANDLER: 2, ENTRY_COPY_VALUES_HANDLER: 3,
    ENTRY_COPY_PREDICATE_HANDLER: 4, CONDITIONAL_COPY_HANDLER: 5, ENTRY_COPY_TAPPED_HANDLER: 6,
    CONTROLLED_COPY_SHIELD_HANDLER: 7, ANNOUNCED_X_COPY_COUNTER_HANDLER: 8, TYPE_DIRECTED_COPY_COUNTER_HANDLER: 9,
    LEGENDARY_SNOW_COPY_HANDLER: 10, CONDITIONAL_VANISHING_COPY_HANDLER: 11, FACE_UP_OR_ENTRY_COPY_HANDLER: 12,
    GRAVEYARD_PAIR_COPY_HANDLER: 13, RETAINED_COLOR_ENTRY_HANDLER: 14,
    TEMPORARY_HASTE_COPY_HANDLER: TEMPORARY_HASTE_COPY_VERSION}


@dataclass(frozen=True, slots=True)
class EntryCopyHandler:
    handler_id: str = ENTRY_COPY_HANDLER
    schema_version: int = 1
    family: str = 'replacement.zone.entry_copy'
    event: str = 'zone.change'
    rule_references: tuple[str, ...] = ('614.12', '614.12a', '616.1c', '707.2', '707.5', '707.9')
    capability_dependencies: tuple[str, ...] = (ENTRY_COPY_CAPABILITY,)

    def validate(self, descriptor):
        exact_fields(descriptor, {'handler_id', 'schema_version', 'event', 'copy'}, field='intrinsic entry copy')
        expected = _COPY_VERSIONS.get(self.handler_id)
        if (self.schema_version != expected or descriptor['handler_id'] != self.handler_id or type(descriptor['schema_version']) is not int
                or descriptor['schema_version'] != self.schema_version or descriptor['event'] != self.event):
            raise SemanticNodeError('Intrinsic entry copy identity changed')
        try:
            spec = EntryCopySpec.from_dict(descriptor['copy'])
            if spec.schema_version != self.schema_version:
                raise ValueError('Copy selector and handler versions differ')
            return spec
        except (ValueError, TypeError) as exc:
            raise SemanticNodeError(str(exc)) from exc

    def lower(self, descriptor, context):
        self.validate(descriptor)
        return ()

    def subject_replacement_effects(self, descriptor, *, subject, component_id):
        self.validate(descriptor)
        # Candidate-dependent effects are captured by the canonical snapshot
        # coordinator, then resolved without mutable game-state reads.
        return ()


def entry_copy_handler(version):
    if type(version) is not int:
        raise SemanticNodeError('Copy selector version must be an integer')
    if version == 1:
        return EntryCopyHandler()
    if version == 2:
        return EntryCopyHandler(handler_id=ENTRY_COPY_QUERY_HANDLER, schema_version=2,
            capability_dependencies=ENTRY_COPY_QUERY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '115.10a', '201.5'))
    if version==3:return EntryCopyHandler(handler_id=ENTRY_COPY_VALUES_HANDLER,schema_version=3,capability_dependencies=(ENTRY_COPY_CAPABILITY,ENTRY_COPY_VALUES_CAPABILITY),rule_references=(*EntryCopyHandler().rule_references,'707.9c','707.9d','205.3d'))
    if version==4:return EntryCopyHandler(handler_id=ENTRY_COPY_PREDICATE_HANDLER,schema_version=4,capability_dependencies=(ENTRY_COPY_CAPABILITY,ENTRY_COPY_PREDICATE_CAPABILITY),rule_references=(*EntryCopyHandler().rule_references,'400.7d','601.2h'))
    if version==5:return EntryCopyHandler(handler_id=CONDITIONAL_COPY_HANDLER,schema_version=5,capability_dependencies=(ENTRY_COPY_CAPABILITY,CONDITIONAL_COPY_CAPABILITY),rule_references=(*EntryCopyHandler().rule_references,'707.9f','702.128a'))
    if version == 6:
        return EntryCopyHandler(handler_id=ENTRY_COPY_TAPPED_HANDLER, schema_version=6,
            capability_dependencies=ENTRY_COPY_TAPPED_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '614.1c', '205.3d', '205.3i'))
    if version == 7:
        return EntryCopyHandler(handler_id=CONTROLLED_COPY_SHIELD_HANDLER, schema_version=7,
            capability_dependencies=CONTROLLED_COPY_SHIELD_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '122.1c'))
    if version == 8:
        return EntryCopyHandler(handler_id=ANNOUNCED_X_COPY_COUNTER_HANDLER, schema_version=8,
            capability_dependencies=ANNOUNCED_X_COPY_COUNTER_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '107.3m', '122.1a'))
    if version == 9:
        return EntryCopyHandler(handler_id=TYPE_DIRECTED_COPY_COUNTER_HANDLER, schema_version=9,
            capability_dependencies=TYPE_DIRECTED_COPY_COUNTER_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '122.1a', '306.5b'))
    if version == 10:
        return EntryCopyHandler(handler_id=LEGENDARY_SNOW_COPY_HANDLER, schema_version=10,
            capability_dependencies=LEGENDARY_SNOW_COPY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '205.4a', '702.73a', '122.1a'))
    if version == 11:
        return EntryCopyHandler(handler_id=CONDITIONAL_VANISHING_COPY_HANDLER, schema_version=11,
            capability_dependencies=CONDITIONAL_VANISHING_COPY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '702.63a'))
    if version == 12:
        return EntryCopyHandler(handler_id=FACE_UP_OR_ENTRY_COPY_HANDLER, schema_version=12,
            capability_dependencies=FACE_UP_OR_ENTRY_COPY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '614.1e', '708.8'))
    if version == 13:
        return EntryCopyHandler(handler_id=GRAVEYARD_PAIR_COPY_HANDLER,schema_version=13,
            capability_dependencies=GRAVEYARD_PAIR_COPY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references,'107.1b','614.12b','707.9e'))
    if version == 14:
        return EntryCopyHandler(handler_id=RETAINED_COLOR_ENTRY_HANDLER, schema_version=14,
            capability_dependencies=(ENTRY_COPY_CAPABILITY, RETAINED_COLOR_COPY_CAPABILITY),
            rule_references=(*EntryCopyHandler().rule_references, '707.9a', '707.9c', '707.9d'))
    if version == TEMPORARY_HASTE_COPY_VERSION:
        return EntryCopyHandler(
            handler_id=TEMPORARY_HASTE_COPY_HANDLER, schema_version=TEMPORARY_HASTE_COPY_VERSION,
            capability_dependencies=TEMPORARY_HASTE_COPY_CAPABILITIES,
            rule_references=(*EntryCopyHandler().rule_references, '514.2', '702.10', '707.9a'),
        )
    raise SemanticNodeError('Copy selector version is unsupported')


def _copiable_values(host, target, spec, *, entering=None, target_characteristics=None):
    from ..card_program_faces import selected_face_id
    record = host.card_record(target)
    if target.face_down:
        from ..continuous_effect_model import Layer
        values = dict(host._effective_card_data(target, maximum_layer=Layer.COPY))
        values['activated_abilities'] = []
        identity, face = 'custom-copy:face-down', 'front'
    else:
        values = dict(host._copyable_characteristics(target))
        identity = copiable_oracle_id(target)
        face = selected_face_id(record, target) if record is not None else 'front'
    exception=spec.exception
    if spec.conditional_vanishing:
        from ..keyword_abilities import normalized_characteristic_keywords
        from ..token_copy_recipes import TokenCopyExceptionSpec

        if target_characteristics is None:
            target_characteristics = host._effective_card_data(target)
        if "vanishing" in normalized_characteristic_keywords(target_characteristics):
            exception = TokenCopyExceptionSpec()
    if spec.conditional_exception is not None:
        if entering is None:raise SemanticNodeError('Conditional copy requires its entering source')
        if entering.annotations.get(EMBALMED_DESIGNATION) is not True:
            from ..token_copy_recipes import TokenCopyExceptionSpec
            exception=TokenCopyExceptionSpec()
    if exception.retain_source_stats:
        if entering is None:raise SemanticNodeError('Retained copy stats require the entering source')
        # Printed/prototype spell values, before the copy and battlefield boosts.
        from ..carddb_characteristics import base_card_characteristics
        from ..rules.prototype import prototype_characteristic_values
        original = base_card_characteristics(
            entering, host.card_record(entering),
            cast_face_version=getattr(getattr(host, "state", None), "cast_face_offers_version", None),
        )
        original.update(prototype_characteristic_values(entering))
        try:power,toughness=int(original['power']),int(original['toughness'])
        except (ValueError,TypeError,KeyError) as exc:raise SemanticNodeError('Entering source stats are not represented') from exc
        exception=replace(exception,power=power,toughness=toughness,retain_source_stats=False)
    if spec.legendary_snow and 'creature' in host._type_parts(str(values['type_line']))[0]:
        from ..ability_fragments import ability_fragment_to_dict
        from ..characteristic_fragments import AllCreatureTypesCharacteristicDefinitionSpec

        changeling = ability_fragment_to_dict(AllCreatureTypesCharacteristicDefinitionSpec())
        exception = replace(exception, add_ability_fragments=(changeling,))
    if spec.retains_color:
        if entering is None:
            raise SemanticNodeError('A color-retaining copy requires its receiving object')
        if not target.face_down:
            flip = retained_color_flip_values(host, target, entering=entering, exception=exception)
            if flip is not None:
                return flip
        values = apply_color_retaining_copy(host._copyable_characteristics(entering), values, exception)
    else:
        values = apply_copy_exception(values, exception)
    from ..defined_token_faces import single_face_copy_values
    values = single_face_copy_values(values)
    values[COPIABLE_ORACLE_FIELD], values[COPIABLE_FACE_FIELD] = identity, face
    return values


def retained_color_copy_values(host, target, spec, *, entering):
    if not spec.retains_color:
        raise SemanticNodeError("Retained-color values require the matching copy descriptor")
    return _copiable_values(host, target, spec, entering=entering)


def _copied_entry_effects(host, subject, source, target, values, *, form_key, simultaneous_departures,
                          prospective_types=None):
    """Prebind copied intrinsic producers, enabled only after this copy choice."""
    from ..card_program_faces import program_matches_face
    from ..rules.copied_flip_faces import COPIED_FLIP_FACES
    from ..entry_counters import intrinsic_entry_counter_effects, intrinsic_entry_counters
    from ..entry_state_metrics import entry_condition_metrics
    from .zone_replacements import default_zone_change_replacement_registry
    from .self_entry_counters import dynamic_self_entry_counter_amounts_from_programs
    record = host.card_record(target)
    if record is None or target.face_down:
        card_types, card_subtypes, _ = host._type_parts(str(values["type_line"]))
        return tuple(replace(effect, effect_id=f'copied-entry:{target.ref}:{effect.effect_id}',
            conditions={**dict(effect.conditions), 'entry_copy_form':{'eq':form_key}})
            for effect in intrinsic_entry_counter_effects(
                object_ref=subject.object_ref, destination_controller=subject.destination_controller,
                counters=tuple(counter for counter in intrinsic_entry_counters(values, card_types=card_types,
                    card_subtypes=card_subtypes, keywords=values.get('keywords', ())) if counter.required_type is None)))
    prospective_face = values["name"] if COPIED_FLIP_FACES in values else None
    programs = tuple(p for p in host.semantics.runtime_handler_programs_for_oracle(
        copiable_oracle_id(target,values), active_zone='all', event='zone.change')
        if host.semantic_program_is_current_trusted(p)
        and program_matches_face(record, p, target, prospective_name=prospective_face))
    # This view owns no state. Cast provenance stays on the entering physical
    # object while copiable descriptors come from the selected source.
    preview = replace(source, annotations={**source.annotations, 'copy_overrides':values},
        active_face=values[COPIABLE_FACE_FIELD])
    amounts = dynamic_self_entry_counter_amounts_from_programs(host, programs=programs,
        record=record, card=preview, destination='battlefield',
        destination_controller=subject.destination_controller,
        prospective_name=values['name'], mana_colors_spent=subject.mana_colors_spent)
    types = (tuple(sorted(set().union(*host._type_parts(str(values['type_line'])))))
             if prospective_types is None else prospective_types)
    copied_subject = replace(subject, object_types=types, self_entry_counter_amounts=amounts,
        entry_condition_metrics=entry_condition_metrics(host, subject.destination_controller))
    card_types, card_subtypes, _ = host._type_parts(str(values["type_line"]))
    counter_characteristics = values
    from ..ability_fragment_primitives import current_saga_intrinsic_abilities

    if "saga" in card_subtypes and current_saga_intrinsic_abilities(host.state):
        from ..ability_fragment_primitives import INTRINSIC_ABILITIES_REMOVED
        from .zone_replacement_inputs import prospective_entry_loses_intrinsic_abilities

        if prospective_entry_loses_intrinsic_abilities(
            host, source, subject.destination_controller, copy_values=values,
        ):
            counter_characteristics = {**values, INTRINSIC_ABILITIES_REMOVED: True}
    effects = list(intrinsic_entry_counter_effects(object_ref=subject.object_ref,
        destination_controller=subject.destination_controller,
        counters=intrinsic_entry_counters(counter_characteristics, card_types=card_types,
            card_subtypes=card_subtypes, keywords=values.get('keywords', ()))))
    registry = default_zone_change_replacement_registry()
    for program in programs:
        for index, descriptor in enumerate(program.handlers):
            if descriptor.get('handler_id') in _COPY_VERSIONS:
                continue
            from ..entry_reveal import ENTRY_REVEAL_HANDLER
            from ..day_night_model import DAY_ENTRY_HANDLER
            from ..rules.entry_land_discard import HANDLER as ENTRY_LAND_DISCARD_HANDLER
            from ..devour import DEVOUR_HANDLER
            from ..preparation_model import PREPARATION_HANDLER
            from .self_entry_counters import ENTRY_COUNTER_TRANSFER_HANDLER_ID
            if descriptor.get('handler_id') in {PREPARATION_HANDLER, AMPLIFY_HANDLER, ENTRY_COUNTER_TRANSFER_HANDLER_ID, DAY_ENTRY_HANDLER}:
                from .self_entry_counters import program_static_component_is_applicable
                if not program_static_component_is_applicable(host,program,preview,prospective_zone='battlefield',
                        prospective_controller=subject.destination_controller):continue
            if descriptor.get("handler_id") == AMPLIFY_HANDLER:
                from .amplify import amplify_replacement_effects
                effects.extend(amplify_replacement_effects(host, copied_subject, descriptor,
                    component_id=f"{program.key}:{index}"))
                continue
            if descriptor.get('handler_id')==DEVOUR_HANDLER:
                from .devour import DevourEntryHandler,devour_effects_for_spec
                effects.extend(devour_effects_for_spec(host,copied_subject,DevourEntryHandler().validate(descriptor),component_id=f'{program.key}:{index}'))
                continue
            if descriptor.get('handler_id')==ENTRY_LAND_DISCARD_HANDLER:
                from .entry_land_discard import EntryLandDiscardHandler, entry_land_discard_options

                EntryLandDiscardHandler().validate(descriptor)
                effects.extend(entry_land_discard_options(host,copied_subject,component_id=f'{program.key}:{index}'))
                continue
            if descriptor.get('handler_id')==ENTRY_REVEAL_HANDLER:
                from .entry_reveal import EntryRevealHandler,entry_reveal_effects_for_spec
                effects.extend(entry_reveal_effects_for_spec(host,copied_subject,
                    EntryRevealHandler().validate(descriptor),component_id=f'{program.key}:{index}'))
                continue
            effects.extend(registry.subject_replacement_effects(descriptor, subject=copied_subject,
                component_id=f'{program.key}:{index}', host=host, simultaneous_departures=simultaneous_departures))
    from ..replacement.tribute import copied_tribute_replacement
    return tuple(copied_tribute_replacement(effect,prefix=f'copied-entry:{target.ref}:',form_key=form_key)
        or replace(effect, effect_id=f'copied-entry:{target.ref}:{effect.effect_id}',
            conditions={**dict(effect.conditions), 'entry_copy_form':{'eq':form_key}}) for effect in effects)


def _copy_form_key(values):
    return hashlib.sha256(stable_json(values).encode('utf-8')).hexdigest()


def _copy_form_view(source,values,history=None):
    from ..replacement.face_status_copy import COPY_HISTORY_FIELD

    annotations={**source.annotations,'copy_overrides':dict(values)}
    annotations.pop(COPY_HISTORY_FIELD,None)
    if history is None:
        return replace(source,annotations=annotations,active_face=values.get(COPIABLE_FACE_FIELD))
    annotations[COPY_HISTORY_FIELD]=history.to_dict()
    return replace(source,annotations=annotations,active_face=values.get(COPIABLE_FACE_FIELD),
                   shared_type_doors=history.effective.doors)


def _entry_copy_history(view, spec, values, *, prior_form):
    if not (spec.face_up_or_entry or spec.temporary_haste):
        return None
    from ..replacement.face_status_copy import (
        COPY_HISTORY_FIELD, CopyExpiry, CopyValueState, ScopedCopyValues, current_copy_history, scoped_copy_form,
    )
    if prior_form is not None and COPY_HISTORY_FIELD in view.annotations:
        history = current_copy_history(view)
    else:
        prior = view.annotations.get("copy_overrides")
        if prior_form is None:
            if view.object_kind in {"token", "spell_copy", "card_copy"}:
                prior = current_copy_history(view).baseline.values
            else:
                prior = None
        history = ScopedCopyValues(scoped_copy_form(prior) if prior is not None else CopyValueState(None))
    expiry = CopyExpiry.END_OF_TURN if spec.temporary_haste else CopyExpiry.FACE_DOWN
    return history.apply(expiry, scoped_copy_form(values))


def _copy_programs(host,source,*,controller):
    from ..card_program_faces import program_matches_face
    from .self_entry_counters import program_static_component_is_applicable
    record=host.card_record(source)
    if record is None:return ()
    return tuple(program for program in host.semantics.runtime_handler_programs_for_oracle(
        copiable_oracle_id(source),active_zone='all',event='zone.change')
        if host.semantic_program_is_current_trusted(program) and program_matches_face(record,program,source)
        and program_static_component_is_applicable(host,program,source,prospective_zone='battlefield',
            prospective_controller=controller))


def _copy_candidates(host,spec,*,controller,source_ref):
    query=replace(spec.selection,controller=controller if spec.selection.controller is not None else None,
        excluded_controllers=(controller,) if spec.selection.excluded_controllers else (),
        exclude_ref=source_ref if spec.selection.exclude_ref == '$source.ref' else spec.selection.exclude_ref)
    for target in host._semantic_event_sources(zones=set(query.zones)):
        if query.zones==('graveyard',) and not target.is_card_object:continue
        effective=host._effective_card_data(target)
        row=object_query_result(target,effective,type_parts=host._type_parts(str(effective.get('type_line') or '')),
            known_to_actor=True,attached_to_ref=None,entered_this_turn=(host.state.turn_sequence>0
                and target.entered_battlefield_turn_sequence==host.state.turn_sequence))
        if object_matches_query(row,query):
            yield target, effective


def face_up_copy_spec_for(host, preview):
    """Read the ordinary trusted source producers on the proposed face-up form."""
    programs = _copy_programs(host, preview, controller=preview.controller)
    if preview.permanent_composition is not None or preview.annotations.get("copy_overrides") is not None:
        from ..composition_characteristics import copiable_source_trigger_programs
        from .self_entry_counters import program_static_component_is_applicable

        inherited = copiable_source_trigger_programs(host,host._effective_card_data(preview),"all")
        if inherited is not None:
            programs = tuple(program for program in inherited if program.event == "zone.change"
                and host.semantic_program_is_current_trusted(program)
                and program_static_component_is_applicable(host,program,preview,prospective_zone="battlefield",
                    prospective_controller=preview.controller))
    for program in programs:
        for descriptor in program.handlers:
            if descriptor.get('handler_id') == FACE_UP_OR_ENTRY_COPY_HANDLER:
                return entry_copy_handler(12).validate(descriptor)
    return None


def resolve_face_up_copy(host, card, *, preview, selections):
    from ..replacement.face_status_copy import (
        FACE_UP_COPY_RESULT, FaceUpCopyResult, face_up_copy_batch, face_up_copy_subject,
    )
    from ..replacement.ordering import ReplacementChoiceRequired, advance_replacement_batch

    if not isinstance(selections, (tuple, list)) or len(selections) > 1 or any(
            type(value) is not str or not value for value in selections):
        raise SemanticNodeError('Face-up copy selections must bind one offered choice')
    matching = face_up_copy_spec_for(host, preview)
    if matching is None:
        if selections:
            raise SemanticNodeError('A face-up copy choice lost its trusted replacement source')
        return None
    candidates = tuple(FaceUpCopyResult(target.ref, target.logical_object_id,
        _copiable_values(host,target,matching,entering=card,target_characteristics=effective))
        for target,effective in _copy_candidates(host,matching,controller=card.controller,source_ref=card.ref))
    batch,effects = face_up_copy_batch(face_up_copy_subject(card), candidates,
        apnap_order=tuple(host.apnap_order()))
    progress = advance_replacement_batch(batch, effects, selections=selections)
    if progress.pending is not None:
        raise ReplacementChoiceRequired(batch=progress.batch, effects=effects, pending=progress.pending)
    raw = progress.batch.events[0].payload[FACE_UP_COPY_RESULT]
    return FaceUpCopyResult.from_dict(raw) if raw is not None else None


def _announced_copy_amount(host, source, controller):
    from ..entry_counter_model import DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, DynamicEntryCounterValueSource
    from ..entry_counters import dynamic_entry_counter_amount

    if source.zone != 'stack':
        return 0
    amount_spec = DynamicEntryCounterAmountSpec(DynamicEntryCounterValueSource.CAST_X, DynamicEntryCounterCalculation.MULTIPLY)
    return dynamic_entry_counter_amount(host, card=source, destination_controller=controller, amount_spec=amount_spec)


def _type_directed_copy_counters(types, *, controller, occurrence):
    return tuple(CreateAffectedObjectCounter(counter, 1, controller, occurrence, sequence)
                 for sequence, (kind, counter) in enumerate((('creature', '+1/+1'), ('planeswalker', 'loyalty')))
                 if kind in types)


def _finish_copy_entry_effects(effects, permissions, choice_counters, subject):
    if subject.counter_permissions.rules or any(snapshot.rules for snapshot in permissions.values()):
        effects = {key: replace(effect, operations=(*effect.operations,
            SetField('_counter_permissions', permissions[key].to_dict())))
            if key in permissions else effect for key, effect in effects.items()}
    for key, counters in choice_counters.items():
        effect = effects[key]
        effects[key] = replace(effect, operations=(*effect.operations, *counters))
    return tuple(effects.values())


def _graveyard_pair_replacement(host, subject, occurrence, form_key):
    from ..replacement.operations import GraveyardCopyEntryOperation
    from ..rules.graveyard_entry_copy import graveyard_copy_candidates

    candidates=graveyard_copy_candidates(host,reserved=(subject.object_id,))
    if len(candidates)<2:
        return None
    return ReplacementEffect(effect_id=f"{occurrence}:pair:{form_key or 'original'}",source_id=subject.object_ref,
        event_kind="zone.change",replacement_class=ReplacementClass.ENTERS_COPY,occurrence_id=occurrence,
        conditions={"destination":{"eq":"battlefield"},"object_ref":{"eq":subject.object_ref},
                    "entry_copy_form":{"eq":form_key}},
        operations=(GraveyardCopyEntryOperation(subject.destination_controller,occurrence,candidates),),
        optional=True,label="Exile the creature card to copy first, then the card that supplies power",
    )


def entry_copy_replacement_effects(host, subject, *, prospective_objects=None, simultaneous_departures=(),initial_form=None):
    """Capture the finite graph of acquired copy forms before mutation.

    Each compiled ability occurrence is shared by its candidate alternatives;
    CR614.5 consumption uses that occurrence, while form conditions expose only
    abilities present after the previous copy has actually been selected.
    """
    if subject.destination!='battlefield':return ()
    source=(prospective_objects or {}).get(subject.object_id) or host.state.cards.get(subject.object_id)
    if source is None or host.card_record(source) is None:return ()
    from ..rules.cross_zone_characteristics import battlefield_entry_type_addition_effects
    from .zone_replacement_inputs import conditional_counter_entry_types

    entry_types_apply = bool(battlefield_entry_type_addition_effects(host, subject.destination_controller))
    preview_types = entry_types_apply or any(counter.required_entry_type is not None
                                                      for counter in subject.effect_entry_counters)
    queue=[(source,initial_form)];seen=set();effects={};forms={};counter_permissions={}
    choice_counters = {}
    while queue:
        view,form_key=queue.pop(0)
        key=form_key or '<initial>'
        if key in seen:continue
        seen.add(key)
        for program in _copy_programs(host,view,controller=subject.destination_controller):
            for index,descriptor in enumerate(program.handlers):
                if descriptor.get('handler_id') not in _COPY_VERSIONS:continue
                spec=entry_copy_handler(descriptor.get('schema_version')).validate(descriptor)
                occurrence=f'entry-copy-occurrence:{subject.object_ref}:{program.key}:{index}'
                if spec.graveyard_pair:
                    effect=_graveyard_pair_replacement(host,subject,occurrence,form_key)
                    if effect is not None:
                        effects[effect.effect_id]=effect
                    continue
                candidates = _copy_candidates(host,spec,controller=subject.destination_controller,source_ref=subject.object_ref)
                amount = _announced_copy_amount(host, source, subject.destination_controller) if candidates and spec.announced_x_counter else 0
                for target, target_characteristics in candidates:
                    if spec.predicate is not None and not entry_copy_predicate_matches(host,spec.predicate,entering=source,target=target,controller=subject.destination_controller):continue
                    values = _copiable_values(host, target, spec, entering=view if spec.retains_color else source,
                                              target_characteristics=target_characteristics)
                    history=_entry_copy_history(view,spec,values,prior_form=form_key)
                    serialized=history.to_dict() if history is not None else None
                    next_key=_copy_form_key(values if serialized is None else {"values":values,"scope":serialized})
                    types = (conditional_counter_entry_types(host, source, subject.destination_controller, copy_values=values)
                             if preview_types or spec.type_directed_counters
                             else tuple(sorted(set().union(*host._type_parts(str(values['type_line']))))))
                    if next_key not in forms:
                        forms[next_key]=values
                        for effect in _copied_entry_effects(
                            host, subject, source, target, values, form_key=next_key,
                            simultaneous_departures=simultaneous_departures,
                            prospective_types=types if entry_types_apply or spec.type_directed_counters else None,
                        ):
                            from ..replacement.tribute import copied_tribute_replacement
                            bound=copied_tribute_replacement(effect,prefix=next_key+':',form_key=next_key)
                            bound=bound or replace(effect,effect_id=effect.effect_id+':'+next_key)
                            effects[bound.effect_id]=bound
                        from .riot_grants import granted_riot_entry_effects

                        for riot in granted_riot_entry_effects(host, subject, source, copy_values=values, form_key=next_key):
                            effects[riot.effect_id] = riot
                        queue.append((_copy_form_view(source,values,history),next_key))
                    effect_id=f'{descriptor["handler_id"]}:{subject.object_ref}:{program.key}:{index}:{key}:{target.ref}'
                    from ..replacement.face_status_copy import COPY_HISTORY_FIELD, ENTRY_COPY_HISTORY_FIELD
                    scope_operation = (SetField(ENTRY_COPY_HISTORY_FIELD,serialized),) if (
                        history is not None or COPY_HISTORY_FIELD in view.annotations) else ()
                    effects[effect_id]=ReplacementEffect(effect_id=effect_id,source_id=subject.object_ref,event_kind='zone.change',
                        replacement_class=ReplacementClass.ENTERS_COPY,occurrence_id=occurrence,
                        conditions={'destination':{'eq':'battlefield'},'object_ref':{'eq':subject.object_ref},'entry_copy_form':{'eq':form_key}},
                        operations=(SetField('entry_copy_source',target.ref),SetField('entry_copy_form',next_key),
                            SetField('entry_copy',values),SetField('object_types',types),*scope_operation,
                            *((SetField('tapped', True),) if spec.tapped else ())),
                        decline_operations=(SetField('entry_copy_source','declined'),),optional=True,
                        label=f'{subject.object_ref}: enter{" tapped" if spec.tapped else ""} as a copy of {target.ref}')
                    if spec.controlled_shield and target.controller == subject.destination_controller:
                        choice_counters[effect_id] = (CreateAffectedObjectCounter(
                            'shield', 1, subject.destination_controller, occurrence),)
                    if amount:
                        choice_counters[effect_id] = (CreateAffectedObjectCounter(
                            '+1/+1', amount, subject.destination_controller, occurrence),)
                    if spec.type_directed_counters:
                        choice_counters[effect_id] = _type_directed_copy_counters(
                            types, controller=subject.destination_controller, occurrence=occurrence)
                    if spec.legendary_snow and 'creature' in host._type_parts(str(values['type_line']))[0]:
                        choice_counters[effect_id] = (CreateAffectedObjectCounter(
                            '+1/+1', 2, subject.destination_controller, occurrence),)
                    from .counter_permissions import copied_entry_counter_permissions
                    counter_permissions[effect_id] = copied_entry_counter_permissions(
                        host, subject, _copy_form_view(source, values))
    return _finish_copy_entry_effects(effects, counter_permissions, choice_counters, subject)
