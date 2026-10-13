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
        from quorune.linked_exile_return_model import CONDITIONAL_EXILE_CAPABILITY
        if not load_default_capability_registry().closure((CONDITIONAL_EXILE_CAPABILITY,),profile='commander_review').trusted:
            self.assertNotEqual('exact',ir.status)
            self.assertTrue(ir.material_residuals)
        else:
            self.assertEqual('exact',ir.status)
            self.assertEqual(2,ir.faces[0].nodes[0].effects[0]['spec']['schema_version'])

    def test_conditional_exile_dependency_and_producer_mutations_fail_closed(self):
        from pathlib import Path
        from common import ROOT
        from unittest.mock import patch
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        from quorune.oracle_ir import compile_oracle_card
        from test_linked_exile_return import record
        value=record('When this enchantment enters, exile target creature until this enchantment leaves the battlefield.',type_line='Enchantment')
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for dependency in ('zone.exile_until_source_departure.fixed','zone.change.destination_replacement','target.revalidate_resolution'):
            modified=json.loads(json.dumps(raw));row=next(x for x in modified['capabilities'] if x['id']==dependency)
            row.update(status='blocked',blockers=['conditional exile dependency witness']);registry=CapabilityRegistry(modified)
            registry.mark_evidence_verified('0'*64)
            self.assertNotEqual('exact',compile_oracle_card(value,capability_registry=registry,capability_profile='commander_review').status)
        with patch('quorune.compiler.resolution_effect_templates.conditional_source_exile_template',return_value=None):
            self.assertNotEqual('exact',compile_oracle_card(value,capability_registry=load_default_capability_registry(),capability_profile='commander_review').status)
        from quorune.linked_exile_return_model import LinkedExileReturnSpec
        for altered in ({'schema_version':1,'timing':'source_departure'},
                        {'schema_version':2,'timing':'immediate'},
                        {'schema_version':2,'timing':'source_departure','controller':'actor'},
                        {'schema_version':2,'timing':'source_departure','tapped':True}):
            with self.assertRaises(ValueError):LinkedExileReturnSpec(**altered)
        from quorune.compiler.program_generation import generated_programs
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from dataclasses import replace
        class SourceDatabase:
            def rulings(self,record):return []
        program=next(p for p in generated_programs(SourceDatabase(),value,capability_registry=load_default_capability_registry(),
            capability_profile='commander_review') if any(e.get('spec',{}).get('timing')=='source_departure' for e in p.effects))
        dependencies=tuple(c for c in program.capability_dependencies if c!='zone.exile_until_source_departure.fixed')
        closure=load_default_capability_registry().closure(dependencies,profile='commander_review').to_dict()
        forged=replace(program,capability_dependencies=dependencies,capability_closure=closure)
        self.assertFalse(bind_semantic_program_runtime(forged,capability_registry=load_default_capability_registry(),profile='commander_review')['strict'])


