from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import PublicQueryAmountSpec
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses


class QueryScaledCounterCompilerTests(unittest.TestCase):
    def test_unsupported_power_up_cost_cannot_be_stripped_into_unlimited_activation(self):
        text='Power-up — {4}{X}: Put a +1/+1 counter on this creature for each card in your hand. (Activate each power-up ability only once. Reduce the cost by its mana cost if it entered this turn.)'
        from quorune.abilities import parse_activated_abilities
        abilities=parse_activated_abilities(card_name='Generic Quantity Source',oracle_text=text,keywords=())
        self.assertTrue(abilities[0].uncompiled_costs)
        ir=compile_oracle_card(replace(query_record(text),type_line='Creature'),capability_registry=load_default_capability_registry())
        self.assertNotEqual('exact',ir.status)
    def test_public_query_counter_amounts_preserve_subject_counter_kind_and_query(self):
        cases=(
            ('Put a +1/+1 counter on target creature for each Elf you control.','elf',1),
            ('Put two charge counters on this artifact for each artifact you control.',None,2),
            ('Put a +1/+1 counter on this creature for each artifact and/or creature card in your graveyard.',None,1),
        )
        for text,subtype,coefficient in cases:
            with self.subTest(text=text):
                record=replace(query_record(text if 'target creature' in text else '{1}: '+text),type_line='Sorcery' if 'target creature' in text else 'Artifact Creature')
                ir=compile_oracle_card(record,capability_registry=load_default_capability_registry())
                self.assertEqual('exact',ir.status)
                effect=ir.faces[0].nodes[0].effects[0]
                self.assertEqual('place_counters',effect['op'])
                amount=PublicQueryAmountSpec.from_dict(effect['amount']);self.assertEqual(coefficient,amount.coefficient)
                if subtype:self.assertEqual(('elf',),amount.quantity.query.subtypes_all)
                if 'graveyard' in text:self.assertEqual(('artifact','creature'),amount.quantity.query.types_any)
                self.assertIn('counter.producer.fixed_effect',ir.faces[0].nodes[0].capability_dependencies)

    def test_query_counter_unknown_quantities_and_blocked_owner_fail_closed(self):
        for text in ('Put a +1/+1 counter on target creature for each creature with flying you control.',
            'Put a +1/+1 counter on this creature for each sticker you own.',
            'Put a +1/+1 counter on this creature for each creature you control. You win the game.'):
            ir=compile_oracle_card(replace(query_record(text if 'target creature' in text else '{1}: '+text),type_line='Sorcery' if 'target creature' in text else 'Creature'),capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact',ir.status,text)
        value=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        for row in value['capabilities']:
            if row['id']=='counter.producer.fixed_effect':row.update(status='blocked',blockers=['placement owner unavailable'])
        ir=compile_oracle_card(replace(query_record('{1}: Put a +1/+1 counter on this creature for each creature you control.'),type_line='Creature'),capability_registry=CapabilityRegistry(value))
        self.assertNotEqual('exact',ir.status)

    def test_query_counter_sequence_preserves_instruction_order_and_rejects_open_tail(self):
        text='When this creature enters, mill two cards, then put a +1/+1 counter on this creature for each creature card in your graveyard.'
        ir=compile_oracle_card(replace(query_record(text),type_line='Creature'),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status)
        self.assertEqual(['mill','place_counters'],[effect['op'] for effect in ir.faces[0].nodes[0].effects])
        for suffix in (' You win the game.',' If you do, draw a card.'):
            ir=compile_oracle_card(replace(query_record(text+suffix),type_line='Creature'),capability_registry=load_default_capability_registry())
            self.assertNotEqual('exact',ir.status)


class QueryScaledCounterRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'quantity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/query-scaled-counter-cards.json',ROOT/'tests/fixtures/counter-doubling-cards.json',ROOT/'tests/fixtures/counter-replacement-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Query counters witness',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve=witnesses.BoundEffectProgramRuntimeTests.resolve
    replay=witnesses.BoundEffectProgramRuntimeTests.replay

    def test_actual_millipede_counts_after_milling_and_replays(self):
        session=self.session(289001);engine=session.engine
        source=self.add(engine,'Moldgraf Millipede',zone='hand')
        for index in range(2):self.add(engine,'Generic Bound Body',zone='library',ref='milled-body-'+str(index))
        self.add(engine,'Generic Bound Plains',zone='library',ref='milled-land')
        before=len(session.state.players['A'].zones['library'])
        action=self.ready(session,source,{'C':4,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(before-3,len(session.state.players['A'].zones['library']))
        self.assertEqual(2,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_actual_magistrate_counts_only_controlled_elves_and_uses_real_targets(self):
        session=self.session(289002);engine=session.engine
        source=self.add(engine,'Immaculate Magistrate');source.acquired_control_turn_count=-1
        elf=self.add(engine,'Generic Counter Quantity Elf')
        self.add(engine,'Generic Counter Quantity Elf',seat='B',ref='enemy-elf')
        target=self.add(engine,'Generic Bound Body',seat='C')
        land=self.add(engine,'Generic Bound Plains')
        action=self.ready(session,source,{});self.checkpoint(session)
        self.assertIn(target.ref,action['target_schema']['legal_refs']);self.assertNotIn(land.ref,action['target_schema']['legal_refs'])
        before=authoritative_state_hash(session.state)
        rejected=session.act('pilot:B',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'})
        self.assertFalse(rejected.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[target.ref],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(2,session.state.cards[target.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_actual_ooze_patrol_counts_overlapping_artifact_creature_once(self):
        session=self.session(289003);engine=session.engine
        source=self.add(engine,'Ooze Patrol',zone='hand')
        self.add(engine,'Generic Counter Quantity Artifact Creature',zone='library')
        self.add(engine,'Generic Bound Plains',zone='library',ref='ooze-milled-land')
        action=self.ready(session,source,{'C':3,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session)
        self.assertEqual(1,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.replay(session,load=True)

    def test_zero_query_placement_creates_no_counter_event_or_replacement_choice(self):
        session=self.session(289004);engine=session.engine
        source=self.add(engine,'Generic Counter Quantity Zero')
        self.add(engine,'Hardened Scales');self.add(engine,'Doubling Season')
        action=self.ready(session,source,{});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        from quorune import counter_placement_events as owner
        with patch.object(owner,'dispatch_counter_placement_occurrences',wraps=owner.dispatch_counter_placement_occurrences) as dispatched:
            pending=self.resolve(session)
            self.assertFalse(dispatched.called)
        self.assertIsNone(pending)
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        self.assertFalse(any(row.code=='counter.put' for row in session.state.events))
        self.replay(session,load=True)

    def test_post_mill_query_pending_replacements_save_original_amount_and_replay(self):
        session=self.session(289005);engine=session.engine
        source=self.add(engine,'Moldgraf Millipede',zone='hand')
        self.add(engine,'Hardened Scales');self.add(engine,'Doubling Season')
        for index in range(2):self.add(engine,'Generic Bound Body',zone='library',ref='replacement-milled-'+str(index))
        self.add(engine,'Generic Bound Plains',zone='library',ref='replacement-milled-land')
        before=len(session.state.players['A'].zones['library'])
        action=self.ready(session,source,{'C':4,'G':1});self.checkpoint(session)
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(before-3,len(session.state.players['A'].zones['library']))
        self.assertEqual(0,session.state.cards[source.object_id].counters.get('+1/+1',0))
        resumed=self.replay(session,load=True);before_hash=authoritative_state_hash(resumed.state)
        option=resumed.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
        wrong=resumed.act('pilot:B',{'action_id':'choose','replacement':option})
        self.assertFalse(wrong.ok);self.assertEqual(before_hash,authoritative_state_hash(resumed.state))
        while resumed.state.pending_decision is not None and resumed.state.pending_decision.kind=='replacement.order':
            option=resumed.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            result=resumed.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        self.resolve(resumed)
        self.assertIn(resumed.state.cards[source.object_id].counters['+1/+1'],(5,6))
        self.assertEqual(before-3,len(resumed.state.players['A'].zones['library']))
        self.replay(resumed,load=True)

    def test_unsupported_power_up_carrier_is_untrusted_and_never_offers_unlimited_activation(self):
        session=self.session(289006);engine=session.engine
        source=self.add(engine,'Generic Unsupported Power Up')
        engine.state.players['A'].mana_pool.update({'C':8,'G':2,'U':2})
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.started=True;engine.state.active_player='A';engine.state.phase='precombat_main';engine.state.step='main'
        engine._grant_priority('A');engine.pump()
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        self.assertFalse(any(row['id'].startswith('activate:'+source.ref+':') for row in actions))
        programs=engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertTrue(any(not engine.semantic_program_is_current_trusted(program) for program in programs))

    def test_query_counter_coefficient_mutant_is_killed(self):
        from quorune.compiler import public_query_effect_amounts as owner
        with patch.object(owner,'_coefficient',return_value=2):
            with self.assertRaises(AssertionError):self.test_actual_magistrate_counts_only_controlled_elves_and_uses_real_targets()

    def test_query_amount_is_sealed_after_instruction_before_replacement_resumption(self):
        session=self.session(289007);engine=session.engine
        source=self.add(engine,'Moldgraf Millipede',zone='hand')
        self.add(engine,'Hardened Scales');self.add(engine,'Doubling Season')
        for index in range(2):self.add(engine,'Generic Bound Body',zone='library',ref='sealed-mill-'+str(index))
        self.add(engine,'Generic Bound Plains',zone='library',ref='sealed-land')
        action=self.ready(session,source,{'C':4,'G':1})
        accepted=session.act('pilot:A',{'action_id':action['id'],'pay':'auto'});self.assertTrue(accepted.ok,accepted.summary)
        self.resolve(session);self.assertEqual('replacement.order',session.state.pending_decision.kind)
        self.assertEqual(2,session.state.pending_decision.continuation['effect']['amount'])
        # Owner diagnostic: no player can alter a resolving instruction here.
        # A changed graveyard proves resumption reads the sealed request.
        self.add(engine,'Generic Bound Body',zone='graveyard',ref='late-graveyard-body')
        while session.state.pending_decision is not None and session.state.pending_decision.kind=='replacement.order':
            option=session.packet('pilot:A',full=True)['decision']['ctx']['options'][0]['id']
            result=session.act('pilot:A',{'action_id':'choose','replacement':option});self.assertTrue(result.ok,result.summary)
        self.resolve(session)
        self.assertIn(session.state.cards[source.object_id].counters['+1/+1'],(5,6))


if __name__=='__main__':unittest.main()
