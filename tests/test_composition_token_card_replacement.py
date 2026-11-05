"""Card-only replacement follows the merged permanent's top token status."""
from pathlib import Path
import tempfile
import unittest

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition,DeckEntry
from quorune.permanent_composition import MergePosition
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.composition_merge import prepare_composition_merge
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


class CardComponentReplacementModelTests(unittest.TestCase):
    def mixed_order_request(self):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.composition_batch_order_model import CompositionDepartureSubject,CompositionDepartureBatchRequest
        from quorune.composition_order_model import CompositionOrderRequest
        from quorune.model import CardInstance
        from quorune.replacement.immutable import FrozenMap
        root=CardInstance(object_id='T1',ref='T1',oracle_id='custom-token:T1',printed_name='Token',
            owner='A',controller='B',zone='battlefield',is_token=True,object_kind='token')
        original=CompositionObjectSnapshot.capture(root,copiable_values={},controller='B')
        storage=PermanentCompositionStorage.single(original)
        for oid,is_copy in (('T2',True),('C1',False),('C2',False)):
            card=CardInstance(object_id=oid,ref=oid,oracle_id='custom-token:'+oid,printed_name=oid,
                owner='A',controller='B',zone='stack',object_kind='spell_copy' if is_copy else 'card')
            incoming=CompositionObjectSnapshot.capture(card,copiable_values={},controller='B',stack_ref='S'+oid,
                stack_kind='spell_copy' if is_copy else 'spell')
            storage=storage.merge(incoming,position=MergePosition.BOTTOM,timestamp=len(storage.parts),expected_object=original.identity)
        root.permanent_composition=storage
        subject=CompositionDepartureSubject(FrozenMap(root.to_dict()),'graveyard','graveyard','A',
            component_destinations=FrozenMap({'C1':'exile','C2':'exile'}),
            component_ordering_actors=FrozenMap({'C1':'D','C2':'D'}))
        batch=CompositionDepartureBatchRequest((subject,),('B','C','D','A'))
        return CompositionOrderRequest(root.to_dict(),'graveyard','C','D','destroy',schema_version=4,
            resolved_destination='graveyard',ordering_actor='D',batch_request=batch)

    def test_two_destination_groups_keep_exiler_and_token_owner_orders_in_apnap(self):
        from dataclasses import replace
        from quorune.composition_order_model import CompositionOrderRequest
        request=self.mixed_order_request()
        self.assertEqual(('C1','C2'),request.refs);self.assertEqual('exile',request.current_destination)
        request.validate_order(('C2','C1'))
        with self.assertRaises(ValueError):request.ordered_ids(('C2','C1'))
        request=replace(request,chooser='A',ordering_actor='A',orders=(('C2','C1'),))
        self.assertEqual(('T1','T2'),request.refs);self.assertEqual('graveyard',request.current_destination)
        self.assertEqual(('T2','T1','C2','C1'),request.ordered_ids(('T2','T1')))
        self.assertEqual(request,CompositionOrderRequest.from_dict(request.to_dict()))

    def test_component_order_decoder_rejects_foreign_actors_scopes_and_saved_orders(self):
        from copy import deepcopy
        from quorune.composition_order_model import CompositionOrderRequest
        wire=self.mixed_order_request().to_dict()
        for change in ('wrong_current_actor','unknown_actor','partial_cards','token_redirect','foreign_saved_order','string_order','extra'):
            with self.subTest(change=change):
                value=deepcopy(wire);subject=value['batch_request']['subjects'][0]
                if change=='wrong_current_actor':value['chooser']='A'
                elif change=='unknown_actor':subject['component_ordering_actors']={'C1':'unknown','C2':'unknown'}
                elif change=='partial_cards':
                    subject['component_destinations'].pop('C2');subject['component_ordering_actors'].pop('C2')
                elif change=='token_redirect':
                    subject['component_destinations']['T2']='exile';subject['component_ordering_actors']['T2']='D'
                elif change=='foreign_saved_order':value.update(orders=[['T1','T2']],chooser='A',ordering_actor='A')
                elif change=='string_order':value['orders']=['C1']
                else:value['extra']=True
                with self.assertRaises(ValueError):CompositionOrderRequest.from_dict(value)

    def test_another_group_can_suspend_only_the_exact_single_composition_completion(self):
        from types import SimpleNamespace
        from quorune.composition_ordering import suspend_composition_completion
        from quorune.semantic_runtime.composition_intent import ResumeCompositionOrderIntent
        from quorune.semantic_choices.model import SemanticChoiceCompletion
        from quorune.errors import GameRuleError
        request=self.mixed_order_request()
        intent=ResumeCompositionOrderIntent(request,{}, {},('C1','C2'))
        for change in ('multiple_intents','prepend','repeat','wrong_index','unrelated_intent','wrong_operation'):
            with self.subTest(change=change):
                completion=SemanticChoiceCompletion(intents=(intent,))
                continuation=SimpleNamespace(effect={'op':'composition_departure_order'})
                index=0;current=intent
                if change=='multiple_intents':completion=SemanticChoiceCompletion(intents=(intent,intent))
                elif change=='prepend':completion=SemanticChoiceCompletion(intents=(intent,),prepend_effects=({'op':'other'},))
                elif change=='repeat':completion=SemanticChoiceCompletion(intents=(intent,),repeat_effect={'op':'other'})
                elif change=='wrong_index':index=1
                elif change=='unrelated_intent':current=object()
                else:continuation.effect={'op':'other'}
                with self.assertRaises(GameRuleError):
                    suspend_composition_completion(None,None,continuation,completion,index,current,None)

    def event(self):
        from quorune.replacement import ReplaceableEvent,AffectedObject
        from quorune.composition_zone_replacement_model import COMPONENTS_FIELD,CardZoneComponent
        return ReplaceableEvent('departure','zone.change',None,affected_object=AffectedObject('token','A','B'),
            payload={'origin':'battlefield','destination':'graveyard','object_kind':'noncard',
                'object_ref':'TOKEN','logical_object_id':'token@0','owner':'A','object_types':['creature'],
                COMPONENTS_FIELD:[CardZoneComponent('card','card@0','CARD').to_dict()]})

    def effects(self):
        from quorune.replacement import ReplacementEffect,ReplacementClass,CreateAffectedObjectCounter
        from quorune.replacement.operations import SetField
        return (ReplacementEffect('cards','D','zone.change',ReplacementClass.OTHER,
            {'destination':'graveyard','object_kind':'card'},
            (SetField('destination','exile'),CreateAffectedObjectCounter('void',1,'D','D',0))),
            ReplacementEffect('whole','C','zone.change',ReplacementClass.OTHER,
                {'destination':'graveyard'},(SetField('destination','library'),)))

    def apply(self,event,effects,selection):
        from quorune.replacement import replacement_choice,apply_replacement
        choice=replacement_choice(event,effects)
        self.assertEqual('B',choice.chooser)
        self.assertIn(selection,choice.options)
        return apply_replacement(choice,effects,selection)

    def test_card_and_whole_replacements_compete_in_the_same_affected_player_choice(self):
        from quorune.replacement import replacement_choice
        self.assertEqual(('cards','whole'),replacement_choice(self.event(),self.effects()).options)

    def test_card_only_result_keeps_token_destination_and_scopes_counter_leaf(self):
        from quorune.replacement.composition_scope import card_component_event
        event=self.apply(self.event(),self.effects(),'cards');group=card_component_event(event)
        self.assertEqual('graveyard',event.payload['destination']);self.assertEqual((),event.children)
        self.assertEqual('exile',group.payload['destination']);self.assertEqual(1,len(group.children))
        self.assertEqual('D',group.children[0].payload['placing_player'])
        self.assertEqual('exile',group.children[0].payload['target_zone'])

    def test_whole_first_removes_inapplicable_card_replacement(self):
        from quorune.replacement import replacement_choice
        from quorune.replacement.composition_scope import card_component_event
        event=self.apply(self.event(),self.effects(),'whole')
        self.assertEqual('library',event.payload['destination'])
        self.assertEqual('library',card_component_event(event).payload['destination'])
        self.assertIsNone(replacement_choice(event,self.effects()))

    def test_card_first_then_whole_updates_all_parts_and_retargets_card_counter(self):
        from quorune.replacement.composition_scope import card_component_event
        event=self.apply(self.event(),self.effects(),'cards')
        event=self.apply(event,self.effects(),'whole');group=card_component_event(event)
        self.assertEqual('library',event.payload['destination']);self.assertEqual('library',group.payload['destination'])
        self.assertEqual('library',group.children[0].payload['target_zone'])
        self.assertEqual(('cards','whole'),event.applied_effects)

    def test_component_result_survives_closed_replacement_event_round_trip(self):
        from quorune.replacement import ReplaceableEvent
        from quorune.replacement.composition_scope import card_component_event
        event=self.apply(self.event(),self.effects(),'cards')
        self.assertEqual(card_component_event(event),card_component_event(ReplaceableEvent.from_dict(event.to_dict())))

    def test_malformed_or_foreign_component_result_is_rejected(self):
        from dataclasses import replace
        from quorune.replacement.immutable import thaw_value
        from quorune.replacement.composition_scope import card_component_event,RESULT_FIELD,COMPONENTS_FIELD
        event=self.apply(self.event(),self.effects(),'cards')
        for change in ('foreign','recursive','missing_scope'):
            with self.subTest(change=change):
                payload=thaw_value(event.payload)
                if change=='foreign':payload[RESULT_FIELD]['event_id']='another:cards'
                elif change=='recursive':payload[RESULT_FIELD]['payload'][COMPONENTS_FIELD]=payload[COMPONENTS_FIELD]
                else:payload.pop(COMPONENTS_FIELD)
                with self.assertRaises(ValueError):card_component_event(replace(event,payload=payload))


class CompositionTokenCardReplacementTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'mixed-token-card.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/face-down-copy-cards.json',
            ROOT/'tests/fixtures/double-faced-spell-copy-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Mixed token/card replacement',[
            DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def assert_arrival_fault_rolls_back(self,session,pilot,ordered):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        before=authoritative_state_hash(session.engine.state);arrivals=[]
        original_commit=ZoneTransitionOwner._commit_move
        def fail_after_arrival(owner,plan,*args,**kwargs):
            original_commit(owner,plan,*args,**kwargs);arrivals.append(plan.card.object_id)
            raise GameRuleError('Injected mixed-component arrival fault')
        with patch.object(ZoneTransitionOwner,'_commit_move',fail_after_arrival):
            rejected=session.act(pilot,{'action_id':'choose','cards':ordered})
        self.assertFalse(rejected.ok);self.assertEqual(1,len(arrivals))
        self.assertIn('Injected mixed-component arrival fault',rejected.summary)
        self.assertEqual(before,authoritative_state_hash(session.engine.state))
        self.assertEqual([pilot],list(session.pending_principals()))

    def journey(self,*,card_on_top,seed,extra_card=False,extra_token=False):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.semantic_runtime.zone_replacements import collect_zone_change_replacement_effects
        session=H.session(self,seed);engine=session.engine
        original=H.add(self,engine,'Ornithopter',ref='COPY_TOKEN_ORIGINAL')
        copy_spell=H.add(self,engine,'Cackling Counterpart',zone='hand',ref='CREATE_COPY_TOKEN')
        for card in (original,copy_spell):self.assert_closed(session,card)
        offer=H.ready(self,session,copy_spell,{'U':2,'C':1})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'targets':[original.ref],'pay':'auto'}).ok)
        self.assertIsNone(H.resolve(self,session))
        token=next(card for card in engine.state.cards.values() if card.is_token and card.zone=='battlefield')
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='MIXED_PHYSICAL_CARD')
        self.assert_closed(session,incoming);offer=H.ready(self,session,incoming,{})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=ContinuousObjectIdentity(token.object_id,token.logical_object_id),
            source=ContinuousObjectIdentity(incoming.object_id,incoming.logical_object_id),
            stack_ref=reference,position=MergePosition.TOP if card_on_top else MergePosition.BOTTOM))
        extra=None
        if extra_card:
            extra=H.add(self,engine,'Ornithopter',zone='hand',ref='SECOND_MIXED_PHYSICAL_CARD')
            self.assert_closed(session,extra);offer=H.ready(self,session,extra,{})
            self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
            reference=next(item.ref for item in engine.state.stack if item.card_object_id==extra.object_id)
            ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
                target=ContinuousObjectIdentity(token.object_id,token.logical_object_id),
                source=ContinuousObjectIdentity(extra.object_id,extra.logical_object_id),stack_ref=reference,
                position=MergePosition.BOTTOM))
        second_token=None
        if extra_token:
            observer=H.add(self,engine,'Generic Complete Creature Copy Observer',zone='hand',ref='MIXED_COPY_OBSERVER')
            self.assert_closed(session,observer);offer=H.ready(self,session,observer,{'C':5,'U':1,'W':1})
            self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
            self.assertIsNone(H.resolve(self,session))
            copied_source=H.add(self,engine,'Ornithopter',zone='hand',ref='MIXED_COPIED_SPELL')
            self.assert_closed(session,copied_source);offer=H.ready(self,session,copied_source,{})
            self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
            for _ in range(12):
                if any(item.kind=='spell_copy' for item in engine.state.stack):break
                self.assertTrue(session.act(session.pending_principals()[0],{'action_id':'pass'}).ok)
            copied=next(item for item in engine.state.stack if item.kind=='spell_copy')
            second_token=engine.state.cards[copied.card_object_id]
            ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
                target=ContinuousObjectIdentity(token.object_id,token.logical_object_id),
                source=ContinuousObjectIdentity(second_token.object_id,second_token.logical_object_id),
                stack_ref=copied.ref,position=MergePosition.BOTTOM))
            engine.permissions.invalidate_current();engine.state.pending_decision=None
            engine._grant_priority('A');engine.pump();H.checkpoint(self,session)
            self.assertIsNone(H.resolve(self,session))
        self.assertEqual(not card_on_top,token.is_token)
        # Reviewed existing replacement control; its unrepresented abilities
        # are not promoted or counted as strict whole-card support here.
        replacer=H.add(self,engine,'Dauthi Voidwalker',seat='D',ref='CARD_ONLY_REPLACER')
        effects=collect_zone_change_replacement_effects(engine,sources=(replacer,),
            source_zones={replacer.object_id:'battlefield'})
        self.assertTrue(effects,'The existing reviewed replacement must actually be registered and operative')
        murder=H.add(self,engine,'Murder',seat='C',zone='hand',ref='MIXED_MURDER')
        self.assert_closed(session,murder)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C';engine.state.started=True;engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['C'].mana_pool.update({'B':2,'C':1})
        engine.state.turn_history.departure_history_version=1
        engine.state.turn_history.arrival_history_version=1
        engine._grant_priority('C');engine.pump();H.checkpoint(self,session)
        offered=next(row for row in session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
            if row.get('card')==murder.ref)
        accepted=session.act('pilot:C',{'action_id':offered['id'],'targets':[token.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=H.resolve(self,session)
        if card_on_top or extra_card:
            self.assertEqual('semantic.choice',decision.kind);self.assertEqual(['pilot:D'],list(session.pending_principals()))
            self.assertEqual('battlefield',engine.state.cards[token.object_id].zone)
            self.assertNotIn(incoming.object_id,engine.state.cards)
            session=H.replay(self,session,load=True);engine=session.engine
            before=authoritative_state_hash(engine.state)
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','cards':[incoming.ref,token.ref]}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
            ordered=[extra.ref,incoming.ref] if extra_card else [incoming.ref,token.ref]
            if extra_card:
                self.assertEqual(set(ordered),set(session.packet('pilot:D',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']))
                self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':[incoming.ref,token.ref]}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                self.assertNotIn(extra.object_id,engine.state.cards)
                if not extra_token:self.assert_arrival_fault_rolls_back(session,'pilot:D',ordered)
            accepted=session.act('pilot:D',{'action_id':'choose','cards':ordered})
            self.assertTrue(accepted.ok,accepted.summary)
            decision=H.resolve(self,session)
            if extra_token:
                self.assertEqual('semantic.choice',decision.kind)
                self.assertEqual(['pilot:A'],list(session.pending_principals()))
                self.assertEqual('battlefield',engine.state.cards[token.object_id].zone)
                self.assertFalse(any(oid in engine.state.cards for oid in (incoming.object_id,extra.object_id,second_token.object_id)))
                self.assertEqual(0,sum(event.kind=='creature_died' for event in engine.state.turn_history.events))
                session=H.replay(self,session,load=True);engine=session.engine
                ordered=[second_token.ref,token.ref]
                before=authoritative_state_hash(engine.state)
                self.assertFalse(session.act('pilot:D',{'action_id':'choose','cards':ordered}).ok)
                self.assertEqual(before,authoritative_state_hash(engine.state))
                self.assert_arrival_fault_rolls_back(session,'pilot:A',ordered)
                accepted=session.act('pilot:A',{'action_id':'choose','cards':ordered})
                self.assertTrue(accepted.ok,accepted.summary)
                decision=H.resolve(self,session)
        self.assertIsNone(decision)
        self.assertEqual('outside',engine.state.cards[token.object_id].zone,
            'The ceased token may retain an authoritative tracking record outside the game')
        self.assertFalse(any(token.object_id in ids for player in engine.state.players.values() for ids in player.zones.values()))
        if extra_token:
            self.assertEqual('outside',engine.state.cards[second_token.object_id].zone)
            self.assertFalse(any(second_token.object_id in ids for player in engine.state.players.values() for ids in player.zones.values()))
        self.assertEqual('exile',engine.state.cards[incoming.object_id].zone)
        self.assertEqual(1,engine.state.cards[incoming.object_id].counters.get('void',0))
        if not card_on_top:
            replacements=[event for event in engine.state.events if event.code=='replacement.apply'
                and event.details.get('source')==replacer.ref and event.details.get('object')==token.ref]
            self.assertTrue(replacements)
            self.assertEqual('cards',replacements[-1].details.get('component_scope'))
            self.assertEqual('exile',replacements[-1].details['destination'])
        if extra_card:
            self.assertEqual('exile',engine.state.cards[extra.object_id].zone)
            self.assertEqual(1,engine.state.cards[extra.object_id].counters.get('void',0))
            self.assertLess(engine.state.cards[extra.object_id].zone_timestamp,engine.state.cards[incoming.object_id].zone_timestamp)
        self.assertEqual(0 if card_on_top else 1,
            sum(event.kind=='creature_died' for event in engine.state.turn_history.events))
        self.assertEqual('battlefield',engine.state.cards[original.object_id].zone)
        H.replay(self,session,load=True)

    def test_actual_card_topped_parent_applies_card_only_replacement_to_all_parts(self):
        self.journey(card_on_top=True,seed=959380)

    def test_actual_token_topped_parent_dies_once_and_exiles_only_physical_card_parts(self):
        self.journey(card_on_top=False,seed=959381)

    def test_actual_token_parent_exiler_orders_only_card_parts_before_atomic_departure(self):
        self.journey(card_on_top=False,seed=959382,extra_card=True)

    def test_actual_card_and_token_groups_wait_for_exiler_then_owner_and_resume_after_load(self):
        self.journey(card_on_top=False,seed=959383,extra_card=True,extra_token=True)
