"""CR609.4/702.3b/508.1c: numeric Defender exceptions read declaration state."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.oracle_ir import compile_oracle_card
from test_granted_skulk import record
from quorune.compiler.defender_permission_templates import static_defender_permission_handler
from quorune.semantic_runtime.defender_declarations import DefenderDeclarationPermissionHandler
from quorune.semantic_runtime.conditional_continuous import validate_static_public_state_condition
from scripts.build_test_database import build_fixture_database
import test_scalar_mill_wording as controls


class DefenderDeclarationNumericCompilerTests(unittest.TestCase):
    def test_whole_card_declaration_composition_retains_legacy_multiple_handlers(self):
        registry=load_default_capability_registry()
        for text in ("Defender\nThis creature can attack as though it didn't have defender.",
                "Creatures you control have vigilance.",
                "Defender\nAs long as this creature's power is 5 or greater, it can attack as though it didn't have defender."):
            ir=compile_oracle_card(record(text,'Creature'),capability_registry=registry,
                capability_profile='commander_review')
            self.assertEqual('exact',ir.status,text);self.assertFalse(ir.material_residuals,text)

    def test_numeric_permission_has_its_own_event_and_keeps_generic_layer_guard(self):
        for text in (
                "As long as you control a creature with power 4 or greater, this creature can attack as though it didn't have defender.",
                "As long as this creature's power is 5 or greater, this creature can attack as though it didn't have defender."):
            template=static_defender_permission_handler(text,source_name='Generic Source')
            self.assertIsNotNone(template)
            descriptor=template[1];self.assertEqual('combat.attack.permission',descriptor['event'])
            rule=DefenderDeclarationPermissionHandler().validate(descriptor)
            with self.assertRaises(ValueError):validate_static_public_state_condition(rule.source_condition.to_dict())
            for changes in ({'permission':'ignore_all_restrictions'},{'schema_version':True},{'event':'characteristics.evaluate'}):
                with self.assertRaises(ValueError):DefenderDeclarationPermissionHandler().validate({**descriptor,**changes})
        self.assertIsNotNone(static_defender_permission_handler(
            "As long as this creature's power is 5 or greater, it can attack as though it didn't have defender.",source_name='Generic Source'))
        self.assertIsNone(static_defender_permission_handler(
            "As long as you control a creature with power 4 or greater, it can attack as though it didn't have defender.",source_name='Generic Source'))

    def test_withdrawn_declaration_capability_keeps_numeric_permission_residual(self):
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        cap='combat.attack.defender_permission.declaration_numeric'
        next(row for row in raw['capabilities'] if row['id']==cap).update(
            status='blocked',blockers=['withdrawn declaration authority'])
        text="Defender\nAs long as this creature's power is 5 or greater, it can attack as though it didn't have defender."
        ir=compile_oracle_card(record(text,'Creature'),capability_registry=CapabilityRegistry(raw),
            capability_profile='commander_review')
        self.assertNotEqual('exact',ir.status);self.assertTrue(ir.material_residuals)

    def test_declaration_descriptor_rejects_unknown_and_nonnumeric_conditions(self):
        _,descriptor,_=static_defender_permission_handler(
            "As long as this creature's power is 5 or greater, it can attack as though it didn't have defender.",
            source_name='Generic Source')
        for changes in ({'extra':True},{'source_condition':None},
                {'source_condition':{'kind':'source_has_counter','counter':'charge','minimum':1}}):
            with self.assertRaises(ValueError):DefenderDeclarationPermissionHandler().validate({**descriptor,**changes})


class DefenderDeclarationNumericRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add=True
    session=controls.ScalarMillWordingRuntimeTests.session
    add=controls.ScalarMillWordingRuntimeTests.add
    prepare=controls.ScalarMillWordingRuntimeTests.prepare
    checkpoint=controls.ScalarMillWordingRuntimeTests.checkpoint
    cast=controls.ScalarMillWordingRuntimeTests.cast
    resolve=controls.ScalarMillWordingRuntimeTests.resolve
    replay=controls.ScalarMillWordingRuntimeTests.replay
    until=controls.ScalarMillWordingRuntimeTests.until
    binding=controls.ScalarMillWordingRuntimeTests.binding

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();path=Path(cls.temp.name)/'defender-numeric.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/defender-declaration-numeric-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Numeric Defender',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',20)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def attack_window(self,session):
        self.until(session,lambda s:s.state.active_player=='A' and s.state.pending_decision is not None
            and s.state.pending_decision.kind=='combat.attackers')
        return session.packet('pilot:A',full=True)['decision']['ctx']['candidates']

    def privacy_and_replay(self,session,private):
        for viewer in 'ABD':self.assertNotIn(private.ref,json.dumps(session.packet('pilot:'+viewer,full=True)))
        self.assertIn(private.ref,json.dumps(session.packet('pilot:C',full=True)))
        return self.replay(session,load=True)

    def test_original_sentry_uses_controlled_full_power_and_actual_combat(self):
        session=self.session(71701001);source=self.add(session.engine,'Bristlepack Sentry')
        large=self.add(session.engine,'Generic Defender Large Body')
        source.acquired_control_turn_count=-1;large.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        candidates=self.attack_window(session);self.assertIn(source.ref,{r['id'] for r in candidates})
        self.assertIn('Defender',session.engine._effective_card_data(source)['keywords'])
        before=authoritative_state_hash(session.state);command={'action_id':'attack','attackers':{source.ref:'B'}}
        self.assertFalse(session.act('pilot:D',command).ok);self.assertEqual(before,authoritative_state_hash(session.state))
        result=session.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('B',session.state.cards[source.object_id].attacking)
        session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(37,session.state.players['B'].life)
        self.privacy_and_replay(session,private)

    def test_original_depthguard_reads_its_buffed_power_after_characteristics_finish(self):
        session=self.session(71701002);source=self.add(session.engine,'Karsus Depthguard')
        source.acquired_control_turn_count=-1
        growth=self.add(session.engine,'Giant Growth',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertLess(session.engine._numeric_stat(source.object_id,'power'),5)
        self.cast(session,'A',growth,[source.ref]);self.assertIsNone(self.resolve(session))
        power=session.engine._numeric_stat(source.object_id,'power');self.assertGreaterEqual(power,5)
        candidates=self.attack_window(session);self.assertIn(source.ref,{r['id'] for r in candidates})
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(40-power,session.state.players['B'].life)
        self.privacy_and_replay(session,private)

    def test_opponents_qualifying_power_does_not_offer_or_accept_original_tyrannodon(self):
        session=self.session(71701003);source=self.add(session.engine,'Drowsing Tyrannodon')
        source.acquired_control_turn_count=-1
        large=self.add(session.engine,'Generic Defender Large Body',seat='B');large.acquired_control_turn_count=-1
        ordinary=self.add(session.engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        candidates=self.attack_window(session);self.assertNotIn(source.ref,{r['id'] for r in candidates})
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.privacy_and_replay(session,private)

    def test_declaration_condition_omission_is_killed_by_fresh_native_original(self):
        method='test_original_sentry_uses_controlled_full_power_and_actual_combat'
        with patch('quorune.rules.defender_declaration.current_defender_declaration_permission',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()

    def test_executable_declaration_rejects_an_undeclared_permission_capability(self):
        session=self.session(71701090);source=self.add(session.engine,'Bristlepack Sentry')
        program=next(p for p in session.engine.semantics.programs_for_oracle(source.oracle_id)
            if any(h.get('handler_id')=='permission.attack.defender-current-numeric.v1' for h in p.handlers))
        self.assertTrue(bind_semantic_program_runtime(program,capability_registry=self.registry,
            profile='commander_review')['strict'])
        cap='combat.attack.defender_permission.declaration_numeric'
        dependencies=tuple(c for c in program.capability_dependencies if c!=cap)
        missing=replace(program,capability_dependencies=dependencies,
            capability_closure=self.registry.closure(dependencies,profile='commander_review').to_dict() if dependencies else None)
        bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+cap,bound['blockers'])

    def test_original_sentry_counts_its_own_current_buffed_power(self):
        session=self.session(71701010);source=self.add(session.engine,'Bristlepack Sentry')
        source.acquired_control_turn_count=-1
        growth=self.add(session.engine,'Giant Growth',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'G':1});self.checkpoint(session)
        self.cast(session,'A',growth,[source.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual(6,session.engine._numeric_stat(source.object_id,'power'))
        candidates=self.attack_window(session);self.assertIn(source.ref,{r['id'] for r in candidates})
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(34,session.state.players['B'].life);self.privacy_and_replay(session,private)

    def test_actual_qualifier_departure_before_declaration_removes_the_offer(self):
        session=self.session(71701011);source=self.add(session.engine,'Bristlepack Sentry')
        source.acquired_control_turn_count=-1
        large=self.add(session.engine,'Generic Defender Large Body');large.acquired_control_turn_count=-1
        ordinary=self.add(session.engine,'Generic Bound Body');ordinary.acquired_control_turn_count=-1
        removal=self.add(session.engine,'Unsummon',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'U':1});self.checkpoint(session)
        self.cast(session,'A',removal,[large.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[large.object_id].zone)
        candidates=self.attack_window(session);self.assertNotIn(source.ref,{r['id'] for r in candidates})
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.privacy_and_replay(session,private)

    def test_actual_qualifier_departure_after_legal_declaration_does_not_undo_attack(self):
        session=self.session(71701012);source=self.add(session.engine,'Bristlepack Sentry')
        source.acquired_control_turn_count=-1
        large=self.add(session.engine,'Generic Defender Large Body');large.acquired_control_turn_count=-1
        removal=self.add(session.engine,'Unsummon',zone='hand')
        self.add(session.engine,'Island',ref='declaration-response-land')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session);self.checkpoint(session)
        self.assertIn(source.ref,{r['id'] for r in self.attack_window(session)})
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertTrue(result.ok,result.summary)
        self.cast(session,'A',removal,[large.ref]);self.assertIsNone(self.resolve(session))
        self.assertEqual('hand',session.state.cards[large.object_id].zone)
        self.assertEqual('B',session.state.cards[source.object_id].attacking)
        session=self.replay(session,load=True)
        self.until(session,lambda s:s.state.active_player=='A' and s.state.phase=='postcombat_main')
        self.assertEqual(37,session.state.players['B'].life);self.privacy_and_replay(session,private)

    def test_numeric_exception_preserves_tap_sickness_and_other_attack_prohibitions(self):
        for index,mode in enumerate(('tapped','sick','forbidden')):
            session=self.session(71701020+index)
            large=self.add(session.engine,'Generic Defender Large Body');large.acquired_control_turn_count=-1
            name='Generic Numeric Forbidden Defender' if mode=='forbidden' else 'Bristlepack Sentry'
            source=self.add(session.engine,name);source.acquired_control_turn_count=-1
            if mode=='tapped':source.tapped=True
            if mode=='sick':source.acquired_control_turn_count=session.state.players['A'].turns_begun
            private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
            self.prepare(session);self.checkpoint(session)
            self.assertTrue(self.binding(session,source)['strict_capability_ready'])
            candidates=self.attack_window(session);self.assertNotIn(source.ref,{r['id'] for r in candidates})
            before=authoritative_state_hash(session.state)
            result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
            self.privacy_and_replay(session,private)

    def test_actual_ability_loss_removes_numeric_permission_while_defender_remains(self):
        session=self.session(71701030);source=self.add(session.engine,'Bristlepack Sentry')
        source.acquired_control_turn_count=-1
        large=self.add(session.engine,'Generic Defender Large Body');large.acquired_control_turn_count=-1
        aura=self.add(session.engine,'Deep Freeze',zone='hand')
        private=self.add(session.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(session,mana={'C':2,'U':1});self.checkpoint(session)
        self.assertTrue(self.binding(session,source)['strict_capability_ready'])
        self.assertTrue(self.binding(session,aura)['strict_capability_ready'])
        self.cast(session,'A',aura,[source.ref]);self.assertIsNone(self.resolve(session))
        from quorune.rules.defender_declaration import current_defender_declaration_permission
        current=session.state.cards[source.object_id];data=session.engine._effective_card_data(current)
        self.assertIn('Defender',data['keywords'])
        self.assertFalse(current_defender_declaration_permission(session.engine,current,data))
        candidates=self.attack_window(session);self.assertNotIn(source.ref,{r['id'] for r in candidates})
        before=authoritative_state_hash(session.state)
        result=session.act('pilot:A',{'action_id':'attack','attackers':{source.ref:'B'}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.privacy_and_replay(session,private)
