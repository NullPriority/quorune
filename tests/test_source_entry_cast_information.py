"""Independent caster, entry-incarnation and controller facts for CR603.4."""

import unittest


class EntryCastInformationTests(unittest.TestCase):
    def test_actual_caster_is_distinct_from_origin_owner_and_ability_controller(self):
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        source=ContinuousObjectIdentity('card','card@2')
        info=EntryCastInformation(source,CastSpellReceipt('A','hand','B','SPELL','card@1'))
        self.assertTrue(info.was_cast_by('A',source=source))
        self.assertFalse(info.was_cast_by('B',source=source))
        self.assertFalse(info.was_cast_by('C',source=source))

    def test_known_noncast_entry_is_distinct_from_missing_information(self):
        from quorune.source_entry_cast_information import EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        source=ContinuousObjectIdentity('card','card@2')
        self.assertFalse(EntryCastInformation(source,None).was_cast_by('A',source=source))
        with self.assertRaises(ValueError):EntryCastInformation.from_dict(None)

    def test_original_entry_information_cannot_be_reused_by_a_new_incarnation(self):
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        source=ContinuousObjectIdentity('card','card@2')
        info=EntryCastInformation(source,CastSpellReceipt('A','exile','A','SPELL','card@1'))
        self.assertTrue(info.was_cast_by('A',source=source))
        with self.assertRaises(ValueError):
            info.was_cast_by('A',source=ContinuousObjectIdentity('card','card@4'))

    def test_receipt_does_not_infer_casting_from_physical_card_or_token_status(self):
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        source=ContinuousObjectIdentity('copy-result','copy-result@1')
        receipt=CastSpellReceipt('B','exile','B','COPY_CAST','card-copy@0')
        self.assertTrue(EntryCastInformation(source,receipt).was_cast_by('B',source=source))
        self.assertFalse(EntryCastInformation(source,None).was_cast_by('B',source=source))

    def test_wire_values_are_closed_immutable_and_round_trip(self):
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        info=EntryCastInformation(ContinuousObjectIdentity('card','card@2'),
            CastSpellReceipt('A','command','A','SPELL','card@1'))
        value=info.to_dict();self.assertEqual(info,EntryCastInformation.from_dict(value))
        value['cast']['caster']='B';self.assertEqual('A',info.cast.caster)
        for changed in ({**info.to_dict(),'schema_version':True},{**info.to_dict(),'extra':True},
                        {**info.to_dict(),'cast':{'caster':'A'}}):
            with self.assertRaises(ValueError):EntryCastInformation.from_dict(changed)
        for args in (('','hand','A','S','card@1'),('A','stack','A','S','card@1'),
                     ('A','hand','','S','card@1')):
            with self.assertRaises(ValueError):CastSpellReceipt(*args)
        with self.assertRaises(ValueError):
            EntryCastInformation(ContinuousObjectIdentity('card','card@2@3'),None)


