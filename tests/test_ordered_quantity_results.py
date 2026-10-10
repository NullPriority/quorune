from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class OrderedQuantityCompilerTests(unittest.TestCase):
    def test_independent_query_results_share_ordered_program_owner(self):
        for text in ('Draw a card, then you gain life equal to the number of cards in your hand.',
                     'Put a lore counter on this enchantment, then draw a card for each lore counter on this enchantment.',
                     'Put a +1/+1 counter on each creature you control. You gain 1 life for each creature you control.'):
            record=replace(query_record(text),type_line='Sorcery') if text.startswith(('Draw','Put a +1/+1')) else query_record('At the beginning of your upkeep, '+text)
            ir=compile_oracle_card(record,capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual(2,len(node.effects))
            self.assertEqual((0,len(record.oracle_text)),(node.span.start,node.span.end))
            self.assertIn('resolution.effect_program.closed_components',node.capability_dependencies)

    def test_conditional_linked_and_ambiguous_sequences_remain_residual(self):
        for text in ('Draw a card, then you gain life equal to the number of cards drawn this way.',
                     'Put a lore counter on this enchantment, then you may draw cards equal to the number of lore counters on it.',
                     'Put a +1/+1 counter on target creature, then draw cards equal to the number of counters on it.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_mixed_scalar_and_public_quantities_project_each_result_independently(self):
        from quorune.compiler.public_query_effect_amounts import public_query_amount_shape_context
        from quorune.scalar_effect_amount_model import ScalarEffectAmountSpec,ScalarAmountOrigin
        from quorune.query_effect_amount_model import PublicQueryAmountSpec
        from quorune.characteristic_fragments import CharacteristicQuantitySpec,CharacteristicQuantityScope
        from quorune.object_predicate import ObjectQuerySpec
        scalar=ScalarEffectAmountSpec(ScalarAmountOrigin.SOURCE,counter_name='lore',schema_version=2).to_dict()
        public=PublicQueryAmountSpec(CharacteristicQuantitySpec(scope=CharacteristicQuantityScope.CONTROLLER_ZONE,query=ObjectQuerySpec(zones=('hand',)))).to_dict()
        effects=({'op':'draw','player':'$controller','count':scalar,'private':True},{'op':'life','player':'$controller','delta':public})
        context=public_query_amount_shape_context(effects,{'closed-effect-program','scalar-effect-amount','public-query-effect-amount'})
        self.assertIsNotNone(context)
        self.assertTrue(all(type(next(e[k] for k in ('count','delta') if k in e)) is int for e in context[0]))
        self.assertNotIn('scalar-effect-amount',context[1]);self.assertNotIn('public-query-effect-amount',context[1])
        from types import SimpleNamespace
        from quorune.compiler.public_query_effect_amounts import public_query_amount_program_is_closed
        from quorune.rules.capabilities import capability_dependencies_for_node
        mechanics={'closed-effect-program','scalar-effect-amount','public-query-effect-amount','cr-121-drawing-a-card','cr-119-life'}
        required=capability_dependencies_for_node(effects=effects,target_schema=None,mechanic_ids=mechanics)
        program=SimpleNamespace(effects=effects,target_schema=None,coverage=list(mechanics),capability_dependencies=list(required))
        self.assertTrue(public_query_amount_program_is_closed(program,required_dependencies=required))
        program.capability_dependencies.remove('quantity_expression.public_query_effect_amount')
        self.assertFalse(public_query_amount_program_is_closed(program,required_dependencies=required))


class OrderedQuantityRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'sequence.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/ordered-quantity-results.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Ordered quantity',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_union_counts_hand_after_its_draw_and_replays(self):
        session=self.session(307001);engine=session.engine
        spell=self.add(engine,'Union of the Third Path',zone='hand')
        action=self.ready(session,spell,{'W':1,'C':2});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand']);before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.assertEqual(40+hand,session.state.players['A'].life)
        private=session.state.cards[session.state.players['A'].zones['hand'][-1]].ref
        for seat in 'BCD':self.assertNotIn(private,str(session.packet('pilot:'+seat,full=True)))
        self.replay(session,load=True)

    def test_actual_mind_unbound_counter_is_counted_by_later_draw(self):
        session=self.session(307002);engine=session.engine
        source=self.add(engine,'Mind Unbound');source.counters['lore']=2
        from test_scheduled_player_triggers import ScheduledPlayerTriggerRuntimeTests
        ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A')
        before=len(session.state.players['A'].zones['hand'])
        self.resolve(session)
        self.assertEqual(3,session.state.cards[source.object_id].counters['lore'])
        self.assertEqual(before+3,len(session.state.players['A'].zones['hand']))
        self.replay(session,load=True)

    def test_source_counter_quantity_early_read_mutant_is_killed(self):
        from quorune.scalar_effect_amounts import resolve_scalar_effect_amount
        session=self.session(307003);source=self.add(session.engine,'Mind Unbound');source.counters['lore']=2
        from test_scheduled_player_triggers import ScheduledPlayerTriggerRuntimeTests
        ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self,session,'A')
        before=len(session.state.players['A'].zones['hand'])
        def early(host,value,item):
            return 2 if value.get('counter_name')=='lore' else resolve_scalar_effect_amount(host,value,item)
        with patch('quorune.semantic_runtime.values.resolve_scalar_effect_amount',side_effect=early):
            self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(before+3,len(session.state.players['A'].zones['hand']))

    def test_actual_rotwidow_created_token_contributes_to_later_opponent_loss(self):
        session=self.session(307004);engine=session.engine
        source=self.add(engine,'Rotwidow Pack')
        price=self.add(engine,'Generic Bound Body',zone='graveyard')
        action=self.ready(session,source,{'B':1,'G':1,'C':3});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[price.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        spiders=[c for c in session.state.cards.values() if c.zone=='battlefield' and c.is_token and c.controller=='A']
        self.assertEqual(1,len(spiders))
        for seat in 'BCD':self.assertEqual(38,session.state.players[seat].life)
        self.assertEqual(40,session.state.players['A'].life);self.replay(session)

    def test_pending_counter_replacement_resumes_once_before_later_quantity(self):
        session=self.session(307005);engine=session.engine
        source=self.add(engine,'Generic Ordered Counter Draw')
        self.add(engine,'Hardened Scales');self.add(engine,'Branching Evolution')
        action=self.ready(session,source,{});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        decision=self.resolve(session);self.assertEqual('replacement.order',decision.kind)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        resumed=self.replay(session,load=True);first=None
        while resumed.state.pending_decision and resumed.state.pending_decision.kind=='replacement.order':
            packet=resumed.packet('pilot:A',full=True)['decision'];option=packet['ctx']['options'][0]['id']
            if first is None:first=option
            before_hash=authoritative_state_hash(resumed.state)
            rejected=resumed.act('pilot:B',{'action_id':'choose','replacement':option});self.assertFalse(rejected.ok)
            self.assertEqual(before_hash,authoritative_state_hash(resumed.state))
            accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(resumed)
        count=resumed.state.cards[source.object_id].counters['+1/+1']
        self.assertIn(count,(3,4));self.assertEqual(before+count,len(resumed.state.players['A'].zones['hand']))
        self.replay(resumed)


if __name__=='__main__':unittest.main()
