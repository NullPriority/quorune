from __future__ import annotations

import json
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


class SourcePronounCompilerTests(unittest.TestCase):
    def test_exact_self_event_body_keeps_original_span_and_source_reference(self):
        for text in ('When this creature enters, it gets +2/+2 until end of turn.',
                     'When this creature dies, it deals 2 damage to each opponent and you gain 2 life.',
                     'Whenever this creature becomes tapped, you may put a +1/+1 counter on it.',
                     'Whenever this creature attacks, you may put a +1/+1 counter on it.',
                     'Whenever this creature attacks, put two +1/+1 counters on it.',
                     'Whenever this creature attacks, put a charge counter on it.'):
            ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
            self.assertEqual('exact',ir.status,ir.material_residuals)
            node=ir.faces[0].nodes[0];self.assertEqual((0,len(text)),(node.span.start,node.span.end));self.assertEqual(text,node.text)

    def test_self_action_characteristics_reuse_closed_keyword_and_scalar_leaves(self):
        registry = load_default_capability_registry()
        for text in (
            'Whenever this creature becomes blocked, it gets +3/+0 and gains trample until end of turn.',
            'Whenever this creature blocks, it gets +1/+0 and gains first strike until end of turn.',
            'Whenever this creature attacks, it gets +X/+0 until end of turn, where X is the greatest power among creatures you control.',
            'Whenever this creature attacks, it gets +X/+0 until end of turn, where X is the greatest power among tapped creatures your opponents control.',
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(query_record(text), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(text, node.text)
                self.assertEqual((0, len(text)), (node.span.start, node.span.end))
                self.assertEqual('$source.zone_object', node.effects[0]['card'])
        text = 'Whenever this creature blocks, it gets +1/+0 and gains first strike until end of turn.'
        with patch('quorune.compiler.source_self_effect_templates.normalized_source_result_body', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status)
        from quorune.rules.capabilities import CapabilityRegistry
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(row for row in raw['capabilities'] if row['id'] ==
            'continuous.resolution.fixed_source_characteristics_until_end_of_turn').update(
                status='blocked', blockers=['source characteristic authority withdrawn'])
        self.assertNotEqual('exact', compile_oracle_card(query_record(text),
            capability_registry=CapabilityRegistry(raw)).status)
        for text in (
            'Whenever a creature blocks, it gets +1/+0 and gains first strike until end of turn.',
            'Whenever this creature blocks, it gets +1/+0 and gains banding until end of turn.',
        ):
            self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=registry).status)

    def test_new_token_or_target_pronoun_is_not_rebound_to_source(self):
        from quorune.compiler.source_self_effect_templates import normalized_source_result_body
        for text in ('Create a 0/0 green Fractal creature token. Put three +1/+1 counters on it.',
                     'Target creature gets +2/+2 until end of turn. Put a counter on it.',
                     'Choose a creature. It gets +2/+2 until end of turn.'):
            self.assertIsNone(normalized_source_result_body(text))
        text='When this creature enters, create a 0/0 green Fractal creature token. Put three +1/+1 counters on it.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertFalse(any(n.exact and any(e.get('card')=='$source.zone_object' for e in n.effects) for f in ir.faces for n in f.nodes))


class SourcePronounRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'source-pronouns.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/repeatable-object-action-triggers.json',
            ROOT/'tests/fixtures/source-pronoun-results.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Source pronouns',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers

    def test_actual_scorpion_death_deals_damage_and_gains_life_for_trigger_controller(self):
        session=self.session(311001);engine=session.engine
        source=self.add(engine,'Serrated Scorpion');feeder=self.add(engine,'Carrion Feeder')
        action=self.ready(session,feeder,{});self.checkpoint(session)
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'cost_cards':[source.ref]});self.assertFalse(rejected.ok)
        self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'cost_cards':[source.ref]});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(42,session.state.players['A'].life)
        for seat in 'BCD':self.assertEqual(38,session.state.players[seat].life)
        self.replay(session,load=True)

    def test_actual_firstblade_entry_modifies_its_own_original_object(self):
        session=self.session(311002);engine=session.engine
        source=self.add(engine,'Viashino Firstblade',zone='hand')
        other=self.add(engine,'Generic Bound Body')
        action=self.ready(session,source,{'W':1,'R':1,'C':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual((4,4),(engine._numeric_stat(source.object_id,'power'),engine._numeric_stat(source.object_id,'toughness')))
        self.assertEqual(2,engine._numeric_stat(other.object_id,'power'));self.replay(session)

    def test_actual_veteran_tap_issues_optional_counter_and_replays(self):
        session=self.session(311003);engine=session.engine
        source=self.add(engine,'Veteran of the Depths')
        spell=self.add(engine,'Generic Source Pronoun Tap',zone='hand')
        action=self.ready(session,spell,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(48):
            if session.state.pending_decision and session.state.pending_decision.kind=='semantic.choice':break
            if session.state.pending_decision and session.state.pending_decision.kind=='trigger.order':self.order_triggers(session);continue
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        self.assertEqual('semantic.choice',session.state.pending_decision.kind)
        resumed=self.replay(session,load=True)
        chosen=resumed.act('pilot:A',{'action_id':'choose','choice':'put'});self.assertTrue(chosen.ok,chosen.summary);self.resolve(resumed)
        self.assertEqual(1,resumed.state.cards[source.object_id].counters['+1/+1']);self.replay(resumed)

    def test_blinked_firstblade_does_not_receive_old_source_trigger_twice(self):
        session=self.session(311004);engine=session.engine
        source=self.add(engine,'Viashino Firstblade',zone='hand');blink=self.add(engine,'Cloudshift',zone='hand')
        action=self.ready(session,source,{'W':3,'R':1,'C':6});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        for _ in range(24):
            if any(i.kind=='triggered_ability' and i.source_object_id==source.object_id for i in session.state.stack):break
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        old=session.state.cards[source.object_id].logical_object_id
        for _ in range(8):
            if session.pending_principals()[0]=='pilot:A':break
            accepted=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(accepted.ok,accepted.summary)
        action=next(a for a in session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions'] if a.get('card')==blink.ref)
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertNotEqual(old,session.state.cards[source.object_id].logical_object_id)
        self.assertEqual(4,engine._numeric_stat(source.object_id,'power'));self.replay(session)

    def assert_original_strict(self, session, source):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        self.assertTrue(bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')['strict_capability_ready'])

    def start_source_combat(self, session, *, active='A'):
        from quorune.model import CombatState
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.active_player = active
        engine.state.started = True
        engine.state.phase = 'combat'
        engine.state.step = 'declare_attackers'
        engine.state.phase_index = 5
        engine.state.combat = CombatState()
        for card in engine.state.cards.values():
            if card.zone == 'battlefield':
                card.acquired_control_turn_count = -1
        engine._issue_attackers()

    def test_original_rioters_attack_uses_resolution_power_and_ignores_returned_incarnation(self):
        for blink_source in (False, True):
            with self.subTest(blink_source=blink_source):
                session = self.session(311011 + int(blink_source))
                engine = session.engine
                source = self.add(engine, 'Rubblebelt Rioters', zone='hand')
                giant = self.add(engine, 'Orcish Siegemaster', ref='source-pronoun-greatest')
                giant.counters['+1/+1'] = 4
                blink = self.add(engine, 'Cloudshift', zone='hand')
                growth = self.add(engine, 'Giant Growth', zone='hand')
                self.assert_original_strict(session, source)
                offered = self.ready(session, source, {'R': 1, 'G': 2, 'C': 8, 'W': 2})
                self.checkpoint(session)
                accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.resolve(session)
                self.replay(session)
                self.start_source_combat(session)
                self.checkpoint(session)
                before = authoritative_state_hash(session.state)
                denied = session.act('pilot:D', {'action_id': 'attack', 'attackers': {source.ref: 'B'}})
                self.assertFalse(denied.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                accepted = session.act('pilot:A', {'action_id': 'attack', 'attackers': {source.ref: 'B'}})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertEqual(1, sum(item.source_object_id == source.object_id for item in engine.state.stack))
                session = self.replay(session, load=True)
                engine = session.engine
                if blink_source:
                    old = engine.state.cards[source.object_id].logical_object_id
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    offered = next(a for a in actions if a.get('card') == blink.ref)
                    accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [source.ref], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve(session)
                    self.assertNotEqual(old, engine.state.cards[source.object_id].logical_object_id)
                else:
                    actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    offered = next(a for a in actions if a.get('card') == growth.ref)
                    accepted = session.act('pilot:A', {'action_id': offered['id'], 'targets': [giant.ref], 'pay': 'auto'})
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.resolve(session)
                self.assertEqual(0 if blink_source else 7, engine._numeric_stat(source.object_id, 'power'))
                self.assertEqual(4 if blink_source else 7, engine._numeric_stat(giant.object_id, 'power'))
                projected = json.dumps(session.projector._snapshot('pilot:D'), sort_keys=True)
                for seat in 'ABC':
                    for object_id in engine.state.players[seat].zones['hand']:
                        self.assertNotIn(engine.state.cards[object_id].ref, projected)
                self.replay(session)

    def test_original_root_spider_block_adds_first_strike_to_only_source_and_replays(self):
        session = self.session(311013)
        engine = session.engine
        source = self.add(engine, 'Root Spider', zone='hand')
        other = self.add(engine, 'Generic Bound Body', ref='source-pronoun-other')
        attacker = self.add(engine, 'Generic Bound Body', seat='B', ref='source-pronoun-attacker')
        self.assert_original_strict(session, source)
        offered = self.ready(session, source, {'G': 1, 'C': 3})
        self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.replay(session)
        self.start_source_combat(session, active='B')
        self.checkpoint(session)
        accepted = session.act('pilot:B', {'action_id': 'attack', 'attackers': {attacker.ref: 'A'}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(30):
            if session.state.pending_decision.kind == 'combat.blockers':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        before = authoritative_state_hash(session.state)
        denied = session.act('pilot:C', {'action_id': 'block', 'blocks': {source.ref: attacker.ref}})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id': 'block', 'blocks': {source.ref: attacker.ref}})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True)
        self.resolve(session)
        engine = session.engine
        self.assertEqual(3, engine._numeric_stat(source.object_id, 'power'))
        self.assertEqual(2, engine._numeric_stat(other.object_id, 'power'))
        self.assertIn('first strike', [k.casefold() for k in engine._effective_card_data(engine.state.cards[source.object_id])['keywords']])
        self.assertNotIn('first strike', [k.casefold() for k in engine._effective_card_data(engine.state.cards[other.object_id])['keywords']])
        self.replay(session)

    def test_source_body_normalization_omission_mutant_is_killed_by_original_firstblade(self):
        c=self.db.lookup('Viashino Firstblade')
        with patch('quorune.compiler.source_self_effect_templates.normalized_source_result_body',return_value=None):
            ir=compile_oracle_card(c,capability_registry=self.registry)
            with self.assertRaises(AssertionError):self.assertEqual('exact',ir.status)


if __name__=='__main__':unittest.main()
