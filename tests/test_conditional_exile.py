from __future__ import annotations

"""Read-only CR610.3 contracts; conditional gameplay admission remains pending."""

import json
from types import SimpleNamespace
import unittest

from quorune.conditional_exile_model import (ConditionalExileBinding, conditional_source_remains,
    conditional_return_objects, conditional_departure_return_objects)
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.linked_exile_return_model import ExiledObjectIdentity
from quorune.compiler.conditional_exile_templates import conditional_source_exile_template
from quorune.model import CardInstance


def exiled_card(object_id='held', ref='HELD', owner='B'):
    return CardInstance(object_id=object_id, ref=ref, oracle_id='fixture:conditional-exile-target',
        printed_name='Fixture Held Object', owner=owner, controller=owner, zone='exile',
        zone_change_counter=1)


class ConditionalExileModelTests(unittest.TestCase):
    def binding(self, card=None, source=None, binding_id='until:1'):
        card = card or exiled_card()
        return ConditionalExileBinding(binding_id, source or ContinuousObjectIdentity('source', 'source@0'),
            (ExiledObjectIdentity(card.object_id, card.ref, card.logical_object_id),))

    def test_binding_is_immutable_and_round_trips_without_reinterpreting_identities(self):
        binding=self.binding();serialized=binding.to_dict()
        self.assertEqual(binding,ConditionalExileBinding.from_dict(json.loads(json.dumps(serialized))))
        serialized['objects'][0]['logical_object_id']='new-object'
        self.assertEqual('held@1',binding.objects[0].logical_object_id)
        for field,value in (('schema_version',True),('binding_id',''),('objects',[]),('source',{'object_id':'source'})):
            altered={**binding.to_dict(),field:value}
            with self.assertRaises(ValueError):ConditionalExileBinding.from_dict(altered)
        with self.assertRaises(ValueError):ConditionalExileBinding.from_dict({**binding.to_dict(),'callback':'return now'})

    def test_source_presence_uses_original_incarnation_and_does_not_depend_on_ability_or_controller(self):
        # CR610.3 creates a second one-shot effect, independently of the
        # ability that caused the first. Phasing isn't a zone departure.
        source=CardInstance(object_id='source',ref='SOURCE',oracle_id='fixture:source',
            printed_name='Fixture Source',owner='A',controller='A',zone='battlefield')
        retained=ContinuousObjectIdentity(source.object_id,source.logical_object_id)
        cards={source.object_id:source}
        self.assertTrue(conditional_source_remains(retained,cards))
        source.controller='C';source.phased_out=True;source.annotations['removed_abilities']=True
        self.assertTrue(conditional_source_remains(retained,cards))
        source.zone='hand';self.assertFalse(conditional_source_remains(retained,cards))
        source.zone='battlefield';source.zone_change_counter+=2
        self.assertFalse(conditional_source_remains(retained,cards))
        other=CardInstance(object_id='other-source',ref='OTHER',oracle_id=source.oracle_id,
            printed_name=source.printed_name,owner='A',controller='A',zone='battlefield')
        self.assertFalse(conditional_source_remains(retained,{'other-source':other}))

    def test_return_requires_the_actual_exile_incarnation_and_excludes_tokens_and_copies(self):
        card=exiled_card();binding=self.binding(card)
        self.assertEqual((card,),conditional_return_objects(binding,{card.object_id:card}))
        for field,value in (('ref','OTHER'),('zone','hand'),('zone_change_counter',3),('is_token',True),('object_kind','spell_copy')):
            changed=CardInstance.from_dict(card.to_dict());setattr(changed,field,value)
            self.assertEqual((),conditional_return_objects(binding,{changed.object_id:changed}),field)
        self.assertEqual((),conditional_return_objects(binding,{}))

    def test_simultaneous_departures_collect_one_return_set_with_owner_control_data(self):
        # CR610.3c-d: return to owners, and simultaneous specified events
        # require simultaneous returns rather than a trigger for each object.
        first=exiled_card();second=exiled_card('second','SECOND','D')
        a=self.binding(first);c=self.binding(second,ContinuousObjectIdentity('second-source','second-source@0'),'until:2')
        cards={first.object_id:first,second.object_id:second}
        self.assertEqual((first,second),conditional_departure_return_objects((a,c),(a.source,c.source),cards))
        self.assertEqual(['B','D'],[card.owner for card in conditional_departure_return_objects((a,c),(a.source,c.source),cards)])
        self.assertEqual((first,),conditional_departure_return_objects((a,c),(a.source,),cards))
        self.assertEqual((),conditional_departure_return_objects((a,c),
            (ContinuousObjectIdentity(a.source.object_id,'source@2'),),cards))
        with self.assertRaises(ValueError):conditional_departure_return_objects((a.to_dict(),),(a.source,),cards)

    def test_shared_blink_identity_codec_retains_serialized_shape_and_strict_values(self):
        value={'object_id':'card','ref':'CARD','logical_object_id':'card@1'}
        self.assertEqual(value,ExiledObjectIdentity.from_dict(value).to_dict())
        for changed in ({**value,'ref':None},{**value,'object_id':True},{**value,'unknown':1}):
            with self.assertRaises(ValueError):ExiledObjectIdentity.from_dict(changed)


