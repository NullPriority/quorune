"""Physical commander identity survives composed public-zone departures."""
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


class CompositionCommanderDepartureTests(unittest.TestCase):
    register_fixture_cards_on_add=True

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'composition-commanders.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',
            ROOT/'tests/fixtures/composition-commander-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Physical commander departure',[
            DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],
            ['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def assert_closed(self,session,card):
        program=compile_best_available_card_program(self.db,self.db.by_oracle_id(card.oracle_id),
            semantic_registry=session.engine.semantics,capability_registry=self.registry,capability_profile='commander_review')
        binding=bind_card_program_runtime(program,capability_registry=self.registry,profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
        self.assertEqual('capability_closed',binding['trust_basis'])

    def priority(self,session,seat,mana):
        engine=session.engine
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.active_player=seat;engine.state.started=True
        engine.state.phase='precombat_main';engine.state.step='main'
        engine.state.players[seat].mana_pool.update({key:0 for key in engine.state.players[seat].mana_pool})
        engine.state.players[seat].mana_pool.update(mana)
        engine._grant_priority(seat);engine.pump()

    def cast(self,session,seat,card,*,targets=()):
        actions=session.packet('pilot:'+seat,full=True)['decision']['ctx']['legal']['actions']
        offer=next(row for row in actions if row.get('card')==card.ref)
        command={'action_id':offer['id'],'pay':'auto'}
        if targets:command['targets']=list(targets)
        result=session.act('pilot:'+seat,command)
        self.assertTrue(result.ok,result.summary)

    def fixture(self,position,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.session(self,seed);engine=session.engine
        commander=next(card for card in engine.state.cards.values() if card.owner=='A' and card.is_commander)
        self.assert_closed(session,commander)
        root=H.add(self,engine,'Ornithopter',ref='COMMANDER_CARRIER')
        self.assert_closed(session,root)
        self.priority(session,'A',{'W':1});self.cast(session,'A',commander)
        self.assertEqual(1,engine.state.players['A'].commander_casts[commander.oracle_id])
        reference=next(item.ref for item in engine.state.stack if item.card_object_id==commander.object_id)
        designation=commander.commander_designation_id
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=ContinuousObjectIdentity(root.object_id,root.logical_object_id),
            source=ContinuousObjectIdentity(commander.object_id,commander.logical_object_id),
            stack_ref=reference,position=position))
        self.assertTrue(root.is_commander);self.assertIsNone(root.commander_designation_id)
        self.assertNotIn(commander.object_id,engine.state.cards)
        control=H.add(self,engine,'Control Magic',seat='B',zone='hand',ref='COMMANDER_CONTROL')
        self.assert_closed(session,control)
        self.priority(session,'B',{'C':2,'U':2});self.cast(session,'B',control,targets=[root.ref])
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual(('A','B'),(root.owner,root.controller))
        shock=H.add(self,engine,'Shock',seat='C',zone='hand',ref='COMMANDER_SHOCK')
        exile=H.add(self,engine,'Scour from Existence',seat='C',zone='hand',ref='COMMANDER_EXILE')
        for card in (shock,exile):self.assert_closed(session,card)
        self.priority(session,'C',{'R':1,'C':7})
        engine.state.turn_history.departure_history_version=1
        engine.state.turn_history.arrival_history_version=1
        H.checkpoint(self,session)
        return session,root,commander,designation,control,shock,exile

    def order_and_commander_choice(self,session,root,commander,decision,*,zone,ordering_actor,action):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        self.assertEqual('permanent.composition_order' if action=='order' else 'semantic.choice',decision.kind)
        self.assertEqual(['pilot:'+ordering_actor],list(session.pending_principals()))
        self.assertEqual('battlefield',session.engine.state.cards[root.object_id].zone)
        self.assertNotIn(commander.object_id,session.engine.state.cards)
        session=H.replay(self,session,load=True);engine=session.engine
        accepted=session.act('pilot:'+ordering_actor,{'action_id':action,'cards':[root.ref,commander.ref]})
        self.assertTrue(accepted.ok,accepted.summary)
        decision=H.resolve(self,session)
        self.assertEqual('state.commander_zone',decision.kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual(zone,engine.state.cards[root.object_id].zone)
        self.assertEqual(zone,engine.state.cards[commander.object_id].zone)
        self.assertFalse(engine.state.cards[root.object_id].is_commander)
        self.assertTrue(engine.state.cards[commander.object_id].is_commander)
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        for principal in ('pilot:B','pilot:C'):
            self.assertFalse(session.act(principal,{'action_id':'command','choice':'command'}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
        return session

    def test_actual_buried_commander_death_returns_only_its_card_and_preserves_real_tax(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,commander,designation,_control,shock,_exile=self.fixture(MergePosition.BOTTOM,959320)
        self.cast(session,'C',shock,targets=[root.ref]);decision=H.resolve(self,session)
        session=self.order_and_commander_choice(session,root,commander,decision,zone='graveyard',ordering_actor='A',action='order')
        accepted=session.act('pilot:A',{'action_id':'command','choice':'command'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session));engine=session.engine
        self.assertEqual('command',engine.state.cards[commander.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[root.object_id].zone)
        self.assertEqual(designation,engine.state.cards[commander.object_id].commander_designation_id)
        self.assertEqual(1,engine.state.players['A'].commander_casts[commander.oracle_id])
        session=H.replay(self,session,load=True);engine=session.engine
        self.priority(session,'A',{'W':1,'C':2});H.checkpoint(self,session)
        self.cast(session,'A',engine.state.cards[commander.object_id])
        self.assertEqual(0,sum(engine.state.players['A'].mana_pool.values()))
        self.assertEqual(2,engine.state.players['A'].commander_casts[commander.oracle_id])
        self.assertIsNone(H.resolve(self,session))
        self.assertEqual('battlefield',engine.state.cards[commander.object_id].zone)
        self.assertEqual('graveyard',engine.state.cards[root.object_id].zone)
        H.replay(self,session,load=True)

    def test_actual_top_commander_exile_uses_exiler_order_then_owner_may_decline(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,commander,designation,_control,_shock,exile=self.fixture(MergePosition.TOP,959321)
        self.cast(session,'C',exile,targets=[root.ref]);decision=H.resolve(self,session)
        session=self.order_and_commander_choice(session,root,commander,decision,zone='exile',ordering_actor='C',action='choose')
        accepted=session.act('pilot:A',{'action_id':'remain','choice':'remain'})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session));engine=session.engine
        physical=engine.state.cards[commander.object_id]
        self.assertEqual('exile',physical.zone);self.assertEqual(designation,physical.commander_designation_id)
        self.assertEqual(physical.logical_object_id,physical.commander_zone_choice_logical_id)
        self.assertEqual('exile',engine.state.cards[root.object_id].zone)
        self.assertFalse(engine._stabilize());self.assertEqual('priority',engine.state.pending_decision.kind)
        H.replay(self,session,load=True)

    def test_hand_library_commander_kernel_requires_selection_before_physical_movement(self):
        from quorune.replacement_effects import ReplacementChoiceRequired
        session,root,commander,_designation,_control,_shock,_exile=self.fixture(MergePosition.BOTTOM,959322)
        engine=session.engine;before=authoritative_state_hash(engine.state)
        for zone in ('hand','library'):
            with self.subTest(zone=zone),self.assertRaises(ReplacementChoiceRequired) as required:
                ZoneTransitionOwner(engine).depart_composition(root.object_id,zone,
                    physical_order=[root.object_id,commander.object_id],chooser='A',actor='C',
                    reason='Unrepresented per-component commander replacement boundary')
            self.assertEqual('A',required.exception.pending.choice.chooser)
            self.assertEqual(before,authoritative_state_hash(engine.state))
