"""Pinned CR111.10i/701.53: defined faces are distinct copiable values."""

from copy import deepcopy
from dataclasses import replace
import unittest

from quorune.defined_token_faces import (
    DEFINED_TOKEN_FACES_FIELD, DEFINED_TOKEN_ACTIVE_FACE_FIELD, DEFINED_TOKEN_TRANSFORM_KEY,
    DefinedTokenFace, DefinedTransformingToken, INCUBATOR_TRANSFORM_PROFILE, incubator_definition,
)


class DefinedTokenFaceTests(unittest.TestCase):
    def test_token_copy_compiler_requires_the_complete_physical_face_dependency(self):
        from dataclasses import replace
        from copy import deepcopy
        import json
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        card=replace(query_record("Create a token that's a copy of target creature you control."),type_line='Instant')
        self.assertEqual('exact',compile_oracle_card(card,capability_registry=load_default_capability_registry()).status)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        discovered=set(discover_tests(ROOT).values())
        for capability in ('token.creation.defined_transforming','permanent.transform.face_change'):
            changed=deepcopy(raw)
            next(row for row in changed['capabilities'] if row['id']==capability).update(
                status='blocked',blockers=['Physical copy-face dependency withdrawn'])
            registry=CapabilityRegistry(changed)
            index=build_index(registry_value=changed,declaration_source=declarations,discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            with self.subTest(capability=capability):
                self.assertNotEqual('exact',compile_oracle_card(card,capability_registry=registry).status)
                self.assertEqual('exact',compile_oracle_card(replace(query_record('Draw a card.'),type_line='Instant'),capability_registry=registry).status)

    def copied_face(self,name='physical-front',*,value_name='Copied creature',type_line='Creature — Elf'):
        from quorune.defined_token_faces import CopiableTokenFace
        return CopiableTokenFace(name,{'name':value_name,'mana_cost':'{G}','mana_value':1,
            'type_line':type_line,'colors':['G'],'keywords':[],'produced_mana':[],
            'power':'*','toughness':'2','ability_fragments':[],'activated_abilities':[],
            '_copiable_oracle_id':'fixture:complete-source','_copiable_face_id':name})

    def test_full_copiable_faces_keep_physical_orientation_separate_from_equal_copied_names(self):
        from quorune.defined_token_faces import CopiableTokenFace
        front=self.copied_face();back=self.copied_face('physical-back')
        definition=DefinedTransformingToken(front,back,schema_version=2)
        self.assertEqual(definition,DefinedTransformingToken.from_dict(definition.to_dict()))
        self.assertEqual(back,definition.other_face('physical-front'))
        self.assertEqual('Copied creature',definition.current_face('physical-back').characteristics()['name'])
        self.assertEqual('*',front.characteristics()['power'])
        raw=definition.to_dict();raw['front']['characteristics']['keywords'].append('Flying')
        self.assertEqual([],front.characteristics()['keywords'])
        for value in ({**front.to_dict(),'schema_version':True},{**front.to_dict(),'extra':1},
                {**front.to_dict(),'name':''},{**front.to_dict(),'characteristics':{**front.characteristics(),'counters':{'+1/+1':4}}},
                {**front.to_dict(),'characteristics':{**front.characteristics(),'mana_value':True}},
                {**front.to_dict(),'characteristics':{**front.characteristics(),'activated_abilities':[{}]}}):
            with self.subTest(value=value),self.assertRaises(ValueError):CopiableTokenFace.from_dict(value)
        with self.assertRaises(ValueError):DefinedTransformingToken(front,incubator_definition().back,schema_version=2)

    def test_full_face_copy_exceptions_are_creation_values_on_both_physical_sides(self):
        from quorune.defined_token_faces import (
            DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD,DEFINED_TOKEN_IDENTITY_FIELD,defined_token_creation_identity,
        )
        from quorune.token_copy_recipes import TokenCopyExceptionSpec
        definition=DefinedTransformingToken(self.copied_face(),self.copied_face('physical-back'),schema_version=2)
        values={DEFINED_TOKEN_FACES_FIELD:definition.to_dict(),DEFINED_TOKEN_ACTIVE_FACE_FIELD:'physical-back',
            DEFINED_TOKEN_COPY_EXCEPTIONS_FIELD:[TokenCopyExceptionSpec(power=4,toughness=4,add_keywords=('Flying',)).to_dict()]}
        physical=DefinedTransformingToken.from_dict(defined_token_creation_identity(values)[DEFINED_TOKEN_IDENTITY_FIELD])
        self.assertEqual(('physical-front','physical-back'),(physical.front.name,physical.back.name))
        for face in (physical.front,physical.back):
            data=face.characteristics();self.assertEqual(('4','4', ['Flying']),(data['power'],data['toughness'],data['keywords']))

    def test_predefined_incubator_has_colorless_artifact_front_and_zero_zero_creature_back(self):
        definition=incubator_definition()
        front=definition.current_face(None).characteristics()
        back=definition.other_face(None).characteristics()
        self.assertEqual(('Incubator','Token Artifact — Incubator',None,None),
            (front['name'],front['type_line'],front['power'],front['toughness']))
        self.assertEqual(('Phyrexian Token','Token Artifact Creature — Phyrexian','0','0'),
            (back['name'],back['type_line'],back['power'],back['toughness']))
        self.assertEqual([],front['colors']);self.assertEqual([],back['colors'])
        self.assertEqual(INCUBATOR_TRANSFORM_PROFILE,front['activated_ability_profile'])
        self.assertNotIn('activated_ability_profile',back)
        self.assertEqual('{2}: Transform this token.',front['display_text'])
        self.assertEqual('',front['executable_oracle_text'])

    def test_face_lookup_and_switching_are_closed_and_names_remain_data(self):
        definition=incubator_definition()
        self.assertEqual(definition.back,definition.other_face(definition.front.name))
        self.assertEqual(definition.front,definition.other_face(definition.back.name))
        for face in ('unknown','',True,0):
            with self.subTest(face=face),self.assertRaises(ValueError):definition.current_face(face)
        renamed=replace(definition,front=replace(definition.front,name='Renamed face'),
                         back=replace(definition.back,name='Another face'))
        self.assertEqual(INCUBATOR_TRANSFORM_PROFILE,renamed.current_face('Renamed face').ability_profile)
        self.assertIsNone(renamed.current_face('Another face').ability_profile)

    def test_serialized_definitions_and_characteristics_do_not_alias_frozen_values(self):
        definition=incubator_definition();payload=definition.to_dict()
        self.assertEqual(definition,DefinedTransformingToken.from_dict(payload))
        payload['back']['power']='99'
        self.assertEqual('0',definition.back.power)
        characteristics=definition.front.characteristics();characteristics['colors'].append('R')
        self.assertEqual([],definition.front.characteristics()['colors'])

    def test_counter_state_and_malformed_face_data_are_not_copiable_definition_fields(self):
        value=incubator_definition().to_dict()
        for change in ({'counters':{'+1/+1':4}},{'schema_version':True},{'schema_version':2},
                       {'back':None},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                DefinedTransformingToken.from_dict({**value,**change})
        with self.assertRaises(ValueError):replace(incubator_definition(),back=incubator_definition().front)
        for field in value:
            bad=deepcopy(value);del bad[field]
            with self.subTest(missing=field),self.assertRaises(ValueError):DefinedTransformingToken.from_dict(bad)

    def test_unrepresented_profiles_and_nonfixed_statistics_reject(self):
        face=incubator_definition().back
        for change in ({'power':True},{'power':'*'},{'power':'01'},{'power':'-0'},
                       {'toughness':None},{'ability_profile':'arbitrary'},
                       {'type_line':'Token Instant'},{'schema_version':False}):
            with self.subTest(change=change),self.assertRaises(ValueError):replace(face,**change)
        front=incubator_definition().front
        with self.assertRaises(ValueError):replace(front,power='0',toughness='0')
        with self.assertRaises(ValueError):DefinedTokenFace.from_dict({**front.to_dict(),'unknown':True})


class DefinedTokenRuntimeTests(unittest.TestCase):
    def test_copied_token_created_with_a_spell_face_cannot_transform_even_under_a_creature_copy(self):
        from types import SimpleNamespace
        from quorune.characteristic_evaluation import type_parts
        from quorune.defined_token_faces import DEFINED_TOKEN_IDENTITY_FIELD
        from quorune.permanent_transform import transform_permanent
        fixture=DefinedTokenFaceTests()
        definition=DefinedTransformingToken(
            fixture.copied_face(type_line='Sorcery'),fixture.copied_face('physical-back'),schema_version=2)
        card=self.token();card.active_face='physical-back'
        card.annotations[DEFINED_TOKEN_IDENTITY_FIELD]=definition.to_dict()
        card.annotations['copy_overrides']=fixture.copied_face(value_name='Current copied creature').characteristics()
        host=SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=None),_type_parts=type_parts)
        before=card.to_dict()
        self.assertIsNone(transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False))
        self.assertEqual(before,card.to_dict())

    def token(self):
        from quorune.model import CardInstance
        from quorune.standard_token_abilities import standard_token_characteristics
        characteristics=standard_token_characteristics({DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()})
        return CardInstance(object_id='token-1',ref='T1',oracle_id='custom-token:incubator',
            printed_name='Incubator',owner='A',controller='B',zone='battlefield',is_token=True,
            active_face='Incubator',zone_timestamp=7,counters={'+1/+1':4},marked_damage=1,
            annotations={'token_characteristics':deepcopy(characteristics),'copy_overrides':deepcopy(characteristics),
                'defined_token_faces':incubator_definition().to_dict(),'token_copy_value_version':1})

    def test_readers_select_current_face_and_only_front_has_the_typed_activation(self):
        from quorune.carddb_characteristics import base_card_characteristics
        from quorune.compiled_activated_abilities import _custom_activated_abilities
        card=self.token()
        for face,expected_type,expected_name,count in (
            ('Incubator','Token Artifact — Incubator','Incubator',1),
            ('Phyrexian Token','Token Artifact Creature — Phyrexian','Phyrexian Token',0),
        ):
            card.active_face=face
            data=base_card_characteristics(card,None)
            self.assertEqual((expected_name,expected_type),(data['name'],data['type_line']))
            abilities=_custom_activated_abilities(card)
            self.assertEqual(count,len(abilities))
            if abilities:
                ability=abilities[0]
                self.assertEqual({'GENERIC':2},{key:value for key,value in ability.mana.items() if value})
                self.assertFalse(ability.tap_source);self.assertFalse(ability.sacrifice_source)
                self.assertFalse(ability.mana_ability);self.assertFalse(ability.sorcery_speed)
                self.assertEqual(DEFINED_TOKEN_TRANSFORM_KEY,ability.builtin_semantic_key)
            else:self.assertEqual(('0','0'),(data['power'],data['toughness']))

    def test_transform_keeps_object_state_and_old_activation_cannot_transform_twice(self):
        from types import SimpleNamespace
        from quorune.permanent_transform import transform_permanent
        from quorune.model import CardInstance
        card=self.token();before=card.to_dict()
        host=SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=None))
        result=transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False)
        self.assertEqual(('Incubator','Phyrexian Token',1),
            (result.previous_face,result.current_face,result.transform_count))
        after=card.to_dict()
        for key,value in before.items():
            if key not in {'active_face','transform_count'}:self.assertEqual(value,after[key],key)
        restored=CardInstance.from_dict(after)
        self.assertIsNone(transform_permanent(host,restored,expected_transform_count=0,day_night_instruction=False))
        result=transform_permanent(host,restored,expected_transform_count=1,day_night_instruction=False)
        self.assertEqual('Incubator',result.current_face)

    def test_defined_token_transform_uses_modern_clock_and_preserves_legacy_absence(self):
        from types import SimpleNamespace
        from quorune.permanent_transform import transform_permanent
        card=self.token();identity=card.logical_object_id
        host=SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=1,timestamp_sequence=10))
        self.assertIsNotNone(transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False))
        self.assertEqual((11,11,1,identity),(host.state.timestamp_sequence,card.zone_timestamp,card.transform_count,card.logical_object_id))
        self.assertIsNone(transform_permanent(host,card,expected_transform_count=0,day_night_instruction=False))
        host.state.transform_timestamp_version=None
        self.assertIsNotNone(transform_permanent(host,card,expected_transform_count=1,day_night_instruction=False))
        self.assertEqual((11,11,2),(host.state.timestamp_sequence,card.zone_timestamp,card.transform_count))

    def test_copiable_snapshot_selects_back_face_without_copying_counters(self):
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        card=self.token();card.active_face='Phyrexian Token'
        copied=copiable_card_characteristics(card,None,base_card_characteristics(card,None))
        self.assertEqual('Phyrexian Token',copied[DEFINED_TOKEN_ACTIVE_FACE_FIELD])
        self.assertEqual('Token Artifact Creature — Phyrexian',copied['type_line'])
        self.assertEqual([],copied['activated_abilities'])
        self.assertNotIn('counters',copied)
        self.assertEqual(incubator_definition().to_dict(),copied[DEFINED_TOKEN_FACES_FIELD])

    def test_builtin_resolution_and_activation_snapshot_share_transform_count_contract(self):
        from types import SimpleNamespace
        from quorune.rules.activation.resolution import builtin_activation_resolution
        from quorune.rules.activation.transform_snapshot import activation_transform_context
        proposal=SimpleNamespace(semantic_key=DEFINED_TOKEN_TRANSFORM_KEY)
        card=self.token();card.transform_count=3
        self.assertEqual({'source_transform_count':3},activation_transform_context(None,proposal,card))
        effects=builtin_activation_resolution(DEFINED_TOKEN_TRANSFORM_KEY,'B').effect_dicts()
        self.assertEqual([{'op':'transform','card':'$source.zone_object',
            'expected_transform_count':'$source.transform_count'}],effects)
        card.is_token=False
        self.assertEqual({'source_transform_count':3},activation_transform_context(None,proposal,card))

    def test_copy_exceptions_survive_both_faces_and_repeated_reads(self):
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        from quorune.token_copy_recipes import TokenCopyExceptionSpec,apply_copy_exception
        card=self.token()
        card.annotations['copy_overrides']=apply_copy_exception(card.annotations['copy_overrides'],
            TokenCopyExceptionSpec(name='Copied object',power=4,toughness=4,colors=('U',),add_keywords=('Flying',),schema_version=3))
        for face in ('Incubator','Phyrexian Token','Incubator'):
            card.active_face=face
            for _ in range(2):
                data=base_card_characteristics(card,None)
                self.assertEqual(('Copied object','4','4',['U'],['Flying']),
                    (data['name'],data['power'],data['toughness'],data['colors'],data['keywords']))
                copied=copiable_card_characteristics(card,None,data)
                self.assertEqual(face,copied[DEFINED_TOKEN_ACTIVE_FACE_FIELD])

    def test_single_faced_card_copy_does_not_export_or_gain_a_transforming_token_definition(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        from quorune.permanent_transform import transform_permanent
        card=self.token();card.is_token=False;card.annotations.pop('token_characteristics')
        copied=copiable_card_characteristics(card,None,base_card_characteristics(card,None))
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,copied)
        self.assertNotIn(DEFINED_TOKEN_ACTIVE_FACE_FIELD,copied)
        self.assertEqual('Incubator',copied['name'])
        self.assertIsNone(transform_permanent(SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=None)),card,expected_transform_count=0,day_night_instruction=False))

    def test_untyped_entry_counter_inputs_reject_before_creation(self):
        from quorune.token_creation import create_tokens,TokenCreationError
        for invalid in ({'amount':3},[{'counter_name':'+1/+1','amount':3}],None):
            with self.subTest(invalid=invalid),self.assertRaises(TokenCreationError):
                create_tokens(None,'A',name='Incubator',effect_entry_counters=invalid)

    def test_entering_as_a_copy_does_not_make_a_single_faced_token_double_faced(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        from quorune.permanent_transform import transform_permanent
        card=self.token();card.annotations.pop('defined_token_faces')
        card.annotations['token_characteristics']={'name':'Original single-faced token','type_line':'Artifact'}
        card.active_face=None
        self.assertIsNone(transform_permanent(SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=None)),card,expected_transform_count=0,day_night_instruction=False))
        copied=copiable_card_characteristics(card,None,base_card_characteristics(card,None))
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,copied)
        self.assertEqual('Incubator',copied['name'])

    def test_ongoing_copy_uses_one_frozen_face_but_does_not_remove_physical_faces(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        from quorune.permanent_transform import transform_permanent
        from quorune.standard_token_abilities import standard_token_characteristics
        card=self.token()
        card.annotations['copy_overrides']={'name':'Copied Relic','type_line':'Artifact',
            'display_text':'','executable_oracle_text':'','power':None,'toughness':None,
            'colors':['U'],'keywords':[],'activated_abilities':[],'ability_fragments':[]}
        self.assertEqual('Copied Relic',base_card_characteristics(card,None)['name'])
        result=transform_permanent(SimpleNamespace(state=SimpleNamespace(transform_timestamp_version=None)),card,expected_transform_count=0,day_night_instruction=False)
        self.assertEqual('Phyrexian Token',result.current_face)
        self.assertEqual('Copied Relic',base_card_characteristics(card,None)['name'])
        copied=copiable_card_characteristics(card,None,base_card_characteristics(card,None))
        for face in ('Incubator','Phyrexian Token'):
            data=standard_token_characteristics(copied,active_face=face)
            self.assertEqual(('Copied Relic',['U'],[]),(data['name'],data['colors'],data['activated_abilities']))
        self.assertNotIn('counters',copied)

    def test_direct_copy_of_single_faced_card_copy_does_not_reintroduce_defining_annotations(self):
        from types import SimpleNamespace
        from quorune.carddb_characteristics import base_card_characteristics,copiable_card_characteristics
        from quorune.token_creation_identity import _copied_token_identity
        card=self.token();card.is_token=False;card.annotations.pop('defined_token_faces')
        host=SimpleNamespace(_resolve_object=lambda *args,**kwargs:card,
            state=SimpleNamespace(token_copy_value_version=1),
            _copyable_characteristics=lambda source:copiable_card_characteristics(source,None,base_card_characteristics(source,None)),
            display_name=lambda object_id:'Incubator')
        identity=_copied_token_identity(host,'A',copy_of=card.ref,name='',characteristics={},ref='T2')
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,identity[3]['copy_overrides'])
        self.assertNotIn('defined_token_faces',identity[3])

    def test_uniform_copy_values_reject_physical_identity_state_and_nested_face_metadata(self):
        from quorune.standard_token_abilities import standard_token_characteristics
        from quorune.defined_token_faces import DEFINED_TOKEN_UNIFORM_COPY_FIELD
        for bad in (None,[],{'counters':{}},{'controller':'C'},{'transform_count':3},
                    {DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                standard_token_characteristics({DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict(),
                    DEFINED_TOKEN_UNIFORM_COPY_FIELD:bad})

    def test_ordinary_copy_uses_copiable_values_without_source_cleanup_or_designations(self):
        from types import SimpleNamespace
        from quorune.token_creation_identity import _copied_token_identity
        card=self.token();card.is_token=False
        card.annotations={'until_end_of_turn':{'copy_overrides_previous':None},'noncopiable_designation':True}
        values={'name':'Ordinary body','type_line':'Creature — Spirit','power':'2','toughness':'2',
            'activated_abilities':[],'ability_fragments':[]}
        host=SimpleNamespace(_resolve_object=lambda *args,**kwargs:card,
            state=SimpleNamespace(token_copy_value_version=1),
            _copyable_characteristics=lambda source:deepcopy(values),display_name=lambda object_id:'Ordinary body')
        identity=_copied_token_identity(host,'A',copy_of=card.ref,name='',characteristics={},ref='T2')
        self.assertEqual({'copied_from','copy_overrides','token_copy_value_version'},set(identity[3]))
        self.assertEqual(1,identity[3]['token_copy_value_version'])
        self.assertEqual('Ordinary body',identity[2])
        self.assertEqual(values,identity[3]['copy_overrides'])

    def test_single_faced_token_copy_values_supersede_its_original_token_definition(self):
        from quorune.defined_token_faces import single_face_copy_values
        from quorune.compiled_activated_abilities import _custom_activated_abilities
        from quorune.carddb_characteristics import base_card_characteristics
        card=self.token();card.annotations.pop('defined_token_faces');card.active_face=None
        card.annotations['copy_overrides']=single_face_copy_values(card.annotations['copy_overrides'])
        card.annotations['token_characteristics']={'name':'Original token','type_line':'Token Artifact — Clue',
            'activated_abilities':[],'display_text':'','executable_oracle_text':''}
        self.assertEqual('Token Artifact — Incubator',base_card_characteristics(card,None)['type_line'])
        self.assertEqual(DEFINED_TOKEN_TRANSFORM_KEY,_custom_activated_abilities(card)[0].builtin_semantic_key)

    def test_physical_identity_from_copied_values_mutant_is_killed(self):
        from unittest.mock import patch
        with patch('quorune.permanent_transform.defined_token_definition',return_value=incubator_definition()):
            with self.assertRaises(AssertionError):
                self.test_entering_as_a_copy_does_not_make_a_single_faced_token_double_faced()


class IncubateProducerTests(unittest.TestCase):
    def effect(self,amount=4,repetitions=1):
        return {'op':'incubate','schema_version':1,'controller':'$controller','source':'$source',
            'amount':amount,'repetitions':repetitions}

    def test_fixed_zero_and_twice_are_single_source_bound_instructions(self):
        from quorune.compiler.incubate_templates import incubate_effect_template
        for text,amount,repetitions in (('Incubate 0.',0,1),('Incubate 4.',4,1),('Incubate 5 twice.',5,2)):
            result=incubate_effect_template(text,source_name='Source')
            self.assertEqual('incubate-v1',result[0]);self.assertEqual((self.effect(amount,repetitions),),result[1])
            self.assertIsNone(result[2])
        for text in ('Incubate -1.','Incubate 04.','Incubate 4 three times.','Incubate 4 and draw a card.',
                     'Target opponent incubates 4.','Incubate X.'):
            with self.subTest(text=text):self.assertIsNone(incubate_effect_template(text,source_name='Source'))

    def test_declared_public_amount_is_owned_and_repetitions_are_preserved(self):
        from quorune.compiler.incubate_templates import incubate_effect_template
        from quorune.incubate_model import validate_incubate_effect
        from quorune.query_effect_amount_model import PUBLIC_QUERY_AMOUNT_KIND
        result=incubate_effect_template('Incubate X twice, where X is the number of lands you control.',source_name='Source')
        self.assertIsNotNone(result)
        effect=result[1][0]
        validate_incubate_effect(effect)
        self.assertIsInstance(effect['amount'],dict);self.assertEqual(2,effect['repetitions'])
        self.assertEqual(PUBLIC_QUERY_AMOUNT_KIND,effect['amount']['kind'])

    def test_closed_effect_rejects_unknown_fields_booleans_and_unresolved_internal_amounts(self):
        from quorune.incubate_model import validate_incubate_effect
        for change in ({'amount':True},{'amount':-1},{'amount':{}},{'schema_version':True},
                       {'repetitions':3},{'repetitions':True},{'controller':False},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):validate_incubate_effect({**self.effect(),**change})
        step={**self.effect(),'op':'incubate_next','remaining':1};step.pop('repetitions')
        validate_incubate_effect(step,resolved=True)
        with self.assertRaises(ValueError):validate_incubate_effect({**step,'remaining':2})

    def test_intent_identity_and_replacement_queue_keep_resolved_source_amount(self):
        from quorune.incubate_model import IncubateIntent,decode_incubate_intent_identity
        from quorune.semantic_choices.intent_replacement import (semantic_intent_identity,
            validate_semantic_intent_identity,with_replacement_selections)
        intent=IncubateIntent('A','A',4,'source@1','Incubate witness')
        kind,identity=semantic_intent_identity(intent)
        self.assertEqual('incubate',kind);self.assertEqual(identity,decode_incubate_intent_identity(identity))
        self.assertEqual(identity,validate_semantic_intent_identity(kind,identity))
        resumed=with_replacement_selections(intent,('replacement-1',))
        self.assertEqual(identity,resumed.identity());self.assertEqual(('replacement-1',),resumed.replacement_selections)
        for change in ({'amount':True},{'controller':'B'},{'source_ref':''},{'schema_version':True},{'extra':0}):
            with self.subTest(change=change),self.assertRaises(ValueError):decode_incubate_intent_identity({**identity,**change})

    def test_preparation_creates_once_and_retains_one_following_incubation_at_the_same_amount(self):
        from types import SimpleNamespace
        from quorune.semantic_choices.incubate import INCUBATE_HANDLERS
        context=SimpleNamespace(actor='A',source_ref='source@1',stack_label='Incubate witness',
            query=SimpleNamespace(active_seats=('A','B','C','D')))
        effect={**self.effect(5,2),'controller':'A','source':'source@1'}
        first=INCUBATE_HANDLERS[0].prepare(effect,context)
        self.assertEqual(1,len(first.preparation_intents));self.assertEqual(5,first.preparation_intents[0].amount)
        self.assertEqual(1,len(first.auto_continue.prepend_effects))
        follow=first.auto_continue.prepend_effects[0]
        self.assertEqual((5,1),(follow['amount'],follow['remaining']))
        second=INCUBATE_HANDLERS[1].prepare(follow,context)
        self.assertEqual(5,second.preparation_intents[0].amount);self.assertEqual((),second.auto_continue.prepend_effects)
        with self.assertRaises(ValueError):INCUBATE_HANDLERS[0].prepare({**effect,'source':'forged-source'},context)

    def test_execution_passes_typed_entry_counters_to_one_creation_including_zero(self):
        from unittest.mock import patch
        from quorune.incubate_model import IncubateIntent
        from quorune.incubate_execution import execute_incubate_intent
        for amount in (0,4):
            with self.subTest(amount=amount),patch('quorune.incubate_execution.create_tokens',return_value=['T1']) as create:
                self.assertEqual(('T1',),execute_incubate_intent(None,IncubateIntent('A','A',amount,'source@1','Incubate witness')))
                kwargs=create.call_args.kwargs
                self.assertEqual(1,kwargs['quantity'])
                self.assertNotIn('counters',kwargs['characteristics'])
                counters=kwargs['effect_entry_counters']
                self.assertEqual(1 if amount else 0,len(counters))
                if counters:self.assertEqual(('+1/+1',4,'A','source@1'),
                    (counters[0].counter_name,counters[0].amount,counters[0].placing_player,counters[0].source_ref))

    def test_capability_shape_and_payload_minimum_fail_closed_on_widened_authority(self):
        from quorune.incubate_model import INCUBATE_DEPENDENCIES,incubate_payload_dependencies
        from quorune.rules.incubate_capability_shapes import incubate_node_capabilities
        effect=self.effect()
        self.assertEqual(INCUBATE_DEPENDENCIES,incubate_payload_dependencies(effect))
        self.assertEqual(INCUBATE_DEPENDENCIES,incubate_node_capabilities(effects=(effect,),target_schema=None,mechanic_ids={'incubate'}))
        for change in ({'controller':'B'},{'source':'other'},{'repetitions':3},{'amount':True}):
            with self.subTest(change=change):
                self.assertEqual((),incubate_node_capabilities(effects=({**effect,**change},),target_schema=None,mechanic_ids={'incubate'}))

    def test_incubate_source_spans_and_dependency_compiler_mutations_fail_closed(self):
        import json
        from common import ROOT
        from unittest.mock import patch
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.incubate_model import INCUBATE_DEPENDENCIES
        source=replace(query_record('Incubate 4.'),type_line='Sorcery',power=None,toughness=None,keywords=('Incubate',))
        ir=compile_oracle_card(source,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(value.kind,value.reason,value.blockers) for value in ir.material_residuals])
        node=ir.faces[0].nodes[0]
        self.assertEqual(source.oracle_text[node.span.start:node.span.end],node.text)
        self.assertEqual((self.effect(),),node.effects)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in INCUBATE_DEPENDENCIES:
            changed=deepcopy(raw)
            cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn for the focused dependency mutation']
            with self.subTest(dependency=dependency):
                self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                    capability_profile='commander_review').status)
        with patch('quorune.compiler.incubate_templates.incubate_effect_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=load_default_capability_registry(),
                capability_profile='commander_review').status)

    def test_changed_entry_counter_amount_mutant_is_killed(self):
        from unittest.mock import patch
        from quorune.entry_counter_model import EffectEntryCounter
        wrong=EffectEntryCounter('+1/+1',1,'A','source@1','701.53a')
        result=unittest.TestResult()
        with patch('quorune.incubate_execution.EffectEntryCounter',return_value=wrong):
            type(self)('test_execution_passes_typed_entry_counters_to_one_creation_including_zero').run(result)
        self.assertEqual(1,len(result.failures));self.assertEqual([],result.errors)

    def test_targeted_prefix_retains_incubate_component_dependencies(self):
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.incubate_model import INCUBATE_DEPENDENCIES
        source=replace(query_record('Exile target artifact or creature. Incubate 3.'),
            type_line='Instant',power=None,toughness=None,keywords=('Incubate',))
        ir=compile_oracle_card(source,capability_registry=load_default_capability_registry(),capability_profile='commander_review')
        required={capability for node in ir.faces[0].nodes for capability in node.capability_dependencies}
        self.assertTrue(set(INCUBATE_DEPENDENCIES)<=required,sorted(set(INCUBATE_DEPENDENCIES)-required))
        self.assertEqual('exact',ir.status,[(value.kind,value.reason,value.blockers) for value in ir.material_residuals])


class DefinedTokenNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'defined-tokens.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/token-entry-boundary-cards.json',ROOT/'tests/fixtures/entry-copy-cards.json',
            ROOT/'tests/fixtures/incubate-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Defined tokens',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.session(self,seed)

    def checkpoint(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.checkpoint(self,session)

    def replay(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.replay(self,session,load=True)

    def resolve(self,session):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        return BoundEffectProgramRuntimeTests.resolve(self,session)

    def priority(self,session,mana):
        engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.started=True;engine.state.active_player='A'
        engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['A'].mana_pool.update(mana)
        engine._grant_priority('A');engine.pump()

    def activation(self,session,card):
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        return next(action for action in actions if action['id'].startswith('activate:'+card.ref+':'))

    def test_native_creation_entry_counters_and_two_paid_activations_with_pending_reload(self):
        from unittest.mock import patch
        from quorune.token_creation import create_tokens
        from quorune.entry_counter_model import EffectEntryCounter
        from quorune.record import authoritative_state_hash
        session=self.session(372001);engine=session.engine;entries=[]
        dispatch=engine._dispatch_semantic_event
        def observe(event,context,**kwargs):
            if event=='permanent.enter':
                entering=next(card for card in engine.state.cards.values() if card.ref==context['card'])
                entries.append((deepcopy(context),deepcopy(entering.counters)))
            return dispatch(event,context,**kwargs)
        with patch.object(engine,'_dispatch_semantic_event',side_effect=observe):
            created=create_tokens(engine,'A',name='Incubator',
                characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
                effect_entry_counters=(EffectEntryCounter('+1/+1',3,'A','incubate-effect','701.53a'),))
        card=next(card for card in engine.state.cards.values() if card.ref==created[0])
        self.assertEqual('Incubator',card.active_face)
        self.assertTrue(card.oracle_id.startswith('custom-token:'))
        self.assertEqual({'+1/+1':3},entries[0][1])
        self.assertEqual(['artifact'],entries[0][0]['types'])
        self.assertNotIn('effect_entry_counters',engine._copyable_characteristics(card))
        self.priority(session,{'C':4});self.checkpoint(session)
        identity=card.logical_object_id;timestamp=card.zone_timestamp;before=authoritative_state_hash(engine.state)
        wrong=session.act('pilot:B',{'action_id':self.activation(session,card)['id'],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        for _ in range(2):
            accepted=session.act('pilot:A',{'action_id':self.activation(session,card)['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
            self.assertEqual(0,engine.state.stack[-1].context['source_transform_count'])
        self.assertEqual(2,len(engine.state.stack));self.assertEqual(0,engine.state.players['A'].mana_pool['C'])
        resumed=self.replay(session);self.resolve(resumed)
        current=resumed.engine.state.cards[card.object_id]
        self.assertEqual(('Phyrexian Token',1,identity,timestamp),
            (current.active_face,current.transform_count,current.logical_object_id,current.zone_timestamp))
        self.assertEqual({'+1/+1':3},current.counters)
        data=resumed.engine._effective_card_data(current)
        self.assertEqual(('0','0'),(data['power'],data['toughness']))
        self.assertEqual((3,3),(resumed.engine._numeric_stat(current.object_id,'power'),
            resumed.engine._numeric_stat(current.object_id,'toughness')))
        self.assertEqual([],list(resumed.engine._activated_abilities(current)))
        self.replay(resumed)

    def test_zero_counter_front_is_legal_and_unpayable_offer_is_absent_before_paid_transform_dies(self):
        from quorune.token_creation import create_tokens
        from quorune.record import authoritative_state_hash
        session=self.session(372002);engine=session.engine
        refs=create_tokens(engine,'A',name='Incubator',
            characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()})
        card=next(card for card in engine.state.cards.values() if card.ref==refs[0])
        self.priority(session,{'C':0})
        self.assertEqual('battlefield',card.zone);self.assertEqual({},card.counters)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(action['id'].startswith('activate:'+card.ref+':') for action in actions))
        before=authoritative_state_hash(engine.state)
        rejected=session.act('pilot:A',{'action_id':'activate:'+card.ref+':ab1','pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        self.priority(session,{'C':2});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':self.activation(session,card)['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertNotIn(card.object_id,engine.state.players['A'].zones['battlefield'])
        self.replay(session)

    def test_created_copies_retain_both_faces_and_current_face_without_counter_state(self):
        from quorune.token_creation import create_tokens,token_copy_snapshot
        from quorune.entry_counter_model import EffectEntryCounter
        from quorune.permanent_transform import transform_permanent
        session=self.session(372003);engine=session.engine
        refs=create_tokens(engine,'A',name='Incubator',
            characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
            effect_entry_counters=(EffectEntryCounter('+1/+1',3,'A','incubate-effect','701.53a'),))
        original=next(card for card in engine.state.cards.values() if card.ref==refs[0])
        front_ref=create_tokens(engine,'A',name='',copy_of=original.ref)[0]
        front=next(card for card in engine.state.cards.values() if card.ref==front_ref)
        self.assertEqual('Incubator',front.active_face);self.assertEqual({},front.counters)
        transform_permanent(engine,original,expected_transform_count=0,day_night_instruction=False)
        back_ref=create_tokens(engine,'A',name='',copy_of=original.ref,
            copy_snapshot=token_copy_snapshot(engine,original))[0]
        back=next(card for card in engine.state.cards.values() if card.ref==back_ref)
        self.assertEqual('Phyrexian Token',back.active_face);self.assertEqual({},back.counters)
        self.assertEqual([],list(engine._activated_abilities(back)))
        self.priority(session,{'C':2});self.checkpoint(session)
        self.assertNotIn(back.object_id,engine.state.players['A'].zones['battlefield'])
        self.assertIn(front.object_id,engine.state.players['A'].zones['battlefield'])
        accepted=session.act('pilot:A',{'action_id':self.activation(session,front)['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertNotIn(front.object_id,engine.state.players['A'].zones['battlefield'])
        self.assertEqual({'+1/+1':3},engine.state.cards[original.object_id].counters)
        self.replay(session)

    def test_entry_counter_instruction_reaches_additional_tokens_and_nested_counter_replacement(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.token_creation import create_tokens
        from quorune.entry_counter_model import EffectEntryCounter
        session=self.session(372004);engine=session.engine
        for name in ('Generic Entry Map Bonus','Generic Entry Counter Double'):
            BoundEffectProgramRuntimeTests.add(self,engine,name)
        refs=create_tokens(engine,'A',name='Incubator',
            characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
            effect_entry_counters=(EffectEntryCounter('+1/+1',3,'A','incubate-effect','701.53a'),))
        tokens=[next(card for card in engine.state.cards.values() if card.ref==ref) for ref in refs]
        self.assertEqual(2,len(tokens))
        self.assertEqual({'Incubator','Map'},{card.printed_name for card in tokens})
        for card in tokens:
            self.assertEqual({'+1/+1':6},card.counters,card.printed_name)
        additional=next(card for card in tokens if card.printed_name=='Map')
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,engine._copyable_characteristics(additional))
        self.assertFalse(any(ability.builtin_semantic_key==DEFINED_TOKEN_TRANSFORM_KEY
            for ability in engine._activated_abilities(additional)))

    def test_actual_token_entry_copy_retains_a_single_face_and_paid_transform_does_nothing(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.token_creation import create_tokens
        from quorune.entry_counter_model import EffectEntryCounter
        from quorune.record import authoritative_state_hash
        session=self.session(372005);engine=session.engine
        copier=BoundEffectProgramRuntimeTests.add(self,engine,'Phyrexian Metamorph')
        copier.counters['+1/+1']=1
        spell=BoundEffectProgramRuntimeTests.add(self,engine,'Generic Entry One Copy',zone='hand')
        ref=create_tokens(engine,'A',name='Incubator',
            characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
            effect_entry_counters=(EffectEntryCounter('+1/+1',3,'A','incubate-effect','701.53a'),))[0]
        original=next(card for card in engine.state.cards.values() if card.ref==ref)
        action=BoundEffectProgramRuntimeTests.ready(self,session,spell,{'W':1,'C':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[copier.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNotNone(self.resolve(session))
        self.assertEqual('replacement.order',session.state.pending_decision.kind)
        session=self.replay(session);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        choice=next(row['id'] for row in options if original.ref in row['id'] and not row['id'].startswith('decline:'))
        before=authoritative_state_hash(session.state)
        wrong=session.act('pilot:B',{'action_id':'choose','replacement':choice})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':choice})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        token=next(card for card in engine.state.cards.values() if card.is_token and card.object_id!=original.object_id and card.zone=='battlefield')
        copied=engine._copyable_characteristics(token)
        self.assertNotIn(DEFINED_TOKEN_FACES_FIELD,copied)
        self.assertEqual('Incubator',copied['name']);self.assertEqual({},token.counters)
        identity=token.logical_object_id
        action=self.activation(session,token)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        current=engine.state.cards[token.object_id]
        self.assertEqual((identity,0,'battlefield'),(current.logical_object_id,current.transform_count,current.zone))
        self.assertEqual('Incubator',engine.display_name(current.object_id))
        self.assertEqual(0,engine.state.players['A'].mana_pool['C'])
        self.replay(session)

    def test_copy_effect_freezes_one_face_and_created_copy_keeps_both_uniform_faces_after_cleanup(self):
        from quorune.token_creation import create_tokens
        from quorune.entry_counter_model import EffectEntryCounter
        from quorune.effect_runtime.objects_stack_and_tokens import _apply_copy_until_end_of_turn
        from quorune.permanent_transform import transform_permanent
        from quorune.turn_step_owner import TURN_STEPS
        from quorune.defined_token_faces import DEFINED_TOKEN_UNIFORM_COPY_FIELD
        session=self.session(372006);engine=session.engine
        def create(counters=()):
            ref=create_tokens(engine,'A',name='Incubator',
                characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
                effect_entry_counters=counters)[0]
            return next(card for card in engine.state.cards.values() if card.ref==ref)
        source=create((EffectEntryCounter('+1/+1',3,'A','incubate-effect','701.53a'),))
        target=create()
        _apply_copy_until_end_of_turn(engine,{'op':'copy_until_end_of_turn','source':source.ref,'target':target.ref},
            actor='A',operation='copy_until_end_of_turn',reason='Frozen copied face boundary')
        transform_permanent(engine,source,expected_transform_count=0,day_night_instruction=False)
        copied_ref=create_tokens(engine,'A',name='',copy_of=source.ref)[0]
        copied=next(card for card in engine.state.cards.values() if card.ref==copied_ref)
        self.assertEqual('Phyrexian Token',copied.active_face)
        self.assertIn(DEFINED_TOKEN_UNIFORM_COPY_FIELD,engine._copyable_characteristics(copied))
        self.assertNotIn('until_end_of_turn',copied.annotations)
        self.assertEqual({},copied.counters)
        self.priority(session,{'C':6})
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.phase='ending';engine.state.step='end_step'
        engine.state.phase_index=TURN_STEPS.index(('ending','end_step'))
        engine._grant_priority('A');engine.pump();self.checkpoint(session)
        for card in (source,source,copied):
            action=self.activation(session,card)
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            self.assertEqual('Incubator',engine.display_name(card.object_id))
            self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(card)['type_line'])[0])
        self.assertEqual(('Phyrexian Token',3),(source.active_face,source.transform_count))
        self.assertEqual(('Incubator',1),(copied.active_face,copied.transform_count))
        for _ in range(24):
            if 'until_end_of_turn' not in source.annotations:break
            self.assertEqual('priority',session.state.pending_decision.kind)
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(accepted.ok,accepted.summary)
        else:self.fail('The normal cleanup step did not expire the copy effect')
        self.assertEqual('Phyrexian Token',engine.display_name(source.object_id))
        self.assertEqual((3,3),(engine._numeric_stat(source.object_id,'power'),engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual('Incubator',engine.display_name(copied.object_id))
        self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(copied)['type_line'])[0])
        self.assertEqual({'+1/+1':3},source.counters);self.assertEqual({},copied.counters)
        self.replay(session)

    def assert_original_closed(self,card):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_actual_converter_beast_cast_incubates_five_before_entry_and_transforms_with_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.record import authoritative_state_hash
        session=self.session(372007);engine=session.engine
        BoundEffectProgramRuntimeTests.add(self,engine,'Hardened Scales')
        spell=BoundEffectProgramRuntimeTests.add(self,engine,'Converter Beast',zone='hand')
        self.assertEqual('7fbb9547-a805-4768-8574-e283e64600a4',spell.oracle_id)
        self.assert_original_closed(spell)
        action=BoundEffectProgramRuntimeTests.ready(self,session,spell,{'G':1,'C':5})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        tokens=[card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(1,len(tokens));token=tokens[0]
        self.assertEqual(('Incubator',{'+1/+1':5}),(token.active_face,token.counters))
        self.assertNotIn('creature',engine._type_parts(engine._effective_card_data(token)['type_line'])[0])
        before=authoritative_state_hash(engine.state)
        wrong=session.act('pilot:B',{'action_id':self.activation(session,token)['id'],'pay':'auto'})
        self.assertFalse(wrong.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':self.activation(session,token)['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        resumed=self.replay(session);self.resolve(resumed)
        current=resumed.state.cards[token.object_id]
        self.assertEqual('Phyrexian Token',current.active_face)
        self.assertEqual({'+1/+1':5},current.counters)
        self.assertEqual((5,5),(resumed.engine._numeric_stat(current.object_id,'power'),resumed.engine._numeric_stat(current.object_id,'toughness')))
        self.replay(resumed)

    def test_actual_glistening_dawn_twice_resumes_each_entry_replacement_without_duplicate_creation(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.record import authoritative_state_hash
        session=self.session(372008);engine=session.engine
        for index in range(3):BoundEffectProgramRuntimeTests.add(self,engine,'Generic Bound Plains',ref='LAND'+str(index))
        double=BoundEffectProgramRuntimeTests.add(self,engine,'Generic Entry Counter Double',ref='COUNTER_DOUBLE')
        BoundEffectProgramRuntimeTests.add(self,engine,'Generic Entry Counter Add',ref='COUNTER_ADD')
        spell=BoundEffectProgramRuntimeTests.add(self,engine,'Glistening Dawn',zone='hand')
        self.assertEqual('13ee85c3-a16a-41ac-bbaa-c7549c700a03',spell.oracle_id)
        self.assert_original_closed(spell)
        action=BoundEffectProgramRuntimeTests.ready(self,session,spell,{'G':2,'C':2})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        for index in range(2):
            self.assertEqual('replacement.order',session.state.pending_decision.kind)
            tokens=[card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']
            self.assertEqual(index,len(tokens))
            self.assertTrue(all(card.counters=={'+1/+1':7} for card in tokens))
            session=self.replay(session);engine=session.engine
            projected=session.packet('pilot:A',full=True)['decision']
            self.assertNotIn('replacement_batch',str(projected));self.assertNotIn('prospective_objects',str(projected))
            for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
            options=projected['ctx']['options']
            selected=next(row['id'] for row in options if double.ref in row['id'])
            before=authoritative_state_hash(session.state)
            rejected=session.act('pilot:B',{'action_id':'choose','replacement':selected})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(2,len(tokens));self.assertTrue(all(card.counters=={'+1/+1':7} for card in tokens))
        self.assertNotEqual(tokens[0].zone_timestamp,tokens[1].zone_timestamp)
        self.replay(session)

    def test_actual_seedshark_uses_cast_spell_mana_value_instead_of_its_own(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        session=self.session(372009);engine=session.engine
        source=BoundEffectProgramRuntimeTests.add(self,engine,'Chrome Host Seedshark')
        spell=BoundEffectProgramRuntimeTests.add(self,engine,'Generic Bound Growth',zone='hand')
        self.assertEqual('837c5d0e-0a33-4f9d-ae25-68a687bdf640',source.oracle_id)
        self.assert_original_closed(source)
        action=BoundEffectProgramRuntimeTests.ready(self,session,spell,{'G':1})
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertTrue(any(item.kind=='triggered_ability' for item in session.state.stack))
        session=self.replay(session);self.resolve(session)
        tokens=[card for card in session.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual({'+1/+1':1},tokens[0].counters)
        self.replay(session)

    def test_actual_furnace_death_uses_departed_power_and_negative_result_creates_zero_counter_token(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        for index,(spell_name,expected) in enumerate((('Lightning Bolt',3),('Disfigure',0))):
            with self.subTest(spell=spell_name):
                session=self.session(372010+index);engine=session.engine
                source=BoundEffectProgramRuntimeTests.add(self,engine,'Furnace Gremlin')
                spell=BoundEffectProgramRuntimeTests.add(self,engine,spell_name,zone='hand')
                self.assertEqual('bcbef58b-0c16-40f2-9b5c-14dfcc00f5e5',source.oracle_id)
                self.assert_original_closed(source)
                BoundEffectProgramRuntimeTests.ready(self,session,spell,{'R':5,'B':1})
                self.checkpoint(session)
                if expected:
                    for _ in range(2):
                        accepted=session.act('pilot:A',{'action_id':self.activation(session,source)['id'],'pay':'auto'})
                        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                    self.assertEqual(3,engine._numeric_stat(source.object_id,'power'))
                actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                action=next(row for row in actions if row.get('card')==spell.ref)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
                self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
                tokens=[card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield']
                self.assertEqual(1,len(tokens));self.assertEqual('Incubator',tokens[0].active_face)
                self.assertEqual({'+1/+1':expected} if expected else {},tokens[0].counters)
                self.replay(session)

    def test_actual_merciless_exiles_then_incubates_and_illegal_target_cancels_the_entire_spell(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        from quorune.token_creation import create_tokens
        from quorune.entry_counter_model import EffectEntryCounter
        from quorune.permanent_transform import transform_permanent
        for index,fizzle in enumerate((False,True)):
            with self.subTest(fizzle=fizzle):
                session=self.session(372012+index);engine=session.engine
                ref=create_tokens(engine,'B',name='Incubator',
                    characteristics={DEFINED_TOKEN_FACES_FIELD:incubator_definition().to_dict()},
                    effect_entry_counters=(EffectEntryCounter('+1/+1',3,'B','incubate-effect','701.53a'),))[0]
                target=next(card for card in engine.state.cards.values() if card.ref==ref)
                transform_permanent(engine,target,expected_transform_count=0,day_night_instruction=False)
                spell=BoundEffectProgramRuntimeTests.add(self,engine,'Merciless Repurposing',zone='hand')
                response=BoundEffectProgramRuntimeTests.add(self,engine,'Naturalize',seat='B',zone='hand')
                self.assertEqual('2e1b5ae6-a29d-4f37-a380-eebee6cdcbd2',spell.oracle_id)
                self.assert_original_closed(spell)
                engine.state.players['B'].mana_pool.update({'G':1,'C':1})
                action=BoundEffectProgramRuntimeTests.ready(self,session,spell,{'B':2,'C':4})
                self.checkpoint(session)
                accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                if fizzle:
                    for _ in range(8):
                        if session.pending_principals()[0]=='pilot:B':break
                        passed=session.act(session.pending_principals()[0],{'action_id':'pass'})
                        self.assertTrue(passed.ok,passed.summary)
                    else:self.fail('Response priority did not reach the target controller')
                    actions=session.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions']
                    offered=next(row for row in actions if row.get('card')==response.ref)
                    accepted=session.act('pilot:B',{'action_id':offered['id'],'targets':[target.ref],'pay':'auto'})
                    self.assertTrue(accepted.ok,accepted.summary)
                self.resolve(session)
                self.assertNotIn(target.object_id,engine.state.players['B'].zones['battlefield'])
                tokens=[card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield' and card.controller=='A']
                self.assertEqual(0 if fizzle else 1,len(tokens))
                if tokens:self.assertEqual({'+1/+1':3},tokens[0].counters)
                self.assertEqual('graveyard',engine.state.cards[spell.object_id].zone)
                self.replay(session)

    def test_actual_blight_titan_counts_creature_cards_after_its_mill_instruction(self):
        """Oracle's mill-then-count order (CR608.2c) distinguishes 2 from 1 or 3."""
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests
        session=self.session(372014);engine=session.engine
        BoundEffectProgramRuntimeTests.add(self,engine,'Furnace Gremlin',zone='graveyard')
        creature=BoundEffectProgramRuntimeTests.add(self,engine,'Converter Beast',zone='library')
        noncreature=BoundEffectProgramRuntimeTests.add(self,engine,'Naturalize',zone='library')
        source=BoundEffectProgramRuntimeTests.add(self,engine,'Blight Titan',zone='hand')
        self.assertEqual('f2c75577-c9be-48f3-8398-3020c79bd34e',source.oracle_id)
        self.assert_original_closed(source)
        action=BoundEffectProgramRuntimeTests.ready(self,session,source,{'B':2,'C':4})
        self.assertEqual([creature.object_id,noncreature.object_id],engine.state.players['A'].zones['library'][-2:])
        self.assertNotIn('Naturalize',str(session.packet('pilot:B',full=True)))
        self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('graveyard',engine.state.cards[creature.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[noncreature.object_id].zone)
        tokens=[card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield']
        self.assertEqual(1,len(tokens));self.assertEqual({'+1/+1':2},tokens[0].counters)
        self.assertIn('Naturalize',str(session.packet('pilot:B',full=True)))
        self.replay(session)
