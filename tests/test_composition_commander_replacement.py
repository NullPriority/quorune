"""Commander hand/library replacement selects physical cards before movement."""
from pathlib import Path
import tempfile
import unittest

from common import ROOT
import test_composition_commander_departure as departures
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.permanent_composition import MergePosition
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class CommanderComponentReplacementModelTests(unittest.TestCase):
    def component(self):
        from quorune.composition_commander_replacement_model import CommanderZoneComponent
        return CommanderZoneComponent('commander-card','commander-card@1','CMD',
            'commander:A:1','A','Public commander')

    def event(self):
        from quorune.replacement_effects import ReplaceableEvent,AffectedObject
        return ReplaceableEvent('physical-command:event','zone.change',None,affected_object=AffectedObject('root','A','A'),
            payload={'object_ref':'ROOT','logical_object_id':'root@1','origin':'battlefield','destination':'hand',
                'commander_components':[self.component().to_dict()],'commander_component_returns':[],
                'commander_destination_epoch':0})

    def test_component_codec_and_operation_reject_unknown_duplicate_or_nondesignated_returns(self):
        from quorune.composition_commander_replacement_model import (CommanderZoneComponent,
            payload_commander_components,validate_commander_component_returns,commander_component_destinations)
        from quorune.replacement_effects import ReplacementEffect,ReplacementClass
        from quorune.replacement.applicability import replacement_choice
        from quorune.replacement.application import apply_replacement
        component=self.component();wire=component.to_dict()
        self.assertEqual(component,CommanderZoneComponent.from_dict(wire))
        for bad in ({**wire,'unknown':1},{**wire,'designation_id':True},{**wire,'logical_object_id':'other@1'}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):CommanderZoneComponent.from_dict(bad)
        event=self.event();self.assertEqual((component,),payload_commander_components(event.payload))
        for ids in (['unknown'],['commander:A:1','commander:A:1'],[False]):
            with self.subTest(ids=ids),self.assertRaises(ValueError):validate_commander_component_returns(event.payload,ids)
        effect=ReplacementEffect('return-known','rule:903.9b:commander:A:1','zone.change',ReplacementClass.OTHER,
            operations=({'op':'union','field':'commander_component_returns','values':['commander:A:1']},))
        result=apply_replacement(replacement_choice(event,(effect,)),(effect,),effect.effect_id)
        self.assertEqual({'commander-card':'command'},commander_component_destinations(result.payload))
        self.assertEqual('hand',result.payload['destination'])

    def test_commander_rule_can_apply_again_after_later_destination_replacements(self):
        from types import SimpleNamespace
        from quorune.commander_zones import commander_hand_library_replacement_effects
        from quorune.replacement_effects import ReplacementEffect,ReplacementClass
        from quorune.replacement.applicability import replacement_choice
        from quorune.replacement.application import apply_replacement
        subject=SimpleNamespace(commander_components=(self.component(),),object_ref='ROOT',logical_object_id='root@1')
        commanders=commander_hand_library_replacement_effects(subject,maximum_destination_changes=2)
        redirects=(ReplacementEffect('to-library','model:library','zone.change',ReplacementClass.OTHER,
            conditions={'destination':{'eq':'hand'}},operations=({'op':'set','field':'destination','value':'library'},)),
            ReplacementEffect('back-to-hand','model:hand','zone.change',ReplacementClass.OTHER,
            conditions={'destination':{'eq':'library'}},operations=({'op':'set','field':'destination','value':'hand'},)))
        effects=(*commanders,*redirects);event=self.event()
        for epoch,redirect in ((0,'to-library'),(1,'back-to-hand')):
            command=next(effect for effect in commanders if effect.effect_id.endswith(':epoch:'+str(epoch)))
            choice=replacement_choice(event,effects);self.assertIn(command.effect_id,choice.optional_options)
            event=apply_replacement(choice,effects,'decline:'+command.effect_id)
            event=apply_replacement(replacement_choice(event,effects),effects,redirect)
        self.assertEqual(2,event.payload['commander_destination_epoch'])
        last=next(effect for effect in commanders if effect.effect_id.endswith(':epoch:2'))
        event=apply_replacement(replacement_choice(event,effects),effects,last.effect_id)
        self.assertEqual(('commander:A:1',),tuple(event.payload['commander_component_returns']))
        self.assertIsNone(replacement_choice(event,effects))


class CompositionCommanderReplacementTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    assert_closed=departures.CompositionCommanderDepartureTests.assert_closed
    priority=departures.CompositionCommanderDepartureTests.priority
    cast=departures.CompositionCommanderDepartureTests.cast
    fixture=departures.CompositionCommanderDepartureTests.fixture

    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'physical-commander-replacement.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/activation-view-equivalence-cards.json',
            ROOT/'tests/fixtures/announced-x-activation-cards.json',
            ROOT/'tests/fixtures/composition-commander-cards.json',
            ROOT/'tests/fixtures/composition-commander-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Physical commander replacement',[
            DeckEntry('Isamaru, Hound of Konda',1,'commander'),DeckEntry('Generic Bound Plains',30)],
            ['Isamaru, Hound of Konda'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()

    def replacement_fixture(self,name,seed):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,commander,designation,_control,_shock,_exile=self.fixture(MergePosition.BOTTOM,seed)
        spell=H.add(self,session.engine,name,seat='C',zone='hand',ref='PHYSICAL_COMMANDER_RETURN')
        self.assert_closed(session,spell)
        self.priority(session,'C',{'U':1,'C':3});H.checkpoint(self,session)
        self.cast(session,'C',spell,targets=[root.ref]);decision=H.resolve(self,session)
        self.assertEqual('replacement.order',decision.kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual('battlefield',session.engine.state.cards[root.object_id].zone)
        self.assertNotIn(commander.object_id,session.engine.state.cards)
        return session,root,commander,designation

    def select(self,session,*,decline):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session=H.replay(self,session,load=True)
        options=session.packet('pilot:A',full=True)['decision']['ctx']['options']
        selected=next(row for row in options if bool(row.get('decline')) is decline)
        before=authoritative_state_hash(session.state)
        for principal in ('pilot:B','pilot:C'):
            self.assertFalse(session.act(principal,{'action_id':'choose','replacement':selected['id']}).ok)
            self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':'choose','replacement':selected['id']})
        self.assertTrue(accepted.ok,accepted.summary)
        return session

    def test_actual_unsummon_returns_only_physical_commander_to_command(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,commander,designation=self.replacement_fixture('Unsummon',959360)
        session=self.select(session,decline=False)
        self.assertIsNone(H.resolve(self,session));engine=session.engine
        self.assertEqual('command',engine.state.cards[commander.object_id].zone)
        self.assertEqual('hand',engine.state.cards[root.object_id].zone)
        self.assertEqual(designation,engine.state.cards[commander.object_id].commander_designation_id)
        self.assertFalse(engine.state.cards[root.object_id].is_commander)
        self.assertEqual(1,engine.state.players['A'].commander_casts[commander.oracle_id])
        self.assertEqual('priority',engine.state.pending_decision.kind)
        H.replay(self,session,load=True)

    def test_actual_griptide_decline_orders_all_physical_cards_into_library(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        session,root,commander,designation=self.replacement_fixture('Griptide',959361)
        session=self.select(session,decline=True)
        decision=H.resolve(self,session)
        self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual(['pilot:A'],list(session.pending_principals()))
        self.assertEqual('battlefield',session.engine.state.cards[root.object_id].zone)
        self.assertNotIn(commander.object_id,session.engine.state.cards)
        for seat in 'BCD':self.assertNotIn(root.ref,str(session.packet('pilot:'+seat,full=True).get('decision')))
        session=H.replay(self,session,load=True)
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[root.ref,commander.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session));engine=session.engine
        self.assertEqual([root.object_id,commander.object_id],engine.state.players['A'].zones['library'][-2:])
        self.assertEqual(designation,engine.state.cards[commander.object_id].commander_designation_id)
        self.assertEqual('priority',engine.state.pending_decision.kind)
        H.replay(self,session,load=True)

    def test_actual_griptide_accept_orders_remaining_parts_and_keeps_commander_separate(self):
        from test_bound_effect_programs import BoundEffectProgramRuntimeTests as H
        from quorune.continuous_effect_model import ContinuousObjectIdentity
        from quorune.rules.composition_merge import prepare_composition_merge
        from quorune.zone_transitions import ZoneTransitionOwner
        session,root,commander,designation,_control,_shock,_exile=self.fixture(MergePosition.BOTTOM,959362)
        engine=session.engine
        extra=H.add(self,engine,'Ornithopter',zone='hand',ref='COMMANDER_OTHER_PART')
        self.priority(session,'A',{});self.cast(session,'A',extra)
        spell=next(item for item in engine.state.stack if item.card_object_id==extra.object_id)
        ZoneTransitionOwner(engine).commit_composition_merge(prepare_composition_merge(engine,
            target=ContinuousObjectIdentity(root.object_id,root.logical_object_id),
            source=ContinuousObjectIdentity(extra.object_id,extra.logical_object_id),
            stack_ref=spell.ref,position=MergePosition.BOTTOM))
        griptide=H.add(self,engine,'Griptide',seat='C',zone='hand',ref='COMMANDER_LIBRARY_RETURN')
        self.assert_closed(session,griptide)
        self.priority(session,'C',{'U':1,'C':3});H.checkpoint(self,session)
        self.cast(session,'C',griptide,targets=[root.ref])
        self.assertEqual('replacement.order',H.resolve(self,session).kind)
        session=self.select(session,decline=False)
        decision=H.resolve(self,session);self.assertEqual('semantic.choice',decision.kind)
        self.assertEqual('battlefield',session.engine.state.cards[root.object_id].zone)
        self.assertNotIn(commander.object_id,session.engine.state.cards)
        self.assertNotIn(extra.object_id,session.engine.state.cards)
        refs=session.packet('pilot:A',full=True)['decision']['ctx']['legal_actions'][0]['choice_schema']['legal_refs']
        self.assertEqual({root.ref,extra.ref},set(refs));self.assertNotIn(commander.ref,refs)
        session=H.replay(self,session,load=True);engine=session.engine
        before=authoritative_state_hash(engine.state)
        for order in ([root.ref,commander.ref],[root.ref,root.ref]):
            self.assertFalse(session.act('pilot:A',{'action_id':'choose','cards':order}).ok)
            self.assertEqual(before,authoritative_state_hash(engine.state))
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[extra.ref,root.ref]})
        self.assertTrue(accepted.ok,accepted.summary);self.assertIsNone(H.resolve(self,session))
        self.assertEqual([extra.object_id,root.object_id],engine.state.players['A'].zones['library'][-2:])
        self.assertEqual('command',engine.state.cards[commander.object_id].zone)
        self.assertEqual(designation,engine.state.cards[commander.object_id].commander_designation_id)
        H.replay(self,session,load=True)