class ConditionalExileCompilerRecognitionTests(unittest.TestCase):
    def parse(self,text,*,name='Fixture Source',permanent=True):
        return conditional_source_exile_template(text,card_name=name,source_is_permanent=permanent)

    def test_source_names_and_descriptions_keep_one_generic_source_duration(self):
        for source in ('this enchantment','this creature','this artifact','this permanent','Fixture Source'):
            with self.subTest(source=source):
                value=self.parse('Exile target nonland permanent an opponent controls until '+source+' leaves the battlefield.')
                self.assertIsNotNone(value)
                self.assertEqual('opponent',value.target_schema['controller_relation'])
                self.assertEqual(['land'],list(value.target_schema['types_none']))
                self.assertEqual(1,value.target_schema['count'])
        self.assertIsNone(self.parse('Exile target creature until another permanent leaves the battlefield.'))
        self.assertIsNone(self.parse('Exile target creature until this enchantment leaves the battlefield.',permanent=False))

    def test_cardinality_optional_selection_and_source_exclusion_remain_independent(self):
        for subject,count,optional,other in (
            ('target creature',1,False,False),('another target creature',1,False,True),
            ('up to one target creature',1,True,False),('up to two other target creatures',2,True,True),
            ('three target nonland permanents',3,False,False)):
            value=self.parse('Exile '+subject+' until this permanent leaves the battlefield.')
            self.assertIsNotNone(value,subject);schema=value.target_schema
            self.assertEqual(count,schema['up_to' if optional else 'count'])
            self.assertEqual(other,bool(schema.get('source_exclusion')))
        self.assertIsNone(self.parse('Exile X target creatures until this permanent leaves the battlefield.'))

    def test_until_grammar_does_not_recognize_linked_leave_triggers_or_other_durations(self):
        for text in ('Exile target creature. When this creature leaves the battlefield, return that card to the battlefield.',
                     'Exile target creature until end of turn.',
                     'Exile target creature until this creature dies.',
                     'Exile target creature until you control no artifacts.',
                     'Exile target creature until this creature leaves the battlefield. Draw a card.'):
            self.assertIsNone(self.parse(text),text)

    def test_recognition_alone_does_not_promote_conditional_exile_to_executable_ir(self):
        # The movement, source-departure and nested continuation coordinator
        # is required before whole-program gameplay admission can change.
        from test_linked_exile_return import record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import load_default_capability_registry
        text='When this enchantment enters, exile target creature until this enchantment leaves the battlefield.'
        ir=compile_oracle_card(record(text,type_line='Enchantment'),capability_registry=load_default_capability_registry(),
            capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(ir.material_residuals)


class ConditionalExileZoneBoundaryTests(unittest.TestCase):
    def test_current_source_or_phasing_cannot_end_the_conditional_instruction(self):
        from quorune.conditional_exile_return import return_after_source_departures
        from quorune.errors import GameRuleError
        from unittest.mock import patch
        source=CardInstance(object_id='source',ref='SOURCE',oracle_id='fixture:source',printed_name='Source',
            owner='A',controller='A',zone='battlefield',phased_out=True)
        retained=ContinuousObjectIdentity(source.object_id,source.logical_object_id)
        host=SimpleNamespace(state=SimpleNamespace(cards={'source':source}),active_seats=tuple('ABCD'))
        with patch('quorune.conditional_exile_return.ZoneTransitionOwner.move_cards_simultaneously') as move:
            with self.assertRaises(GameRuleError):return_after_source_departures(host,(),(retained,))
            move.assert_not_called()

    def test_committed_source_departures_return_real_exile_objects_simultaneously_under_owners(self):
        # A canonical-zone kernel witness, not compiler or printed-card admission.
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.conditional_exile_return import return_after_source_departures
        from quorune.zone_transitions import ZoneTransitionOwner
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100301);engine=session.engine
            source=actions.add(engine,'Generic Bound Mentor',ref='conditional-kernel-source')
            first=actions.add(engine,'Generic Bound Body',seat='B',ref='conditional-held-B')
            second=actions.add(engine,'Generic Bound Body',seat='D',ref='conditional-held-D')
            retained=ContinuousObjectIdentity(source.object_id,source.logical_object_id)
            ZoneTransitionOwner(engine).move_cards_simultaneously(((first.object_id,'exile'),(second.object_id,'exile')),
                reason='conditional kernel first movement')
            binding=ConditionalExileBinding('conditional-kernel',retained,
                tuple(ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id) for card in (first,second)))
            first_exile=first.logical_object_id;second_exile=second.logical_object_id
            ZoneTransitionOwner(engine).move_card(source.object_id,'hand',reason='conditional kernel source departure')
            restored=ConditionalExileBinding.from_dict(json.loads(json.dumps(binding.to_dict())))
            result=return_after_source_departures(engine,(restored,),(retained,))
            self.assertEqual((first.object_id,second.object_id),tuple(card.object_id for card in result))
            self.assertEqual(('B','D'),tuple(card.controller for card in result))
            self.assertTrue(all(card.zone=='battlefield' for card in result))
            self.assertEqual(first.zone_timestamp,second.zone_timestamp)
            self.assertNotEqual(first_exile,first.logical_object_id);self.assertNotEqual(second_exile,second.logical_object_id)
            self.assertEqual((),return_after_source_departures(engine,(restored,),(retained,)))
        finally:Actions.tearDownClass()