class EntryCastCaptureTests(unittest.TestCase):
    def host(self,*,kind='spell',actor='A',origin='hand',context_origin='hand'):
        from types import SimpleNamespace
        from quorune.model import TurnHistory,TurnHistoryEvent
        card=SimpleNamespace(object_id='card',logical_object_id='card@1',owner='B',zone='stack',
            object_kind='card_copy',is_token=True)
        item=SimpleNamespace(card_object_id='card',kind=kind,ref='SPELL',controller='C',
            context={'cast_origin':context_origin})
        history=TurnHistory(turn_sequence=4,spell_origin_history_version=1,
            events=[TurnHistoryEvent('spell_cast',actor=actor,object_incarnation='card@1',origin=origin)])
        return SimpleNamespace(state=SimpleNamespace(stack=[item],turn_sequence=4,turn_history=history,
            cards={'card':card},players={'A':object(),'B':object(),'C':object()})),card

    def test_capture_uses_actual_caster_history_instead_of_current_spell_controller_or_owner(self):
        from quorune.rules.entry_cast_information import capture_stack_cast_receipt
        host,card=self.host();receipt=capture_stack_cast_receipt(host,card)
        self.assertEqual(('A','hand','B','SPELL','card@1'),(receipt.caster,receipt.origin,
            receipt.origin_owner,receipt.stack_ref,receipt.stack_logical_object_id))

    def test_uncast_spell_copy_and_direct_entry_do_not_become_casts(self):
        from quorune.rules.entry_cast_information import capture_stack_cast_receipt
        host,card=self.host(kind='spell_copy')
        self.assertIsNone(capture_stack_cast_receipt(host,card))
        card.zone='graveyard';host.state.stack=[];host.state.turn_history=None
        self.assertIsNone(capture_stack_cast_receipt(host,card))

    def test_missing_duplicate_or_conflicting_provenance_rejects_without_inventing_a_caster(self):
        from quorune.rules.entry_cast_information import capture_stack_cast_receipt
        host,card=self.host(context_origin='exile')
        with self.assertRaises(ValueError):capture_stack_cast_receipt(host,card)
        host,card=self.host();host.state.turn_history.events*=2
        with self.assertRaises(ValueError):capture_stack_cast_receipt(host,card)
        host,card=self.host();host.state.turn_history=None
        with self.assertRaises(ValueError):capture_stack_cast_receipt(host,card)

    def test_exile_origin_and_known_departed_caster_are_retained_as_facts(self):
        from types import SimpleNamespace
        from quorune.rules.entry_cast_information import capture_stack_cast_receipt
        host,card=self.host(actor='B',origin='exile',context_origin='exile')
        host.state.players['B']=SimpleNamespace(status='conceded')
        receipt=capture_stack_cast_receipt(host,card)
        self.assertEqual(('B','exile'),(receipt.caster,receipt.origin))


class ResolvedEntryCastInformationTests(unittest.TestCase):
    def host(self):
        from types import SimpleNamespace
        from quorune.source_entry_cast_information import source_entry_cast_condition
        host,card=EntryCastCaptureTests().host()
        program=SimpleNamespace(event_condition=source_entry_cast_condition(),
            event='permanent.enter.self',active_zone='battlefield',
            coverage=('intervening_condition','source_entry_cast_intervening'))
        host.semantics=SimpleNamespace(programs_for_event=lambda event:(program,))
        host.semantic_program_is_current_trusted=lambda program:True
        return host,card,program

    def test_only_the_resolving_scope_attaches_cast_information_and_does_not_change_unrelated_bytes(self):
        from types import SimpleNamespace
        from quorune.rules.entry_cast_information import resolving_entry_cast_information,information_for_committed_entry
        from quorune.source_entry_cast_information import EntryCastInformation
        host,card,program=self.host();departure=SimpleNamespace(origin='stack',logical_object_id='card@1')
        with resolving_entry_cast_information(host,host.state.stack[0]):
            card.zone='battlefield';card.logical_object_id='card@2'
            info=EntryCastInformation.from_dict(information_for_committed_entry(host,card,departure))
            self.assertEqual('A',info.cast.caster)
        self.assertIsNone(EntryCastInformation.from_dict(information_for_committed_entry(host,card,departure)).cast)
        host.semantic_program_is_current_trusted=lambda program:False
        self.assertIsNone(information_for_committed_entry(host,card,departure))

    def test_retained_entry_uses_copied_ability_controller_and_survives_source_departure(self):
        from quorune.errors import GameRuleError
        from quorune.trigger_discovery import semantic_intervening_condition_matches
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        host,card,program=self.host()
        info=EntryCastInformation(ContinuousObjectIdentity('card','card@2'),CastSpellReceipt('A','hand','B','SPELL','card@1'))
        context={'source_entry_cast_information':info.to_dict(),'source_logical_object_id':'card@2'}
        self.assertTrue(semantic_intervening_condition_matches(host,program,source=None,context=context,
            controller='A',source_object_id='card'))
        self.assertFalse(semantic_intervening_condition_matches(host,program,source=None,context=context,
            controller='C',source_object_id='card'))
        with self.assertRaises(GameRuleError):semantic_intervening_condition_matches(host,program,source=None,
            context={**context,'source_logical_object_id':'card@4'},controller='A',source_object_id='card')

    def test_predicate_structure_and_coverage_cannot_omit_the_information_dependency(self):
        from quorune.source_entry_cast_information import source_entry_cast_program_dependencies,SOURCE_ENTRY_CAST_CAPABILITY
        host,card,program=self.host()
        self.assertEqual((SOURCE_ENTRY_CAST_CAPABILITY,),source_entry_cast_program_dependencies(program))
        program.coverage=()
        with self.assertRaises(ValueError):source_entry_cast_program_dependencies(program)
        program.event_condition=None;program.coverage=('source-entry-cast-condition',)
        with self.assertRaises(ValueError):source_entry_cast_program_dependencies(program)


class EntryCastReceiptNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'entry-cast-information.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Entry information',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def test_actual_cast_receipt_is_read_only_and_survives_pending_stack_load_and_replay(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.rules.entry_cast_information import capture_stack_cast_receipt
        from quorune.record import authoritative_state_hash
        session=H.session(self,959001);engine=session.engine
        source=H.add(self,engine,'Generic Bound Body',zone='hand',ref='CAST_INFORMATION')
        action=H.ready(self,session,source,{'W':1});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        before=authoritative_state_hash(engine.state)
        receipt=capture_stack_cast_receipt(engine,engine.state.cards[source.object_id])
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.assertEqual(('A','hand','A'),(receipt.caster,receipt.origin,receipt.origin_owner))
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertEqual(receipt,capture_stack_cast_receipt(engine,engine.state.cards[source.object_id]))
        H.resolve(self,session)
        self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        self.assertIsNone(capture_stack_cast_receipt(engine,engine.state.cards[source.object_id]))
        H.replay(self,session,load=True)


class SourceCastGuardCompilerTests(unittest.TestCase):
    def test_source_guard_is_material_without_its_capability_and_unknown_guards_remain_unowned(self):
        import json
        from copy import deepcopy
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=query_record('When this creature enters, if you cast it, draw a card.')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status,[(row.reason,row.blockers) for row in ir.material_residuals])
        self.assertEqual({'field':'source_entry_cast_by_controller','op':'eq','value':True},ir.faces[0].nodes[0].event_condition)
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']=='trigger.condition.source_entry_cast')
        cap['status']='blocked';cap['blockers']=['Withdrawn source-entry information owner']
        self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
            capability_profile='commander_review').status)
        with patch('quorune.compiler.source_self_effect_templates.compile_source_self_zone_effect',
                return_value=(None,(),None,(),None)):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        from dataclasses import replace
        for text in ('When this creature enters, if you cast it from your library, draw a card.',
                     'When this creature enters, if it was cast, draw a card.',
                     'When this creature enters, if you cast it, perform an unknown action.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(source,oracle_text=text),capability_registry=registry,
                capability_profile='commander_review').status)


