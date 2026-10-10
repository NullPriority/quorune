from __future__ import annotations

from pathlib import Path
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


class AggregatedZoneTriggerCompilerTests(unittest.TestCase):
    def test_aggregate_query_preserves_source_span_and_batch_coverage(self):
        for text in (
            'Whenever one or more creatures you control with mana value 3 or less enter, draw a card.',
            'Whenever one or more other Elves you control enter, draw a card.',
            'Whenever one or more other creatures you control die, put a +1/+1 counter on this creature.',
            'Whenever one or more legendary creatures you control enter, draw a card.',
        ):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertIn('one_or_more_event_batch',node.runtime_coverage)
            self.assertIn('current_ability_fragment_required',node.runtime_coverage)
            self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_exact_single_event_limit_integrates_without_widening_leaf_parser(self):
        text='Whenever this creature becomes tapped, draw a card. This ability triggers only once each turn.'
        from quorune.compiler.tap_state_event_bindings import tap_state_event_binding_spec
        binding=tap_state_event_binding_spec(text,card_name='Generic Query Observer')
        self.assertIsNotNone(binding)
        self.assertTrue(binding.body.endswith('This ability triggers only once each turn.'))
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        self.assertIsNotNone(ir.faces[0].nodes[0].trigger_limit)

    def test_open_plural_references_and_unsupported_limiting_departures_stay_residual(self):
        for text in (
            'Whenever one or more creatures enter, put a +1/+1 counter on each of them.',
            'Whenever one or more cards are discarded, draw a card.',
            'Whenever one or more other creatures die, draw a card. This ability triggers only once each turn.',
        ):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)


class AggregatedZoneTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'aggregate.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/aggregated-zone-event-triggers.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Aggregate zone',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def cast(self,session,spell):
        for _ in range(8):
            if session.pending_principals()[0]=='pilot:'+spell.owner:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        action=next(a for a in session.packet(session.pending_principals()[0],full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spell.ref)
        result=session.act(session.pending_principals()[0],{'action_id':action['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.resolve(session)

    def test_actual_token_batch_triggers_once_and_limiter_rejects_later_batch(self):
        session=self.session(304001);engine=session.engine
        source=self.add(engine,'Tocasia\'s Welcome')
        spells=[self.add(engine,'Raise the Alarm',zone='hand',ref='alarm-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{'W':4,'C':8});self.checkpoint(session)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==spells[0].ref)
        before_hash=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'pay':'auto'});self.assertFalse(rejected.ok)
        self.assertEqual(before_hash,authoritative_state_hash(session.state))
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1]);self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        tokens=[c for c in session.state.cards.values() if c.is_token and c.zone=='battlefield' and c.controller=='A']
        self.assertEqual(4,len(tokens));self.replay(session,load=True)

    def test_separate_real_token_batches_each_trigger_once_without_turn_limit(self):
        session=self.session(304002);engine=session.engine
        source=self.add(engine,'Generic Aggregate Entry Observer')
        spells=[self.add(engine,'Raise the Alarm',zone='hand',ref='alarm-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{'W':4,'C':8});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1]);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_singular_control_triggers_once_per_token_in_the_same_batch(self):
        session=self.session(304003);engine=session.engine
        source=self.add(engine,'Generic Singular Entry Observer')
        spell=self.add(engine,'Raise the Alarm',zone='hand')
        self.ready(session,spell,{'W':1,'C':1});self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spell);self.assertEqual(before+1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_nonqualifying_entry_does_not_consume_limited_aggregate(self):
        session=self.session(304006);engine=session.engine
        source=self.add(engine,'Tocasia\'s Welcome')
        big=self.add(engine,'Generic Aggregate Large Entrant',zone='hand')
        alarm=self.add(engine,'Raise the Alarm',zone='hand')
        self.ready(session,big,{'W':2,'C':8});self.checkpoint(session)
        self.cast(session,big)
        self.assertNotIn('once_per_turn_triggers',session.state.cards[source.object_id].annotations)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,alarm);self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_actual_mass_death_batch_produces_one_source_counter(self):
        session=self.session(304004);engine=session.engine
        source=self.add(engine,'Vengeful Townsfolk')
        for i in range(2):self.add(engine,'Generic Aggregate Victim',ref='victim-'+str(i))
        spell=self.add(engine,'Generic Aggregate Small Sweeper',zone='hand')
        self.ready(session,spell,{});self.checkpoint(session)
        self.cast(session,spell)
        self.assertEqual(1,session.state.cards[source.object_id].counters['+1/+1'])
        self.assertEqual(2,sum(c.zone=='graveyard' and c.ref.startswith('victim-') for c in session.state.cards.values()))
        self.replay(session,load=True)

    def test_omitted_aggregation_mutant_is_killed_by_two_token_batch(self):
        import quorune.compiler.fixed_counter_trigger_nodes as nodes
        with patch.object(nodes,'_ONE_OR_MORE_PUBLIC_EVENT_VARIANTS',nodes._ONE_OR_MORE_PUBLIC_EVENT_VARIANTS-{nodes.AGGREGATED_ZONE_VARIANT}):
            session=self.session(304005)
            source=self.add(session.engine,'Generic Aggregate Entry Observer')
            spell=self.add(session.engine,'Raise the Alarm',zone='hand')
            self.ready(session,spell,{'W':1,'C':1});before=len(session.state.players['A'].zones['hand'])
            self.cast(session,spell)
            with self.assertRaises(AssertionError):self.assertEqual(before,len(session.state.players['A'].zones['hand']))


if __name__=='__main__':unittest.main()