class ConditionalExileZoneBoundaryTests(unittest.TestCase):
    def test_nested_source_departures_ignore_retired_exile_and_preserve_current_binding(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.zone_transitions import ZoneTransitionOwner
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100332);engine=session.engine;owner=ZoneTransitionOwner(engine)
            first=actions.add(engine,'Generic Bound Mentor',ref='nested-first')
            second=actions.add(engine,'Generic Bound Mentor',ref='nested-second')
            held=actions.add(engine,'Generic Bound Body',seat='B',zone='exile',ref='nested-held')
            old=ConditionalExileBinding('old-hold',ContinuousObjectIdentity(first.object_id,first.logical_object_id),
                (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
            owner.remember_conditional_exile(old)
            owner.move_card(held.object_id,'battlefield',controller='B',reason='held object leaves original exile')
            owner.move_card(held.object_id,'exile',reason='new exile instruction')
            current=ConditionalExileBinding('current-hold',ContinuousObjectIdentity(second.object_id,second.logical_object_id),
                (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
            owner.remember_conditional_exile(current)
            owner.move_card(first.object_id,'hand',reason='retired binding source departs')
            self.assertEqual('exile',held.zone)
            owner.move_card(second.object_id,'hand',reason='current binding source departs')
            self.assertEqual(('battlefield','B'),(held.zone,held.controller))
            self.assertEqual(3,held.zone_change_counter)
        finally:Actions.tearDownClass()

    def test_exiling_a_bound_source_returns_its_hold_then_outer_departure_returns_that_source(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.zone_transitions import ZoneTransitionOwner
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100333);engine=session.engine;owner=ZoneTransitionOwner(engine)
            outer=actions.add(engine,'Generic Bound Mentor',ref='outer-source')
            inner=actions.add(engine,'Generic Bound Mentor',seat='C',ref='inner-source')
            held=actions.add(engine,'Generic Bound Body',seat='B',zone='exile',ref='inner-held')
            owner.remember_conditional_exile(ConditionalExileBinding('inner-binding',ContinuousObjectIdentity(inner.object_id,inner.logical_object_id),
                (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),)))
            owner.move_card(inner.object_id,'exile',reason='outer first one-shot exiles inner source')
            self.assertEqual(('battlefield','B'),(held.zone,held.controller))
            owner.remember_conditional_exile(ConditionalExileBinding('outer-binding',ContinuousObjectIdentity(outer.object_id,outer.logical_object_id),
                (ExiledObjectIdentity(inner.object_id,inner.ref,inner.logical_object_id),)))
            owner.move_card(outer.object_id,'hand',reason='outer specified event')
            self.assertEqual(('battlefield','C'),(inner.zone,inner.controller))
            self.assertEqual('battlefield',held.zone)
            from quorune.conditional_exile_model import source_conditional_bindings
            self.assertEqual((),source_conditional_bindings(inner))
        finally:Actions.tearDownClass()

    def run_consumed_sacrifice_return(self, *, aura=False, mixed=False):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.zone_transitions import ZoneTransitionOwner
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'consumed-choice.sqlite3';build_fixture_database([
                ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/linked-exile-return-cards.json',
                ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
            db=CardDatabase(path)
            try:
                actions=Actions();actions.db=db;actions.registry=load_default_capability_registry()
                actions.deck=DeckDefinition('Generic completed choice',[DeckEntry('Generic Bound Commander',1,'commander'),
                    DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
                session=actions.session(6100330);engine=session.engine
                source=actions.add(engine,'Generic Bound Body',ref='choice-source')
                held=actions.add(engine,'Generic Conditional Aura' if aura else 'Generic Conditional Counter Body',zone='exile',ref='choice-held')
                extra_aura=actions.add(engine,'Generic Conditional Aura',zone='exile',ref='mixed-choice-aura') if mixed else None
                attachment=actions.add(engine,'Generic Bound Body',seat='B',ref='completed-aura-subject') if aura else None
                if mixed:attachment=actions.add(engine,'Generic Bound Body',seat='B',ref='mixed-attachment-subject')
                actions.add(engine,'Generic Blink Counter Doubler',ref='choice-double')
                actions.add(engine,'Generic Blink Counter Addition',ref='choice-plus')
                ZoneTransitionOwner(engine).remember_conditional_exile(ConditionalExileBinding('choice-binding',
                    ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                    tuple(ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id) for card in
                        ((held,extra_aura) if mixed else (held,)))))
                spell=actions.add(engine,'Generic Conditional Choice Sacrifice',zone='hand',ref='choice-spell')
                offered=actions.ready(session,spell,{'B':1});actions.checkpoint(session)
                self.assertTrue(session.act('pilot:A',{'action_id':offered['id'],'targets':['A'],'pay':'auto'}).ok)
                decision=actions.resolve(session);self.assertIsNotNone(decision)
                packet=session.packet('pilot:A',full=True)['decision'];self.assertIn('sacrifice',json.dumps(packet).casefold())
                life=session.state.players['A'].life
                accepted=session.act('pilot:A',{'action_id':'choose','cards':[source.ref]})
                self.assertTrue(accepted.ok,accepted.summary)
                self.assertEqual('aura.entry' if aura else 'replacement.order',session.state.pending_decision.kind)
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.assertEqual(life,session.state.players['A'].life)
                resumed=actions.replay(session,load=True)
                if aura:accepted=resumed.act('pilot:A',{'action_id':'choose','aura_target':attachment.ref})
                else:
                    choice=resumed.packet('pilot:A',full=True)['decision']['ctx']['options']
                    selected=next(row['id'] for row in choice if 'choice-plus' in row['id'])
                    accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':selected})
                self.assertTrue(accepted.ok,accepted.summary)
                if mixed:
                    self.assertEqual('aura.entry',resumed.state.pending_decision.kind)
                    self.assertEqual('exile',resumed.state.cards[held.object_id].zone)
                    resumed=actions.replay(resumed,load=True)
                    accepted=resumed.act('pilot:A',{'action_id':'choose','aura_target':attachment.ref})
                    self.assertTrue(accepted.ok,accepted.summary)
                actions.resolve(resumed)
                self.assertEqual(life-1,resumed.state.players['A'].life)
                self.assertEqual(1,resumed.state.cards[source.object_id].zone_change_counter)
                if aura:self.assertEqual(('battlefield',attachment.object_id),(resumed.state.cards[held.object_id].zone,
                    resumed.state.cards[held.object_id].attached_to))
                else:self.assertEqual(('battlefield',4),(resumed.state.cards[held.object_id].zone,
                    resumed.state.cards[held.object_id].counters['+1/+1']))
                actions.replay(resumed)
            finally:db.close()

    def test_consumed_sacrifice_choice_returns_held_card_then_continues_without_rechoosing(self):
        self.run_consumed_sacrifice_return()

    def test_consumed_sacrifice_choice_returns_aura_and_resumes_remaining_effect_once(self):
        self.run_consumed_sacrifice_return(aura=True)

    def test_consumed_choice_mixed_aura_and_counter_return_preserves_both_choices_and_replay(self):
        self.run_consumed_sacrifice_return(mixed=True)

    def test_completed_choice_payload_rejects_unknown_intents_and_malformed_completion(self):
        from unittest.mock import patch
        import quorune.conditional_exile_return as owner
        from quorune.semantic_choices.completed_choice import decode_remaining_completion
        from quorune.semantic_choices.model import SemanticChoiceError
        captured=[];original=owner.issue_conditional_return_choice
        def inspect(host,required,completed_choice=None):
            original(host,required,completed_choice)
            if completed_choice is not None:captured.append(json.loads(json.dumps(completed_choice)))
        with patch.object(owner,'issue_conditional_return_choice',inspect):
            self.test_consumed_sacrifice_choice_returns_held_card_then_continues_without_rechoosing()
        self.assertTrue(captured)
        value=captured[0];decode_remaining_completion(value)
        for key,replacement in (('schema_version',True),('actor',''),('intents',[{'kind':'arbitrary_callback','fields':{}}]),
                                ('prepend_effects',None)):
            with self.assertRaises((SemanticChoiceError,ValueError,TypeError)):
                decode_remaining_completion({**value,key:replacement})
        from quorune.semantic_runtime.intents import LifeChangeIntent,PlaceCounterBatchIntent,CounterPlacementAmount
        from quorune.semantic_choices.completed_choice import encode_remaining_completion
        from quorune.semantic_choices.model import SemanticChoiceCompletion
        continuation,actor,response,_=decode_remaining_completion(value)
        completion=SemanticChoiceCompletion(intents=(LifeChangeIntent(actor,actor,-2,'sealed result'),
            PlaceCounterBatchIntent(actor,'OBJECT',(CounterPlacementAmount('+1/+1',2),CounterPlacementAmount('flying',1)),'sealed counters')))
        encoded=encode_remaining_completion(continuation,actor,response,completion,0)
        self.assertEqual(completion.intents,decode_remaining_completion(json.loads(json.dumps(encoded)))[3].intents)

    def test_initial_exile_of_its_source_returns_immediately_and_other_destinations_are_not_linked(self):
        # A scoped zone/lifecycle witness; original-card commands are tested
        # separately and this does not force semantic program trust.
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.model import StackItem
        from quorune.linked_exile_return import resolve_linked_exile_return
        from quorune.linked_exile_return_model import LinkedExileReturnSpec
        from quorune.zone_transitions import ZoneTransitionOwner
        from unittest.mock import patch
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100320);engine=session.engine
            source=actions.add(engine,'Generic Bound Mentor',ref='initial-self-exile')
            other=actions.add(engine,'Generic Bound Body',seat='B',ref='initial-held-control')
            item=StackItem(stack_id='lifecycle-kernel',ref='KERNEL',kind='triggered_ability',controller='A',label='kernel',
                source_object_id=source.object_id,context={'source_logical_object_id':source.logical_object_id})
            engine.state.stack.append(item)
            common={'op':'linked_exile_return','binding_id':'blink:initial','spec':LinkedExileReturnSpec(schema_version=2,timing='source_departure').to_dict(),
                '_runtime_source':{'stack_ref':item.ref,'object_id':source.object_id,'logical_object_id':source.logical_object_id,'card_ref':source.ref}}
            resolve_linked_exile_return(engine,{**common,'phase':'exile','cards':[source.ref,other.ref]},actor='A',operation='linked_exile_return',reason='initial conditional movement')
            resolve_linked_exile_return(engine,{**common,'phase':'return'},actor='A',operation='linked_exile_return',reason='initial conditional movement')
            self.assertEqual(['battlefield','battlefield'],[source.zone,other.zone])
            self.assertEqual(['A','B'],[source.controller,other.controller])
            self.assertEqual(2,source.zone_change_counter);self.assertEqual(2,other.zone_change_counter)
            # A canonical destination result other than exile is not retained.
            second=actions.add(engine,'Generic Bound Mentor',ref='alternative-source')
            target=actions.add(engine,'Generic Bound Body',seat='B',ref='alternative-target')
            item.context={};item.source_object_id=second.object_id
            common={**common,'binding_id':'blink:alternative','_runtime_source':{
                'stack_ref':item.ref,'object_id':second.object_id,'logical_object_id':second.logical_object_id,'card_ref':second.ref}}
            original=ZoneTransitionOwner.move_cards_simultaneously
            def redirect(owner,changes,**kwargs):
                return original(owner,tuple((key,'hand' if key==target.object_id and zone=='exile' else zone) for key,zone in changes),**kwargs)
            with patch.object(ZoneTransitionOwner,'move_cards_simultaneously',redirect):
                resolve_linked_exile_return(engine,{**common,'phase':'exile','cards':[target.ref]},actor='A',operation='linked_exile_return',reason='canonical outcome diagnostic')
                resolve_linked_exile_return(engine,{**common,'phase':'return'},actor='A',operation='linked_exile_return',reason='canonical outcome diagnostic')
            from quorune.conditional_exile_model import source_conditional_bindings
            self.assertEqual('hand',target.zone);self.assertEqual((),source_conditional_bindings(second))
        finally:Actions.tearDownClass()

    def test_mixed_return_keeps_unattachable_aura_exiled_and_returns_other_objects(self):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.zone_transitions import ZoneTransitionOwner
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mixed.sqlite3';build_fixture_database([
                ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
            db=CardDatabase(path)
            try:
                actions=Actions();actions.db=db;actions.registry=load_default_capability_registry()
                actions.deck=DeckDefinition('Generic mixed return',[DeckEntry('Generic Bound Commander',1,'commander'),
                    DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
                session=actions.session(6100305);engine=session.engine;owner=ZoneTransitionOwner(engine)
                source=actions.add(engine,'Generic Conditional Sacrifice Source',ref='mixed-source')
                aura=actions.add(engine,'Generic Conditional Aura',zone='exile',ref='unattachable-aura')
                body=actions.add(engine,'Generic Bound Body',seat='B',zone='exile',ref='simultaneous-body')
                binding=ConditionalExileBinding('mixed-created',ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                    tuple(ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id) for card in (aura,body)))
                owner.remember_conditional_exile(binding)
                owner.move_card(source.object_id,'hand',reason='mixed return specified event')
                self.assertEqual('exile',aura.zone)
                self.assertEqual(('battlefield','B'),(body.zone,body.controller))
            finally:db.close()

    def run_created_binding_return_choice(self, *, sacrifice, state_based=False, aura=False, mana=False, cast_cost=False):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.zone_transitions import ZoneTransitionOwner
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'conditional.sqlite3'
            build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
                ROOT/'tests/fixtures/linked-exile-return-cards.json',ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
            db=CardDatabase(path)
            try:
                actions=Actions();actions.db=db;actions.registry=load_default_capability_registry()
                actions.deck=DeckDefinition('Generic conditional test',[DeckEntry('Generic Bound Commander',1,'commander'),
                    DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
                session=actions.session(6100304);engine=session.engine
                source=actions.add(engine,'Generic Conditional Mana Source' if mana else
                    'Generic Conditional Sacrifice Source' if sacrifice else 'Generic Bound Body',ref='removal-source')
                held=actions.add(engine,'Generic Conditional Aura' if aura else 'Generic Conditional Counter Body',zone='exile',ref='replacement-held')
                attachment=actions.add(engine,'Generic Bound Body',seat='B',ref='return-aura-subject') if aura else None
                actions.add(engine,'Generic Blink Counter Doubler',ref='return-double')
                actions.add(engine,'Generic Blink Counter Addition',ref='return-plus')
                binding=ConditionalExileBinding('already-created',ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                    (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
                ZoneTransitionOwner(engine).remember_conditional_exile(binding)
                removal=(actions.add(engine,'Generic Conditional Cast Sacrifice',zone='hand',ref='cast-sacrifice-spell') if cast_cost else
                    source if sacrifice else actions.add(engine,'Murder',zone='hand',ref='actual-removal-spell'))
                action=actions.ready(session,removal,{'B':1} if cast_cost else {} if sacrifice else {'B':2,'C':1})
                if state_based:source.marked_damage=20
                actions.checkpoint(session)
                before_hand=len(session.state.players['A'].zones['hand'])
                accepted=session.act('pilot:A',{'action_id':'pass'} if state_based else
                    {'action_id':action['id'],**({'sacrifice_cards':[source.ref]} if cast_cost else {} if sacrifice else {'targets':[source.ref]}),'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                if not sacrifice and not state_based and not cast_cost:actions.resolve(session)
                if state_based:
                    for _ in range(12):
                        if session.state.pending_decision.kind=='replacement.order':break
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                self.assertEqual('aura.entry' if aura else 'replacement.order',session.state.pending_decision.kind)
                self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
                self.assertEqual('exile',session.state.cards[held.object_id].zone)
                if (sacrifice or state_based or cast_cost) and not aura:
                    self.assertEqual('conditional_return',session.state.pending_decision.continuation['replacement_resume_kind'])
                    if sacrifice:self.assertEqual(before_hand,len(session.state.players['A'].zones['hand']))
                    if sacrifice and not mana:self.assertEqual('activated_ability',session.state.stack[-1].kind)
                    if cast_cost:self.assertEqual('spell',session.state.stack[-1].kind)
                elif not aura:self.assertEqual('return_linked_exiled_objects',session.state.pending_decision.continuation['effect']['op'])
                resumed=actions.replay(session,load=True)
                from quorune.record import authoritative_state_hash
                before_choice=authoritative_state_hash(resumed.state)
                denied=resumed.act('pilot:D',{'action_id':'choose',**({'aura_target':attachment.ref} if aura else {'replacement':'unknown'})})
                self.assertFalse(denied.ok);self.assertEqual(before_choice,authoritative_state_hash(resumed.state))
                if aura:accepted=resumed.act('pilot:A',{'action_id':'choose','aura_target':attachment.ref})
                else:
                    options=resumed.packet('pilot:A',full=True)['decision']['ctx']['options']
                    chosen=next(row['id'] for row in options if 'return-plus' in row['id'])
                    accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':chosen})
                self.assertTrue(accepted.ok,accepted.summary)
                if sacrifice:
                    self.assertEqual(before_hand,len(resumed.state.players['A'].zones['hand']))
                    if not mana:self.assertEqual('activated_ability',resumed.state.stack[-1].kind)
                if cast_cost:self.assertEqual('spell',resumed.state.stack[-1].kind)
                actions.resolve(resumed)
                self.assertEqual('graveyard',resumed.state.cards[source.object_id].zone)
                self.assertEqual(1,resumed.state.cards[source.object_id].zone_change_counter)
                self.assertEqual('battlefield',resumed.state.cards[held.object_id].zone)
                if aura:self.assertEqual(attachment.object_id,resumed.state.cards[held.object_id].attached_to)
                else:self.assertEqual(4,resumed.state.cards[held.object_id].counters['+1/+1'])
                if sacrifice and not mana:self.assertEqual(before_hand+1,len(resumed.state.players['A'].zones['hand']))
                if mana:self.assertEqual(1,resumed.state.players['A'].mana_pool['C'])
                actions.replay(resumed)
            finally:db.close()

    def test_conditional_return_continuation_rejects_wrong_departures_and_extra_aura_objects(self):
        # Reuse an actual pending cost return; decoding must reject changed
        # source lifetimes and unrelated object selections before resumption.
        from quorune.replacement.replay import ReplacementContinuation
        from quorune.replacement.model import ReplacementEffectError
        from unittest.mock import patch
        import quorune.conditional_exile_return as owner
        original=owner.issue_conditional_return_choice
        observed=[]
        def inspect(host,required):
            original(host,required);observed.append(json.loads(json.dumps(host.state.pending_decision.continuation)))
        with patch.object(owner,'issue_conditional_return_choice',inspect):
            self.run_created_binding_return_choice(sacrifice=True)
        value=observed[0];self.assertEqual('conditional_return',ReplacementContinuation.from_dict(value).resume_kind)
        for change in ('source','aura','event'):
            altered=json.loads(json.dumps(value))
            if change=='source':altered['conditional_departures'][0]['logical_object_id']='new-incarnation'
            elif change=='aura':altered['conditional_aura_targets']={'unrelated':'OBJECT'}
            else:altered['replacement_batch']['events'][0]['payload']['origin']='hand'
            with self.assertRaises(ReplacementEffectError):ReplacementContinuation.from_dict(altered)

    def test_semantic_source_removal_waits_for_return_only_and_replays_from_actual_cast(self):
        self.run_created_binding_return_choice(sacrifice=False)

    def test_actual_sacrifice_cost_return_choice_does_not_resolve_parent_activation(self):
        self.run_created_binding_return_choice(sacrifice=True)

    def test_state_based_source_departure_return_replacement_replays_from_actual_pass(self):
        self.run_created_binding_return_choice(sacrifice=False,state_based=True)

    def test_actual_source_removal_returns_aura_after_attachment_choice_and_replays(self):
        self.run_created_binding_return_choice(sacrifice=False,aura=True)

    def test_actual_sacrifice_cost_aura_return_keeps_parent_ability_pending(self):
        self.run_created_binding_return_choice(sacrifice=True,aura=True)

    def test_native_mana_sacrifice_return_choice_retains_payment_without_repeating_cost(self):
        self.run_created_binding_return_choice(sacrifice=True,mana=True)

    def test_actual_casting_sacrifice_cost_return_choice_keeps_parent_spell_pending(self):
        self.run_created_binding_return_choice(sacrifice=False,cast_cost=True)

    def test_canonical_storage_survives_hydration_and_is_captured_before_source_reset(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.conditional_exile_model import source_conditional_bindings, CONDITIONAL_EXILE_BINDINGS
        from quorune.zone_transitions import ZoneTransitionOwner
        from quorune.errors import GameRuleError
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100302);engine=session.engine
            source=actions.add(engine,'Generic Bound Mentor',ref='stored-conditional-source')
            held=actions.add(engine,'Generic Bound Body',seat='B',zone='exile',ref='stored-held-object')
            binding=ConditionalExileBinding('created-instruction',ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                (ExiledObjectIdentity(held.object_id,held.ref,held.logical_object_id),))
            owner=ZoneTransitionOwner(engine);owner.remember_conditional_exile(binding)
            hydrated=CardInstance.from_dict(json.loads(json.dumps(source.to_dict())))
            self.assertEqual((binding,),source_conditional_bindings(hydrated))
            source.controller='C';source.phased_out=True;source.annotations['removed_abilities']=True
            self.assertEqual((binding,),source_conditional_bindings(source))
            with self.assertRaises(GameRuleError):owner.remember_conditional_exile(binding)
            source.phased_out=False
            snapshot=owner._capture_departure(source,semantic_events=True)
            self.assertEqual((binding,),snapshot.conditional_returns)
            batch_snapshot=owner._simultaneous_departure(source,source_snapshot=snapshot.trigger_sources)[1]
            self.assertEqual((binding,),batch_snapshot.conditional_returns)
            owner.move_card(source.object_id,'hand',reason='conditional binding departure storage boundary')
            self.assertNotIn(CONDITIONAL_EXILE_BINDINGS,source.annotations)
            self.assertEqual((binding,),snapshot.conditional_returns)
            self.assertEqual(('battlefield','B'),(held.zone,held.controller))
            owner.move_card(source.object_id,'battlefield',reason='new source incarnation')
            with self.assertRaises(GameRuleError):owner.remember_conditional_exile(binding)
        finally:Actions.tearDownClass()

    def test_native_simultaneous_source_departures_return_held_objects_in_one_event(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as Actions
        from quorune.zone_transitions import ZoneTransitionOwner
        Actions.setUpClass()
        try:
            actions=Actions();session=actions.session(6100303);engine=session.engine;owner=ZoneTransitionOwner(engine)
            sources=[actions.add(engine,'Generic Bound Mentor',ref='batch-source-'+seat,seat=seat) for seat in 'AC']
            held=[actions.add(engine,'Generic Bound Body',ref='batch-held-'+seat,seat=seat,zone='exile') for seat in 'BD']
            for source,card in zip(sources,held):
                owner.remember_conditional_exile(ConditionalExileBinding('instruction-'+source.ref,
                    ContinuousObjectIdentity(source.object_id,source.logical_object_id),
                    (ExiledObjectIdentity(card.object_id,card.ref,card.logical_object_id),)))
            owner.move_cards_simultaneously(tuple((card.object_id,'hand') for card in sources),reason='two sources depart together')
            self.assertEqual(['battlefield','battlefield'],[card.zone for card in held])
            self.assertEqual(['B','D'],[card.controller for card in held])
            self.assertEqual(held[0].zone_timestamp,held[1].zone_timestamp)
            self.assertEqual([],session.state.stack)
        finally:Actions.tearDownClass()

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


class ConditionalExileOriginalActionTests(unittest.TestCase):
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Actions
    add=_Actions.add
    ready=_Actions.ready
    checkpoint=_Actions.checkpoint
    resolve=_Actions.resolve
    replay=_Actions.replay

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        import tempfile
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.linked_exile_return_model import CONDITIONAL_EXILE_CAPABILITY
        if not load_default_capability_registry().closure((CONDITIONAL_EXILE_CAPABILITY,),profile='commander_review').trusted:
            raise unittest.SkipTest('Conditional gameplay admission waits for complete parent-continuation evidence')
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'original-exile.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/conditional-exile-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Generic original conditional deck',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def session(self,seed):
        from quorune.semantics import SemanticRegistry
        from quorune.oracle_ir import register_generated_programs
        session=self._Actions.session(self,seed)
        session.engine.semantics=SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(self.db,session.engine.semantics,tuple(self.db.iter_cards()),trust_level='provisional',
            capability_registry=self.registry,capability_profile='commander_review',promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        return session

    def test_original_banishing_light_exiles_returns_under_owner_and_replays_actual_actions(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.record import authoritative_state_hash
        session=self.session(6100310);engine=session.engine
        source=self.add(engine,'Banishing Light',zone='hand',ref='original-banish')
        target=self.add(engine,'Generic Bound Body',seat='B',ref='original-held')
        own=self.add(engine,'Generic Bound Body',seat='A',ref='opponent-only-control')
        removal=self.add(engine,'Disenchant',zone='hand',ref='source-removal')
        self.add(engine,'Generic Bound Plains',ref='source-removal-land-one')
        self.add(engine,'Generic Bound Plains',ref='source-removal-land-two')
        program=compile_best_available_card_program(self.db,self.db.lookup('Banishing Light'),semantic_registry=engine.semantics,
            capability_registry=self.registry,capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')['strict_capability_ready'])
        action=self.ready(session,source,{'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        before=authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':[own.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[target.ref]}).ok)
        self.resolve(session);self.assertEqual('exile',session.state.cards[target.object_id].zone)
        from quorune.conditional_exile_model import source_conditional_bindings
        self.assertEqual(1,len(source_conditional_bindings(session.state.cards[source.object_id])))
        session=self.replay(session,load=True);engine=session.engine
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==removal.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[source.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(('battlefield','B'),(session.state.cards[target.object_id].zone,session.state.cards[target.object_id].controller))
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual((),source_conditional_bindings(session.state.cards[source.object_id]))
        for seat in 'ACD':self.assertIn(target.ref,json.dumps(session.packet('pilot:'+seat,full=True)))
        self.replay(session)

    def test_original_banishing_light_source_leaves_before_resolution_exiles_nothing(self):
        from quorune.conditional_exile_model import source_conditional_bindings
        session=self.session(6100311);engine=session.engine
        source=self.add(engine,'Banishing Light',zone='hand',ref='early-banish')
        target=self.add(engine,'Generic Bound Body',seat='B',ref='early-target')
        removal=self.add(engine,'Disenchant',zone='hand',ref='early-removal')
        self.add(engine,'Generic Bound Plains',ref='early-land-one');self.add(engine,'Generic Bound Plains',ref='early-land-two')
        action=self.ready(session,source,{'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[target.ref]}).ok)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==removal.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.resolve(session)
        self.assertEqual('graveyard',session.state.cards[source.object_id].zone)
        self.assertEqual('battlefield',session.state.cards[target.object_id].zone)
        self.assertEqual(0,session.state.cards[target.object_id].zone_change_counter)
        self.assertEqual((),source_conditional_bindings(session.state.cards[source.object_id]))
        self.replay(session,load=True)

    def test_original_portable_hole_rejects_own_land_and_excess_mana_value_and_replays(self):
        from quorune.record import authoritative_state_hash
        session=self.session(6100312);engine=session.engine
        source=self.add(engine,'Portable Hole',zone='hand',ref='hole-source')
        valid=self.add(engine,'Generic Bound Body',seat='B',ref='hole-valid')
        own=self.add(engine,'Generic Bound Body',seat='A',ref='hole-own')
        land=self.add(engine,'Generic Bound Plains',seat='B',ref='hole-land')
        large=self.add(engine,'Banishing Light',seat='B',ref='hole-large')
        action=self.ready(session,source,{'W':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        for invalid in (own,land,large):
            before=authoritative_state_hash(session.state)
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','targets':[invalid.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[valid.ref]}).ok)
        self.resolve(session);self.assertEqual('exile',session.state.cards[valid.object_id].zone)
        self.replay(session,load=True)

    def test_original_banishing_light_can_exile_opponent_noncreature_kindred_permanent(self):
        session=self.session(6100314);engine=session.engine
        source=self.add(engine,'Banishing Light',zone='hand',ref='kindred-banish')
        target=self.add(engine,'Generic Kindred Zombie Enchantment',seat='B',ref='kindred-exile-target')
        action=self.ready(session,source,{'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[target.ref]}).ok)
        self.resolve(session);self.assertEqual('exile',session.state.cards[target.object_id].zone)
        self.replay(session,load=True)

    def test_original_banisher_priest_return_survives_actual_ability_removal_response(self):
        session=self.session(6100313);engine=session.engine
        source=self.add(engine,'Banisher Priest',zone='hand',ref='priest-source')
        target=self.add(engine,'Generic Bound Body',seat='B',ref='priest-held')
        snake=self.add(engine,'Snakeform',zone='hand',ref='priest-ability-removal')
        murder=self.add(engine,'Murder',zone='hand',ref='priest-departure')
        self.add(engine,'Island',ref='priest-island')
        self.add(engine,'Swamp',ref='priest-swamp-one');self.add(engine,'Swamp',ref='priest-swamp-two')
        for index in range(5):self.add(engine,'Generic Bound Plains',ref='priest-land-'+str(index))
        action=self.ready(session,source,{'W':2,'C':1});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[target.ref]}).ok)
        self.resolve(session);self.assertEqual('exile',session.state.cards[target.object_id].zone)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==snake.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.resolve(session);self.assertFalse(session.engine._effective_ability_fragments(session.state.cards[source.object_id]))
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==murder.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'}).ok)
        self.resolve(session)
        self.assertEqual(('battlefield','B'),(session.state.cards[target.object_id].zone,session.state.cards[target.object_id].controller))
        self.replay(session,load=True)

    def test_two_original_banishing_sources_keep_distinct_holds_and_return_only_departed_source(self):
        from quorune.conditional_exile_model import source_conditional_bindings
        session=self.session(6100315);engine=session.engine
        first=self.add(engine,'Banishing Light',zone='hand',ref='first-banish-source')
        second=self.add(engine,'Banishing Light',zone='hand',ref='second-banish-source')
        first_target=self.add(engine,'Generic Bound Body',seat='B',ref='first-held-object')
        second_target=self.add(engine,'Generic Bound Body',seat='D',ref='second-held-object')
        removal=self.add(engine,'Disenchant',zone='hand',ref='first-source-removal')
        for index in range(8):self.add(engine,'Generic Bound Plains',ref='multiple-banish-land-'+str(index))
        action=self.ready(session,first,{'W':1,'C':2});self.checkpoint(session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[first_target.ref]}).ok);self.resolve(session)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==second.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertEqual('semantic.target',self.resolve(session).kind)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','targets':[second_target.ref]}).ok);self.resolve(session)
        self.assertNotEqual(source_conditional_bindings(session.state.cards[first.object_id])[0].source,
            source_conditional_bindings(session.state.cards[second.object_id])[0].source)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(row for row in actions if row.get('card')==removal.ref)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[first.ref],'pay':'auto'}).ok);self.resolve(session)
        self.assertEqual('battlefield',session.state.cards[first_target.object_id].zone)
        self.assertEqual('exile',session.state.cards[second_target.object_id].zone)
        self.assertEqual(1,len(source_conditional_bindings(session.state.cards[second.object_id])))
        self.replay(session,load=True)
