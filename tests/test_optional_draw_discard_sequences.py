from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class OptionalDrawDiscardCompilerTests(unittest.TestCase):
    def test_fixed_optional_draw_discard_compiles_original_order(self):
        text='Whenever you cast an instant or sorcery spell, you may draw a card. If you do, discard a card.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0];self.assertEqual('offer_optional_effect',node.effects[0]['op'])
        self.assertEqual(['draw','choose_cards_apnap'],[e['op'] for e in node.effects[0]['effects']])
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_linked_dynamic_random_and_other_player_variants_stay_residual(self):
        for text in ('Whenever you cast a spell, you may draw X cards. If you do, discard X cards.',
                     'Whenever you cast a spell, you may draw a card. If you do, discard a card at random.',
                     'Whenever you cast a spell, target player may draw a card. If they do, discard a card.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)
        from quorune.compiler.fixed_controller_effect_sequences import fixed_controller_effect_clause
        from quorune.rules.optional_draw_discard import optional_draw_discard_is_closed
        effects=[fixed_controller_effect_clause(text)[0] for text in ('Draw a card.','Discard a card.')]
        self.assertTrue(optional_draw_discard_is_closed(effects,player='$controller'))
        self.assertFalse(optional_draw_discard_is_closed((effects[0],{**effects[1],'players':None}),player='$controller'))
        self.assertFalse(optional_draw_discard_is_closed(({**effects[0],'count':True},effects[1]),player='$controller'))


class OptionalDrawDiscardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'optional-sequence.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/optional-draw-discard-sequences.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Optional draw discard',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def resolve_to_choice(self,session):
        for _ in range(64):
            pending=session.state.pending_decision
            if pending and pending.kind=='trigger.order':self.order_triggers(session);continue
            if pending and pending.kind!='priority':return pending
            if not session.state.stack:return None
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.fail('Optional sequence did not reach its decision')

    def test_actual_academy_decline_consumes_limit_without_draw_or_discard(self):
        session=self.session(310001);engine=session.engine
        source=self.add(engine,'Academy Wall');target=self.add(engine,'Generic Bound Body')
        spells=[self.add(engine,'Unsummon',zone='hand',ref='bounce-'+str(i)) for i in range(2)]
        action=self.ready(session,spells[0],{'U':2});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        pending=self.resolve_to_choice(session);self.assertEqual('semantic.choice',pending.kind)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        result=session.act('pilot:A',{'action_id':'choose','choice':'decline'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)
        # A second actual spell cannot trigger again after optional decline.
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spells[1].ref)
        result=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)

    def test_actual_koi_acceptance_draws_before_private_discard_and_saves_replays(self):
        session=self.session(310002);engine=session.engine
        source=self.add(engine,'Skyswimmer Koi')
        artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,artifact,{});self.checkpoint(session)
        hand_ids=set(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        result=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(result.ok,result.summary)
        pending=self.resolve_to_choice(session);self.assertEqual('choice.apnap',pending.kind)
        new_id=next(i for i in session.state.players['A'].zones['hand'] if i not in hand_ids)
        new_ref=session.state.cards[new_id].ref
        for seat in 'BCD':self.assertNotIn(new_ref,str(session.packet('pilot:'+seat,full=True)))
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':'choose','cards':[new_ref]});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        resumed=self.replay(session,load=True)
        chosen=resumed.act('pilot:A',{'action_id':'choose','cards':[new_ref]});self.assertTrue(chosen.ok,chosen.summary)
        self.assertIsNone(self.resolve_to_choice(resumed));self.assertEqual('graveyard',resumed.state.cards[new_id].zone);self.replay(resumed)

    def test_accepting_prevented_draw_still_discards_after_real_prior_draw(self):
        session=self.session(310003);engine=session.engine
        draw=self.add(engine,'Generic Optional Prior Draw',zone='hand')
        artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,draw,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve_to_choice(session));self.replay(session)
        self.add(engine,'Spirit of the Labyrinth');self.add(engine,'Skyswimmer Koi')
        action=self.ready(session,artifact,{});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        accepted=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('choice.apnap',self.resolve_to_choice(session).kind)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        chosen=session.state.cards[session.state.players['A'].zones['hand'][0]].ref
        accepted=session.act('pilot:A',{'action_id':'choose','cards':[chosen]});self.assertTrue(accepted.ok,accepted.summary)
        self.assertIsNone(self.resolve_to_choice(session))
        self.assertEqual(hand-2,len(session.state.players['A'].zones['hand']));self.replay(session)

    def test_omitted_followup_discard_mutant_is_killed_after_actual_acceptance(self):
        from quorune.semantic_choices.optional_effect import OptionalEffectHandler
        from dataclasses import replace
        original=OptionalEffectHandler.complete
        session=self.session(310004);engine=session.engine
        self.add(engine,'Skyswimmer Koi');artifact=self.add(engine,'Generic Optional Draw Artifact',zone='hand')
        action=self.ready(session,artifact,{});accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',self.resolve_to_choice(session).kind)
        def omit(self,continuation,response,query):
            result=original(self,continuation,response,query)
            return replace(result,prepend_effects=result.prepend_effects[:1])
        with patch.object(OptionalEffectHandler,'complete',omit):
            accepted=session.act('pilot:A',{'action_id':'choose','choice':'apply'});self.assertTrue(accepted.ok,accepted.summary)
            pending=self.resolve_to_choice(session)
            with self.assertRaises(AssertionError):self.assertIsNotNone(pending)


if __name__=='__main__':unittest.main()