class SourceCastHandConditionTests(unittest.TestCase):
    def test_hand_origin_requires_the_actual_caster_and_that_players_own_hand(self):
        from quorune.source_entry_cast_information import CastSpellReceipt,EntryCastInformation
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        identity=ContinuousObjectIdentity('card','card@2')
        for origin,owner,controller,expected in (
                ('hand','A','A',True),('exile','A','A',False),
                ('hand','B','A',False),('hand','A','B',False)):
            with self.subTest(origin=origin,owner=owner,controller=controller):
                info=EntryCastInformation(identity,CastSpellReceipt('A',origin,owner,'SPELL','card@1'))
                self.assertEqual(expected,info.was_cast_by(controller,source=identity,origin='hand'))
                self.assertEqual(info,EntryCastInformation.from_dict(info.to_dict()))
        self.assertFalse(EntryCastInformation(identity,None).was_cast_by('A',source=identity,origin='hand'))

    def test_retained_hand_condition_rechecks_the_ability_controller_and_strict_dependency(self):
        from quorune.source_entry_cast_information import (CastSpellReceipt,EntryCastInformation,
            source_entry_cast_program_dependencies,validate_source_entry_cast_condition)
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.trigger_discovery import semantic_intervening_condition_matches
        host,card,program=ResolvedEntryCastInformationTests().host()
        program.event_condition={**program.event_condition,'origin':'hand'}
        program.coverage=(*program.coverage,'source_entry_cast_hand_intervening')
        info=EntryCastInformation(ContinuousObjectIdentity('card','card@2'),CastSpellReceipt('A','hand','A','SPELL','card@1'))
        context={'source_entry_cast_information':info.to_dict(),'source_logical_object_id':'card@2'}
        self.assertTrue(semantic_intervening_condition_matches(host,program,source=None,context=context,
            controller='A',source_object_id='card'))
        self.assertFalse(semantic_intervening_condition_matches(host,program,source=None,context=context,
            controller='B',source_object_id='card'))
        self.assertEqual(('trigger.condition.source_entry_cast','trigger.condition.source_entry_cast_hand'),
            source_entry_cast_program_dependencies(program))
        program.coverage=program.coverage[:-1]
        with self.assertRaises(ValueError):source_entry_cast_program_dependencies(program)
        for changed in ({**program.event_condition,'origin':'exile'},
                        {**program.event_condition,'origin':None},
                        {**program.event_condition,'origin_owner':'A'}):
            with self.assertRaises(ValueError):validate_source_entry_cast_condition(changed)
        program.event_condition={key:value for key,value in program.event_condition.items() if key!='origin'}
        program.coverage=(*program.coverage,'source-entry-cast-hand-condition')
        with self.assertRaises(ValueError):source_entry_cast_program_dependencies(program)

    def test_hand_guard_and_its_compiler_production_cannot_bypass_the_new_capability(self):
        import json
        from copy import deepcopy
        from dataclasses import replace
        from unittest.mock import patch
        from common import ROOT
        from test_qualified_zone_event_queries import query_record
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
        source=query_record('When this creature enters, if you cast it from your hand, draw a card.')
        registry=load_default_capability_registry()
        ir=compile_oracle_card(source,capability_registry=registry,capability_profile='commander_review')
        self.assertEqual('exact',ir.status)
        self.assertEqual('hand',ir.faces[0].nodes[0].event_condition['origin'])
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf8'))
        for dependency in ('trigger.condition.source_entry_cast','trigger.condition.source_entry_cast_hand'):
            changed=deepcopy(raw);cap=next(row for row in changed['capabilities'] if row['id']==dependency)
            cap['status']='blocked';cap['blockers']=['Withdrawn hand-origin guard dependency']
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=CapabilityRegistry(changed),
                capability_profile='commander_review').status)
        with patch('quorune.compiler.source_self_effect_templates.compile_source_self_zone_effect',
                return_value=(None,(),None,(),None)):
            self.assertNotEqual('exact',compile_oracle_card(source,capability_registry=registry,
                capability_profile='commander_review').status)
        for text in ('When this creature enters, if you cast it from exile, draw a card.',
                     'When this creature enters, if you cast it from your hand and control a Forest, draw a card.',
                     'Whenever a creature enters, if you cast it from your hand, draw a card.'):
            self.assertNotEqual('exact',compile_oracle_card(replace(source,oracle_text=text),capability_registry=registry,
                capability_profile='commander_review').status)


class SourceCastGuardNativeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    @classmethod
    def setUpClass(cls):
        import tempfile
        from pathlib import Path
        from common import ROOT
        from scripts.build_test_database import build_fixture_database
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition,DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'source-cast-guard.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/discover-cards.json',ROOT/'tests/fixtures/source-entry-cast-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source cast guard',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def closed(self,card):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        from quorune.semantics import SemanticRegistry
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=SemanticRegistry(),capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])

    def test_original_appraiser_cast_triggers_but_actual_reanimation_does_not(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,cast in enumerate((True,False)):
            with self.subTest(cast=cast):
                session=H.session(self,959010+index);engine=session.engine
                top=H.add(self,engine,'Centaur Courser',zone='library',ref='GUARD_TOP')
                unseen=H.add(self,engine,'Generic Bound Growth',zone='library',ref='GUARD_UNSEEN')
                library=engine.state.players['A'].zones['library'];library.remove(top.object_id);library.append(top.object_id)
                source=H.add(self,engine,'Geological Appraiser',zone='hand' if cast else 'graveyard',ref='ORIGINAL_APPRAISER')
                self.closed(source)
                spell=source if cast else H.add(self,engine,'Zombify',zone='hand')
                self.closed(spell)
                action=H.ready(self,session,spell,{'R':2,'C':2} if cast else {'B':1,'C':3})
                H.checkpoint(self,session);before_library=list(library)
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto',
                    **({} if cast else {'targets':[source.ref]})})
                self.assertTrue(accepted.ok,accepted.summary);decision=H.resolve(self,session)
                self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
                if cast:
                    self.assertIsNotNone(decision);self.assertEqual('exile',engine.state.cards[top.object_id].zone)
                    self.assertNotIn('Generic Bound Growth',str(session.packet('pilot:B',full=True)))
                    session=H.replay(self,session,load=True);engine=session.engine
                    before=authoritative_state_hash(engine.state)
                    rejected=session.act('pilot:B',{'action_id':'choose','choice':'decline'})
                    self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
                    accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
                    self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
                    self.assertEqual('hand',engine.state.cards[top.object_id].zone)
                else:
                    self.assertIsNone(decision);self.assertEqual(before_library,engine.state.players['A'].zones['library'])
                    self.assertFalse(any(event.code=='library.discover' for event in engine.state.events))
                H.replay(self,session,load=True)

    def test_original_entry_fact_survives_actual_source_death_but_not_reanimation(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959020);engine=session.engine
        top=H.add(self,engine,'Centaur Courser',zone='library',ref='DEPARTURE_TOP')
        source=H.add(self,engine,'Geological Appraiser',zone='hand',ref='DEPARTURE_SOURCE');self.closed(source)
        murder=H.add(self,engine,'Murder',zone='hand',ref='DEPARTURE_MURDER')
        reanimate=H.add(self,engine,'Zombify',zone='hand',ref='DEPARTURE_REANIMATE')
        action=H.ready(self,session,source,{'R':2,'B':3,'C':6});H.checkpoint(self,session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'manual','payment':{'R':2,'C':2}})
        self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(12):
            if engine.state.cards[source.object_id].zone=='battlefield':break
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'})
            self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==murder.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[source.ref],
            'pay':'manual','payment':{'B':2,'C':1}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('graveyard',engine.state.cards[source.object_id].zone)
        self.assertEqual('exile',engine.state.cards[top.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'decline'})
        self.assertTrue(accepted.ok,accepted.summary);H.resolve(self,session)
        before_library=list(engine.state.players['A'].zones['library'])
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        cast=next(row for row in actions if row.get('card')==reanimate.ref)
        accepted=session.act('pilot:A',{'action_id':cast['id'],'targets':[source.ref],
            'pay':'manual','payment':{'B':1,'C':3}})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual('battlefield',engine.state.cards[source.object_id].zone)
        self.assertEqual(before_library,engine.state.players['A'].zones['library'])
        self.assertEqual(1,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_canonical_spell_and_trigger_copy_fixtures_recheck_their_own_controller(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.semantic_runtime import CopyStackItemIntent
        from quorune.replacement.immutable import FrozenMap
        for index,kind in enumerate(('spell','trigger')):
            with self.subTest(copy_kind=kind):
                session=H.session(self,959030+index);engine=session.engine
                top=H.add(self,engine,'Centaur Courser',zone='library',ref='COPY_A_TOP')
                H.add(self,engine,'Generic Bound Body',seat='B',zone='library',ref='COPY_B_TOP')
                source=H.add(self,engine,'Geological Appraiser',zone='hand',ref='COPY_SOURCE');self.closed(source)
                action=H.ready(self,session,source,{'R':2,'C':2})
                accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
                self.assertTrue(accepted.ok,accepted.summary)
                if kind=='trigger':
                    for _ in range(12):
                        if engine.state.cards[source.object_id].zone=='battlefield':break
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                original=next(item for item in session.state.stack if item.source_object_id==source.object_id
                    and item.kind=='triggered_ability') if kind=='trigger' else session.state.stack[-1]
                # Canonical copies are explicit fixture preconditions, as in
                # the existing Miracle copy tests; no copy-producing card claim.
                copied=engine.copy_stack_item_intent(CopyStackItemIntent(actor='B',controller='B',
                    target_stack_ref=original.ref,targets=(),target_groups=FrozenMap(),reason='Source condition copied-occurrence fixture'))
                engine.permissions.invalidate_current();engine.state.pending_decision=None
                engine.state.priority_passes=[];engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
                before_b=list(engine.state.players['B'].zones['library'])
                self.assertIsNotNone(H.resolve(self,session))
                self.assertEqual(['pilot:A'],list(session.pending_principals()))
                self.assertFalse(any(item.ref==copied for item in session.state.stack))
                self.assertEqual(before_b,engine.state.players['B'].zones['library'])
                self.assertEqual('exile',engine.state.cards[top.object_id].zone)
                session=H.replay(self,session,load=True);engine=session.engine
                self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok);H.resolve(self,session)
                self.assertFalse(any(event.code=='library.discover' and event.actor=='B' for event in engine.state.events))
                H.replay(self,session,load=True)

    def test_cast_copy_entry_uses_its_own_receipt_instead_of_the_uncast_target(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959040);engine=session.engine
        target=H.add(self,engine,'Geological Appraiser',ref='UNCast_COPY_TARGET');self.closed(target)
        top=H.add(self,engine,'Centaur Courser',zone='library',ref='COPY_ENTRY_TOP')
        source=H.add(self,engine,'Mirror Image',zone='hand',ref='CAST_COPY_ENTRY');self.closed(source)
        action=H.ready(self,session,source,{'U':1,'C':2});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
        self.assertIsNotNone(H.resolve(self,session));self.assertEqual('replacement.order',session.state.pending_decision.kind)
        session=H.replay(self,session,load=True);engine=session.engine
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        selected=next(option['id'] for option in options if target.ref in option['id'] and not option['id'].startswith('decline:'))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','replacement':selected}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('Geological Appraiser',engine.display_name(source.object_id))
        self.assertEqual('exile',engine.state.cards[top.object_id].zone)
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok);H.resolve(self,session)
        H.replay(self,session,load=True)

    def test_original_appraiser_cast_from_exile_retains_its_caster_and_triggers(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,959050);engine=session.engine
        appraiser=H.add(self,engine,'Geological Appraiser',zone='library',ref='EXILE_CAST_APPRAISER');self.closed(appraiser)
        top=H.add(self,engine,'Centaur Courser',zone='library',ref='EXILE_CAST_NEXT')
        library=engine.state.players['A'].zones['library']
        library.remove(appraiser.object_id);library.append(appraiser.object_id)
        target=H.add(self,engine,'Generic Bound Body',seat='B',ref='EXILE_CAST_TARGET')
        source=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(source)
        action=H.ready(self,session,source,{'R':2,'C':4});H.checkpoint(self,session)
        self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('exile',engine.state.cards[appraiser.object_id].zone)
        session=H.replay(self,session,load=True);engine=session.engine
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'cast'}).ok)
        self.assertIsNotNone(H.resolve(self,session))
        self.assertEqual('battlefield',engine.state.cards[appraiser.object_id].zone)
        self.assertEqual('exile',engine.state.cards[top.object_id].zone)
        receipt=session.state.stack[0].context['source_entry_cast_information']['cast']
        self.assertEqual(('A','exile'),(receipt['caster'],receipt['origin']))
        self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'decline'}).ok);H.resolve(self,session)
        self.assertEqual(2,sum(event.code=='library.discover' for event in engine.state.events))
        H.replay(self,session,load=True)

    def test_original_coal_stoker_hand_cast_uses_stack_but_exile_cast_does_not_trigger(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.record import authoritative_state_hash
        for index,origin in enumerate(('hand','exile')):
            with self.subTest(origin=origin):
                session=H.session(self,959060+index);engine=session.engine
                secret=H.add(self,engine,'Generic Bound Growth',zone='library',ref='HAND_GUARD_SECRET')
                coal=H.add(self,engine,'Coal Stoker',zone='hand' if origin=='hand' else 'library',ref='HAND_GUARD_COAL')
                self.closed(coal)
                if origin=='hand':
                    action=H.ready(self,session,coal,{'R':1,'C':3});H.checkpoint(self,session)
                    self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'pay':'auto'}).ok)
                    for _ in range(12):
                        if engine.state.cards[coal.object_id].zone=='battlefield':break
                        self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
                    self.assertEqual('battlefield',engine.state.cards[coal.object_id].zone)
                    triggers=[item for item in engine.state.stack if item.kind=='triggered_ability' and item.source_object_id==coal.object_id]
                    self.assertEqual(1,len(triggers))
                    self.assertEqual(0,engine.state.players['A'].mana_pool.get('R',0))
                    receipt=triggers[0].context['source_entry_cast_information']['cast']
                    self.assertEqual(('A','hand','A'),(receipt['caster'],receipt['origin'],receipt['origin_owner']))
                    session=H.replay(self,session,load=True);engine=session.engine
                    self.assertEqual(('pilot:A',),tuple(session.pending_principals()))
                    before=authoritative_state_hash(engine.state)
                    self.assertFalse(session.act('pilot:B',{'action_id':'pass'}).ok)
                    self.assertEqual(before,authoritative_state_hash(engine.state))
                else:
                    target=H.add(self,engine,'Generic Bound Body',seat='B',ref='HAND_GUARD_TARGET')
                    discovery=H.add(self,engine,'Daring Discovery',zone='hand');self.closed(discovery)
                    action=H.ready(self,session,discovery,{'R':2,'C':4});H.checkpoint(self,session)
                    self.assertTrue(session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'}).ok)
                    self.assertIsNotNone(H.resolve(self,session))
                    self.assertEqual('exile',engine.state.cards[coal.object_id].zone)
                    session=H.replay(self,session,load=True);engine=session.engine
                    self.assertTrue(session.act('pilot:A',{'action_id':'choose','choice':'cast'}).ok)
                    spell=next(item for item in engine.state.stack if item.card_object_id==coal.object_id and item.kind=='spell')
                    self.assertEqual('exile',spell.context['cast_origin'])
                self.assertNotIn(secret.printed_name,str(session.packet('pilot:B',full=True)))
                self.assertIsNone(H.resolve(self,session))
                self.assertEqual('battlefield',engine.state.cards[coal.object_id].zone)
                self.assertEqual(3 if origin=='hand' else 0,engine.state.players['A'].mana_pool.get('R',0))
                self.assertFalse(engine.state.stack)
                H.replay(self,session,load=True)
