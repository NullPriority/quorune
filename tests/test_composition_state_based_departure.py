"""State-based deaths retain one permanent and every physical arrival."""
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


class CompositionBatchOrderModelTests(unittest.TestCase):
    def frame(self,*,departed_anchor=False):
        from quorune.composition_snapshot import CompositionObjectSnapshot
        from quorune.composition_storage import PermanentCompositionStorage
        from quorune.composition_batch_order_model import (CompositionDepartureSubject,
            CompositionDepartureBatchRequest,CompositionStateBasedOrderContinuation)
        from quorune.model import CardInstance
        from quorune.replacement.immutable import FrozenMap
        subjects=[];battlefield=[]
        for seat in ('A','D'):
            root=CardInstance(object_id=seat+'root',ref=seat+'root',oracle_id='custom-token:'+seat,
                printed_name='Root',owner=seat,controller=seat,zone='battlefield')
            part=CardInstance(object_id=seat+'part',ref=seat+'part',oracle_id='custom-token:'+seat+'part',
                printed_name='Part',owner=seat,controller=seat,zone='stack')
            original=CompositionObjectSnapshot.capture(root,copiable_values={},controller=seat)
            incoming=CompositionObjectSnapshot.capture(part,copiable_values={},controller=seat,
                stack_ref='S'+seat,stack_kind='spell')
            root.permanent_composition=PermanentCompositionStorage.single(original).merge(incoming,
                position=MergePosition.BOTTOM,timestamp=1,expected_object=original.identity)
            subjects.append(CompositionDepartureSubject(FrozenMap(root.to_dict()),'graveyard','graveyard',seat))
            battlefield.append({'object_id':root.object_id,'logical_object_id':root.logical_object_id})
        ordinary=CardInstance(object_id='ordinary',ref='ordinary',oracle_id='custom-token:ordinary',
            printed_name='Ordinary',owner='A',controller='B',zone='battlefield')
        subjects.append(CompositionDepartureSubject(FrozenMap(ordinary.to_dict()),'graveyard','graveyard','A'))
        battlefield.append({'object_id':ordinary.object_id,'logical_object_id':ordinary.logical_object_id})
        order=('D','A','B') if departed_anchor else ('C','D','A','B')
        request=CompositionDepartureBatchRequest(tuple(subjects),order)
        frame={'active_player':'C','phase':'precombat_main','step':'main','turn_sequence':1,
            'turn_order':['A','B','C','D'],'active_seats':list(order),'stack_refs':[], 'battlefield':battlefield}
        return CompositionStateBasedOrderContinuation(request,FrozenMap(frame),(),())

    def test_owner_apnap_orders_include_ordinary_cards_and_allow_interleaving(self):
        from quorune.composition_batch_order_model import CompositionStateBasedOrderContinuation
        frame=self.frame();self.assertEqual('D',frame.current_choice[0][0])
        frame=frame.with_order(['Dpart','Droot']);self.assertEqual('A',frame.current_choice[0][0])
        frame=frame.with_order(['Apart','ordinary','Aroot']);self.assertIsNone(frame.current_choice)
        ids=frame.request.ordered_ids(frame.orders)
        self.assertEqual(['Apart','ordinary','Aroot'],[oid for oid in ids if oid.startswith('A') or oid=='ordinary'])
        self.assertEqual(frame,CompositionStateBasedOrderContinuation.from_dict(frame.to_dict()))
        departed=self.frame(departed_anchor=True)
        self.assertEqual('C',departed.state_based_frame['active_player'])
        self.assertEqual('D',departed.current_choice[0][0])

    def test_closed_batch_codec_rejects_unknown_boolean_duplicate_and_mismatched_frames(self):
        from copy import deepcopy
        from quorune.composition_batch_order_model import CompositionStateBasedOrderContinuation
        frame=self.frame();wire=frame.to_dict()
        def altered(path,value):
            result=deepcopy(wire);parent=result
            for field in path[:-1]:parent=parent[field]
            parent[path[-1]]=value
            return result
        variants=[altered(('schema_version',),True),altered(('extra',),True),
            altered(('request','schema_version'),True),altered(('request','apnap_order'),['A','A','B','D']),
            altered(('state_based_frame','active_player'),'unknown'),
            altered(('state_based_frame','active_seats'),['A','B','C']),
            altered(('state_based_frame','battlefield'),[]),
            altered(('request','subjects'),[wire['request']['subjects'][0],wire['request']['subjects'][0]])]
        for index,value in enumerate(variants):
            with self.subTest(index=index),self.assertRaises(ValueError):
                CompositionStateBasedOrderContinuation.from_dict(value)
        for order in ([],['Droot'],['Droot','Droot'],['Droot','unknown']):
            with self.subTest(order=order),self.assertRaises(ValueError):frame.with_order(order)

    def test_physical_order_uses_the_existing_generic_ordered_reference_form(self):
        from quorune.composition_state_based_order import _payload
        from quorune.choice_forms import build_action_form,delegated_choice_fields
        frame=self.frame();context=_payload(frame);action=context['legal_actions'][0]
        form=build_action_form(action,decision_kind='permanent.composition_order',context=context)
        self.assertIsNotNone(form)
        self.assertEqual({'cards'},delegated_choice_fields(action,decision_kind='permanent.composition_order',context=context))
        field=next(field for field in form['fields'] if field['name']=='cards')
        self.assertTrue(field['ordered']);self.assertEqual('bottom_to_top',field['order'])


class CompositionStateBasedDepartureTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory()
        path=Path(cls.temporary.name)/'composition-state-based.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',
            ROOT/'tests/fixtures/composition-state-based-cards.json',
            ROOT/'tests/fixtures/state-based-destination-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Composed state-based departure',
            [DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,
            capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def fixture(self,root_name,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,seed);engine=session.engine
        root=H.add(self,engine,root_name,ref='SBA_COMPOSED_ROOT')
        engine.state.players['A'].zones['battlefield'].remove(root.object_id)
        engine.state.players['B'].zones['battlefield'].append(root.object_id);root.controller='B'
        incoming=H.add(self,engine,'Ornithopter',zone='hand',ref='SBA_COMPOSED_PART')
        self.assert_closed(session,root);self.assert_closed(session,incoming)
        offer=H.ready(self,session,incoming,{})
        self.assertTrue(session.act('pilot:A',{'action_id':offer['id'],'pay':'auto'}).ok)
        spell=next(item for item in engine.state.stack if item.card_object_id==incoming.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=ContinuousObjectIdentity(root.object_id,root.logical_object_id),
            source=ContinuousObjectIdentity(incoming.object_id,incoming.logical_object_id),
            stack_ref=spell.ref,position=MergePosition.BOTTOM))
        shock=H.add(self,engine,'Shock',seat='C',zone='hand',ref='SBA_DAMAGE')
        shrink=H.add(self,engine,'Disfigure',seat='C',zone='hand',ref='SBA_ZERO_TOUGHNESS')
        self.assert_closed(session,shock);self.assert_closed(session,shrink)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C';engine.state.started=True
        engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players['C'].mana_pool.update({'R':1,'B':1})
        engine.state.turn_history.arrival_history_version=1
        engine.state.turn_history.graveyard_type_history_version=1
        engine.state.turn_history.departure_history_version=1
        engine._grant_priority('C');engine.pump();H.checkpoint(self,session)
        return session,root,incoming,shock,shrink

    def cast(self,session,source,target):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        actions=session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in actions if row.get('card')==source.ref)
        result=session.act('pilot:C',{'action_id':offer['id'],'targets':[target.ref],'pay':'auto'})
        self.assertTrue(result.ok,result.summary)
        return H.resolve(self,session)

    def complete_departure(self,session,root,incoming,decision):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        self.assertIsNotNone(decision)
        self.assertEqual('permanent.composition_order',decision.kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        engine=session.engine
        self.assertEqual('battlefield',engine.state.cards[root.object_id].zone)
        self.assertNotIn(incoming.object_id,engine.state.cards)
        self.assertEqual([],engine.state.players['A'].zones['graveyard'])
        for seat in 'BCD':
            public=session.packet('pilot:'+seat,full=True)
            self.assertNotIn(incoming.ref,str(public.get('decision')))
            self.assertNotIn('permanent_composition',str(public))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        for principal,order in (('pilot:B',[incoming.ref,root.ref]),('pilot:C',[incoming.ref,root.ref]),
                                ('pilot:A',[root.ref,root.ref])):
            rejected=session.act(principal,{'action_id':'order','cards':order})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'order','cards':[incoming.ref,root.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual([incoming.object_id,root.object_id],engine.state.players['A'].zones['graveyard'])
        self.assertEqual(1,sum(event.kind=='creature_died' for event in engine.state.turn_history.events))
        self.assertEqual('priority',engine.state.pending_decision.kind)
        self.assertNotEqual('A',engine.state.priority_player)
        H.replay(self,session,load=True)

    def test_actual_shock_preserves_owner_order_before_composed_state_based_death(self):
        session,root,incoming,shock,_shrink=self.fixture('Ornithopter',959300)
        decision=self.cast(session,shock,root)
        self.complete_departure(session,root,incoming,decision)

    def test_physical_departure_rolls_back_a_fault_after_the_first_card_arrives(self):
        from unittest.mock import patch
        from quorune.errors import GameRuleError
        session,root,incoming,shock,_shrink=self.fixture('Ornithopter',959303)
        self.cast(session,shock,root);engine=session.engine
        before=authoritative_state_hash(engine.state);committed=[]
        original=ZoneTransitionOwner._commit_move
        def fault(owner,plan,*args,**kwargs):
            result=original(owner,plan,*args,**kwargs)
            committed.append(plan.card.object_id)
            raise GameRuleError('Injected fault after physical arrival')
        with patch.object(ZoneTransitionOwner,'_commit_move',fault):
            rejected=session.act('pilot:A',{'action_id':'order','cards':[incoming.ref,root.ref]})
        self.assertFalse(rejected.ok);self.assertEqual(1,len(committed))
        self.assertIn('Injected fault',rejected.summary)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        self.complete_departure(session,root,incoming,engine.state.pending_decision)

    def test_indestructible_rejects_lethal_destruction_but_zero_toughness_still_departs(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,incoming,shock,shrink=self.fixture('Darksteel Myr',959301)
        self.assertIsNone(self.cast(session,shock,root))
        self.assertEqual('battlefield',session.engine.state.cards[root.object_id].zone)
        self.assertEqual(2,session.engine.state.cards[root.object_id].marked_damage)
        session=H.replay(self,session,load=True)
        decision=self.cast(session,shrink,session.engine.state.cards[root.object_id])
        self.complete_departure(session,root,incoming,decision)

    def test_actual_pyroclasm_holds_all_deaths_and_interleaves_physical_cards_with_a_dying_observer(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,part,_shock,_shrink=self.fixture('Ornithopter',959302);engine=session.engine
        other=H.add(self,engine,'Ornithopter',seat='D',ref='SBA_SECOND_ROOT')
        other_part=H.add(self,engine,'Ornithopter',seat='D',zone='hand',ref='SBA_SECOND_PART')
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='D';engine._grant_priority('D');engine.pump()
        actions=session.packet('pilot:D',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==other_part.ref)
        self.assertTrue(session.act('pilot:D',{'action_id':offered['id'],'pay':'auto'}).ok)
        spell=next(item for item in engine.state.stack if item.card_object_id==other_part.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=ContinuousObjectIdentity(other.object_id,other.logical_object_id),
            source=ContinuousObjectIdentity(other_part.object_id,other_part.logical_object_id),
            stack_ref=spell.ref,position=MergePosition.BOTTOM))
        ordinary=H.add(self,engine,'Ornithopter',ref='SBA_ORDINARY_COMPANION')
        observer=H.add(self,engine,'Midnight Reaper',seat='B',ref='SBA_DYING_OBSERVER')
        spell=H.add(self,engine,'Pyroclasm',seat='C',zone='hand',ref='SBA_SIMULTANEOUS_DAMAGE')
        for card in (other,other_part,ordinary,observer,spell):self.assert_closed(session,card)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player='C'
        engine.state.players['C'].mana_pool.update({key:0 for key in engine.state.players['C'].mana_pool})
        engine.state.players['C'].mana_pool.update({'R':1,'C':1})
        engine._grant_priority('C');engine.pump();H.checkpoint(self,session)
        life_before=engine.state.players['B'].life
        hand_before=len(engine.state.players['B'].zones['hand'])
        actions=session.packet('pilot:C',full=True)['decision']['ctx']['legal']['actions']
        offered=next(row for row in actions if row.get('card')==spell.ref)
        accepted=session.act('pilot:C',{'action_id':offered['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=H.resolve(self,session)
        self.assertEqual('permanent.composition_order',decision.kind)
        self.assertEqual(['pilot:D'],list(session.pending_principals()))
        for card in (root,other,ordinary,observer):self.assertEqual('battlefield',engine.state.cards[card.object_id].zone)
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'order','cards':[other.ref,other_part.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        session=H.replay(self,session,load=True);engine=session.engine
        accepted=session.act('pilot:D',{'action_id':'order','cards':[other_part.ref,other.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        for card in (root,other,ordinary,observer):self.assertEqual('battlefield',engine.state.cards[card.object_id].zone)
        self.assertTrue(all(not engine.state.players[seat].zones['graveyard'] for seat in 'ABD'))
        schema=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']
        self.assertEqual({part.ref,ordinary.ref,root.ref},set(schema['legal_refs']))
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        self.assertFalse(session.act('pilot:A',{'action_id':'order','cards':[root.ref,root.ref,ordinary.ref]}).ok)
        self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'order','cards':[part.ref,ordinary.ref,root.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        triggers=H.resolve(self,session)
        self.assertEqual('trigger.order',triggers.kind);self.assertEqual(['B'],triggers.actors)
        self.assertEqual(2,len(triggers.payload_by_actor['B']['triggers']))
        session=H.replay(self,session,load=True);engine=session.engine
        refs=[item.ref for item in engine.state.pending_trigger_batches[0].items]
        accepted=session.act('pilot:B',{'action_id':'order','triggers':list(reversed(refs))})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual([part.object_id,ordinary.object_id,root.object_id],engine.state.players['A'].zones['graveyard'])
        self.assertEqual([other_part.object_id,other.object_id],engine.state.players['D'].zones['graveyard'])
        self.assertEqual([observer.object_id],engine.state.players['B'].zones['graveyard'])
        self.assertEqual(4,sum(event.kind=='creature_died' for event in engine.state.turn_history.events))
        self.assertEqual(life_before-2,engine.state.players['B'].life)
        self.assertEqual(hand_before+2,len(engine.state.players['B'].zones['hand']))
        private_refs=[engine.state.cards[oid].ref for oid in engine.state.players['B'].zones['hand']]
        for seat in 'ACD':
            packet=str(session.packet('pilot:'+seat,full=True)['state'])
            for ref in private_refs:self.assertNotIn(ref,packet)
        H.replay(self,session,load=True)
