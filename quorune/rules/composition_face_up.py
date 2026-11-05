"""Current inherited face-up prices through detached characteristic reads."""
from collections import ChainMap
from dataclasses import dataclass,replace
from types import MappingProxyType
from ..ability_fragments import canonical_ability_fragments
from ..card_program_faces import normalized_program_face_id
from ..card_programs import bind_card_program_runtime
from ..card_programs.admission import program_has_complete_card_program_admission
from ..characteristic_read_frames import CharacteristicReadHost,CharacteristicStateView
from ..continuous_effect_model import ContinuousObjectIdentity
from ..copiable_program_source import CopiableProgramSourceSpec
from ..entry_copy import COPIABLE_ORACLE_FIELD,COPIABLE_FACE_FIELD
from ..model import CardInstance
from ..face_status_timestamp_model import planned_face_up_timestamp
from ..morph import FACE_DOWN_CAST_METHODS,FACE_DOWN_METHOD_RUNTIME_EVENTS,FixedManaMorphSpec
from ..nonmana_morph_model import FixedNonmanaMorphSpec
from ..manifest import ManifestTurnUpSpec,is_manifested,physical_manifest_turn_up_spec
from ..semantic_runtime.morph import default_fixed_mana_morph_registry
from ..zone_object_state import turn_card_face_up
from .capabilities import load_default_capability_registry
from .composition_faces import prepare_composition_face_up


@dataclass(frozen=True,slots=True)
class ComposedTurnUpOption:
    source_key: str
    source_oracle_id: str
    spec: FixedManaMorphSpec | FixedNonmanaMorphSpec | ManifestTurnUpSpec

    def __post_init__(self):
        if (any(type(value) is not str or not value or value!=value.strip()
                for value in (self.source_key,self.source_oracle_id))
                or not isinstance(self.spec,(FixedManaMorphSpec,FixedNonmanaMorphSpec,ManifestTurnUpSpec))):
            raise ValueError('Composed turn-up choices require their source identity and typed price')


def composition_face_up_read_view(host,card):
    plan=prepare_composition_face_up(host,ContinuousObjectIdentity(card.object_id,card.logical_object_id))
    if plan is None or not plan.can_turn_face_up:return None
    preview=CardInstance.from_dict(card.to_dict())
    turn_card_face_up(preview,viewers=plan.viewers,timestamp=planned_face_up_timestamp(host.state))
    preview=replace(preview,permanent_composition=plan.after)
    state=CharacteristicStateView(host.state,
        ChainMap(MappingProxyType({card.object_id:preview}),host.state.cards),host.state.players)
    reader=CharacteristicReadHost(host,state)
    return reader,preview,reader._effective_card_data(preview,ignore_face_down=True)


def composed_turn_up_options(host,seat,card):
    if (card.permanent_composition is None or card.zone!='battlefield' or card.controller!=seat
            or card.phased_out or not card.face_down):return ()
    result=composition_face_up_read_view(host,card)
    if result is None:return ()
    _reader,_preview,data=result
    methods=set(FACE_DOWN_CAST_METHODS).intersection(str(word).casefold() for word in data.get('keywords',()))
    manifest_option=None
    storage=card.permanent_composition
    if is_manifested(card) and not storage.topology.is_token:
        top=storage.parts[0]
        spec=physical_manifest_turn_up_spec(host,top.detached_card())
        if spec is not None:
            key=top.card_state['oracle_id']+':manifest:'+top.identity.logical_object_id
            manifest_option=ComposedTurnUpOption(key,top.card_state['oracle_id'],spec)
    if not methods and manifest_option is None:return ()
    references=tuple(fragment for fragment in canonical_ability_fragments(data.get('ability_fragments',()))
        if isinstance(fragment,CopiableProgramSourceSpec))
    if not references and data.get(COPIABLE_ORACLE_FIELD):
        references=(CopiableProgramSourceSpec(data[COPIABLE_ORACLE_FIELD],data.get(COPIABLE_FACE_FIELD,'front')),)
    capabilities=load_default_capability_registry();components=default_fixed_mana_morph_registry()
    options={manifest_option.source_key:manifest_option} if manifest_option is not None else {}
    seen=set()
    for reference in references:
        identity=(reference.oracle_id,reference.face_id)
        if identity in seen:continue
        seen.add(identity)
        record=host.card_db.by_oracle_id(reference.oracle_id)
        program=host.semantics.card_program_for_oracle(reference.oracle_id)
        if record is None or program is None:return ()
        if not bind_card_program_runtime(program,capability_registry=capabilities,
                profile=host.state.config.review_profile)['strict_capability_ready']:return ()
        for method in FACE_DOWN_CAST_METHODS:
            if method not in methods:continue
            for current in host.semantics.runtime_handler_programs_for_oracle(reference.oracle_id,
                    active_zone='all',event=FACE_DOWN_METHOD_RUNTIME_EVENTS[method]):
                if normalized_program_face_id(record,current)!=reference.face_id:continue
                if (not host.semantic_program_is_current_trusted(current)
                        or not program_has_complete_card_program_admission(current)):return ()
                for index,descriptor in enumerate(current.handlers):
                    if components.describe(str(descriptor.get('handler_id') or '')) is None:continue
                    for spec in components.lower(descriptor,None):
                        if spec.method!=method:continue
                        key=current.key+':'+str(index)
                        options[key]=ComposedTurnUpOption(key,reference.oracle_id,spec)
    marker=card.annotations.get('face_down_method')
    original_method=marker.get('kind') if isinstance(marker,dict) else None
    return tuple(sorted(options.values(),key=lambda option:(
        option.spec.method!='manifest',
        not (option.source_oracle_id==card.oracle_id and option.spec.method==original_method),option.source_key)))


def selected_composed_turn_up_option(host,seat,card,*,method=None,source_key=None):
    return next((option for option in composed_turn_up_options(host,seat,card)
        if (method is None or option.spec.method==method)
        and (source_key is None or option.source_key==source_key)),None)
