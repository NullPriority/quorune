from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_self_entry_counter_templates import dynamic_self_entry_counter_handler
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_counter_model import DynamicEntryCounterAmountSpec, DynamicEntryCounterCalculation, DynamicEntryCounterValueSource, EntryCounterError
from quorune.entry_counters import dynamic_entry_counter_amount
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class HistoryEntryCounterCompilerTests(unittest.TestCase):
    def test_history_entry_grammar_has_exact_value_sources_and_spans(self):
        registry=load_default_capability_registry()
        for text,expected in (
            ('This creature enters with two +1/+1 counters on it if a permanent left the battlefield under your control this turn.','controller_permanents_left'),
            ('This creature enters with X +1/+1 counters on it, where X is the amount of life you\'ve gained this turn.','controller_life_gained'),
            ('This creature enters with a +1/+1 counter on it for each creature that died under your control this turn.','controller_creatures_died'),
            ('This creature enters with a number of +1/+1 counters on it equal to the number of creatures that died this turn.','creatures_died'),
        ):
            with self.subTest(text=text):
                parsed=dynamic_self_entry_counter_handler(text,source_name='History source')
                self.assertIsNotNone(parsed)
                self.assertEqual(expected,parsed[1]['amount_spec']['value_source'])
                ir=compile_oracle_card(query_record(text),capability_registry=registry)
                self.assertEqual('exact',ir.status,ir.material_residuals)
                node=ir.faces[0].nodes[0];self.assertEqual((0,len(text)),(node.span.start,node.span.end))

    def test_adjacent_unknown_history_and_open_quantities_remain_residual(self):
        for text in (
            'This creature enters with two +1/+1 counters on it if a permanent left the battlefield last turn.',
            'This creature enters with X +1/+1 counters on it, where X is your current life total.',
            'This creature enters with a +1/+1 counter on it for each creature that died during your last turn.',
        ):
            self.assertIsNone(dynamic_self_entry_counter_handler(text,source_name='History source'))
        for source,calculation,minimum in (
            (DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT,DynamicEntryCounterCalculation.MULTIPLY,None),
            (DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT,DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,2),
            (DynamicEntryCounterValueSource.CONTROLLER_LIFE_GAINED,DynamicEntryCounterCalculation.FIXED_IF_BELOW,1),
        ):
            with self.assertRaises(EntryCounterError):
                DynamicEntryCounterAmountSpec(source,calculation,minimum=minimum)


class HistoryEntryCounterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'history-entry.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/history-entry-counters.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('History entry',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_revolt_cast_uses_prior_departure_controller_and_replays(self):
        for controller,expected in (('A',2),('B',0)):
            session=self.session(299001 if controller=='A' else 299002);engine=session.engine
            victim=self.add(engine,'Generic Bound Body',seat=controller)
            bounce=self.add(engine,'Unsummon',zone='hand')
            entrant=self.add(engine,'Greenwheel Liberator',zone='hand')
            action=self.ready(session,bounce,{'U':1,'G':1,'C':1});self.checkpoint(session)
            accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            self.assertEqual('hand',victim.zone)
            actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(a for a in actions if a.get('card')==entrant.ref)
            before=authoritative_state_hash(session.state)
            rejected=session.act('pilot:C',{'action_id':action['id'],'pay':'auto'})
            self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
            self.assertEqual(expected,entrant.counters.get('+1/+1',0))
            self.replay(session,load=True)

    def test_actual_wurm_entry_counts_life_gained_independently_of_later_payment(self):
        from quorune.life_state import pay_life_cost
        session=self.session(299003);engine=session.engine
        gain=self.add(engine,"Chaplain's Blessing",zone='hand')
        entrant=self.add(engine,'Voracious Wurm',zone='hand')
        action=self.ready(session,gain,{'W':1,'G':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.replay(session,load=True)
        pay_life_cost(engine,'A',7)
        self.assertEqual(38,engine.state.players['A'].life)
        action=self.ready(session,entrant,{'G':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(5,entrant.counters.get('+1/+1'))
        self.replay(session,load=True)

    def test_new_history_amounts_reject_missing_or_stale_journal_before_entry(self):
        session=self.session(299004);engine=session.engine
        entrant=self.add(engine,'Greenwheel Liberator',zone='hand')
        for value in (DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT,
                      DynamicEntryCounterValueSource.CONTROLLER_LIFE_GAINED,
                      DynamicEntryCounterValueSource.CONTROLLER_CREATURES_DIED):
            spec=DynamicEntryCounterAmountSpec(value,
                DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST if value is DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT else DynamicEntryCounterCalculation.MULTIPLY,
                minimum=1 if value is DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT else None)
            original=engine.state.turn_history
            try:
                for history in (None,replace(original,turn_sequence=engine.state.turn_sequence+1)):
                    engine.state.turn_history=history
                    before=authoritative_state_hash(session.state)
                    with self.assertRaises(EntryCounterError):
                        dynamic_entry_counter_amount(engine,card=entrant,destination_controller='A',amount_spec=spec)
                    self.assertEqual(before,authoritative_state_hash(session.state))
            finally:engine.state.turn_history=original

    def test_controller_death_amount_uses_committed_death_not_replaced_departure(self):
        session=self.session(299005);engine=session.engine
        entrant=self.add(engine,'Greenwheel Liberator',zone='hand')
        own=self.add(engine,'Generic Bound Body')
        foreign=self.add(engine,'Generic Bound Body',seat='B',ref='foreign-dying-body')
        engine.move_card(own.object_id,'graveyard',log=False,semantic_events=True)
        engine.move_card(foreign.object_id,'graveyard',log=False,semantic_events=True)
        spec=DynamicEntryCounterAmountSpec(DynamicEntryCounterValueSource.CONTROLLER_CREATURES_DIED,
            DynamicEntryCounterCalculation.MULTIPLY)
        self.assertEqual(1,dynamic_entry_counter_amount(engine,card=entrant,destination_controller='A',amount_spec=spec))
        self.assertEqual(1,dynamic_entry_counter_amount(engine,card=entrant,destination_controller='B',amount_spec=spec))
        exiled=self.add(engine,'Generic Bound Body',ref='exiled-not-dead')
        engine.move_card(exiled.object_id,'exile',log=False,semantic_events=True)
        self.assertEqual(1,dynamic_entry_counter_amount(engine,card=entrant,destination_controller='A',amount_spec=spec))
        departed=DynamicEntryCounterAmountSpec(DynamicEntryCounterValueSource.CONTROLLER_PERMANENTS_LEFT,
            DynamicEntryCounterCalculation.FIXED_IF_AT_LEAST,coefficient=2,minimum=1)
        self.assertEqual(2,dynamic_entry_counter_amount(engine,card=entrant,destination_controller='A',amount_spec=departed))

    def test_missing_departure_history_mutant_is_killed_by_actual_revolt_cast(self):
        import quorune.entry_counters as owner
        original=owner.current_turn_history_events
        def omitted(history,*,turn_sequence,kind):
            return () if kind=='permanent_left' else original(history,turn_sequence=turn_sequence,kind=kind)
        with patch.object(owner,'current_turn_history_events',side_effect=omitted):
            with self.assertRaises(AssertionError):
                self.test_actual_revolt_cast_uses_prior_departure_controller_and_replays()

    def test_actual_history_entry_quantity_replacement_adds_once_and_replays(self):
        session=self.session(299006);engine=session.engine
        self.add(engine,'Hardened Scales')
        victim=self.add(engine,'Generic Bound Body')
        bounce=self.add(engine,'Unsummon',zone='hand')
        entrant=self.add(engine,'Greenwheel Liberator',zone='hand')
        action=self.ready(session,bounce,{'U':1,'G':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==entrant.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual('battlefield',entrant.zone)
        self.assertEqual(3,entrant.counters.get('+1/+1'))
        self.replay(session,load=True)

    def test_history_entry_competing_replacements_pending_save_load_preserves_frozen_amount(self):
        from quorune.session import CommanderSession
        session=self.session(299007);engine=session.engine
        self.add(engine,'Hardened Scales');self.add(engine,'Branching Evolution')
        victim=self.add(engine,'Generic Bound Body')
        bounce=self.add(engine,'Unsummon',zone='hand')
        entrant=self.add(engine,'Greenwheel Liberator',zone='hand')
        action=self.ready(session,bounce,{'U':1,'G':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[victim.ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==entrant.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);decision=self.resolve(session)
        self.assertEqual('replacement.order',decision.kind)
        for seat in 'BCD':self.assertIsNone(session.packet('pilot:'+seat,full=True)['decision'])
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'pending-entry'
            session.save(path);resumed=CommanderSession.load(self.db,path)
            self.assertEqual(authoritative_state_hash(session.state),authoritative_state_hash(resumed.state))
            first_id=None
            while resumed.state.pending_decision is not None and resumed.state.pending_decision.kind=='replacement.order':
                packet=resumed.packet('pilot:A',full=True)['decision']
                option=packet['ctx']['options'][0]['id']
                if first_id is None:first_id=option
                before=authoritative_state_hash(resumed.state)
                rejected=resumed.act('pilot:B',{'action_id':'choose','replacement':option})
                self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(resumed.state))
                accepted=resumed.act('pilot:A',{'action_id':'choose','replacement':option})
                self.assertTrue(accepted.ok,accepted.summary)
            self.resolve(resumed)
            card=resumed.state.cards[entrant.object_id]
            expected=6 if 'hardened' in first_id.casefold() else 5
            self.assertEqual('battlefield',card.zone)
            self.assertEqual(expected,card.counters.get('+1/+1'))
            self.replay(resumed,load=True)


if __name__=='__main__':unittest.main()
