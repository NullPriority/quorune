from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import json,tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from damage_replacement_support import DamageReplacementPipelineBase
from quorune.damage_prevention_permission import (DamagePreventionPermissionSnapshot,StaticUnpreventableDamageSpec,
    UNPREVENTABLE_SOURCE_FACT,UNPREVENTABLE_CAPABILITY,decode_static_unpreventability,unpreventability_payload_dependencies)
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as native
import test_ability_label_composition as journeys
import test_opening_hand_battlefield as opening
from quorune.rules.evidence_storage import read_evidence_document


class UnpreventableDamageKernelTests(DamageReplacementPipelineBase):
    def test_closed_permission_scopes_preserve_controller_type_combat_and_sealed_self_facts(self):
        source = SimpleNamespace(controller='A',types=('Creature',),scope_facts=())
        local = DamagePreventionPermissionSnapshot(False,('A',))
        self.assertTrue(local.unpreventable(source,combat=True))
        self.assertFalse(local.unpreventable(source,combat=False))
        source.controller = 'B';self.assertFalse(local.unpreventable(source,combat=True))
        source.controller = 'A';source.types = ('Artifact',)
        self.assertFalse(local.unpreventable(source,combat=True))
        source.scope_facts = (UNPREVENTABLE_SOURCE_FACT,)
        self.assertTrue(DamagePreventionPermissionSnapshot().unpreventable(source,combat=False))
        source.scope_facts = ()
        self.assertFalse(DamagePreventionPermissionSnapshot().unpreventable(source,combat=False))
        self.assertTrue(DamagePreventionPermissionSnapshot(True).unpreventable(source,combat=False))
        for scope in ('global','self','controller_creatures_combat'):
            spec = StaticUnpreventableDamageSpec(scope)
            self.assertEqual(spec,StaticUnpreventableDamageSpec.from_dict(spec.to_dict()))
        for scope in ('opponents','owner','all_sources_with_this_name',False,[]):
            with self.assertRaises(ValueError):StaticUnpreventableDamageSpec(scope)
        for snapshot in ((1,()),(False,('B','A')),(False,('A','A')),(False,['A'])):
            with self.assertRaises(ValueError):DamagePreventionPermissionSnapshot(*snapshot)

    def test_static_frames_require_exact_descriptor_and_current_source_scope(self):
        from quorune.compiler.damage_permission_templates import static_damage_unpreventability_handler
        frames = (("Damage can't be prevented.",'global'),
            ("Damage that would be dealt by this creature can't be prevented.",'self'),
            ("Combat damage that would be dealt by creatures you control can't be prevented.",'controller_creatures_combat'))
        for text,scope in frames:
            row = static_damage_unpreventability_handler(text);self.assertIsNotNone(row)
            self.assertEqual(scope,decode_static_unpreventability(row[1]).scope)
            self.assertEqual((UNPREVENTABLE_CAPABILITY,),unpreventability_payload_dependencies(row[1],coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,)))
            with self.assertRaises(ValueError):unpreventability_payload_dependencies(row[1])
            for change in ({'schema_version':True},{'event':'damage'},{'future':1}):
                with self.assertRaises(ValueError):decode_static_unpreventability({**row[1],**change})
        for text in ("Damage can't be prevented this turn.","If you control a Dragon, damage can't be prevented.",
                "Damage dealt by creatures you own can't be prevented.","Damage can't be prevented. Draw a card."):
            self.assertIsNone(static_damage_unpreventability_handler(text))

    def flagged_damage(self,seed,*,self_fact):
        from test_damage_result_events import DamageResultEventTests
        from quorune.rules.damage_snapshots import source_snapshot
        from quorune.damage import damage_proposal,prepare_damage_batch,commit_prepared_damage_batch
        from quorune.damage_modifier_state import DamageModifierDuration,DamagePreventionShield,DamageSubject,PreventionMode
        engine = self.session(seed).engine
        source = DamageResultEventTests.token(self,engine,'A','Sealed source witness')
        snapshot = source_snapshot(engine,source.ref,controller='A')
        if self_fact:snapshot = replace(snapshot,scope_facts=tuple(sorted({*snapshot.scope_facts,UNPREVENTABLE_SOURCE_FACT})))
        shield = DamagePreventionShield(shield_id='sealed-source-shield',source_id='fixture:shield',controller='B',
            subject=DamageSubject(ref='B',kind='player',controller='B'),mode=PreventionMode.AMOUNT,remaining=3,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN,created_turn_sequence=engine.state.turn_sequence)
        engine.state.damage_prevention_shields.append(shield)
        proposal = damage_proposal(engine,proposal_id='sealed-source-damage',actor='A',source_ref=source.ref,
            source_override=snapshot,target='B',amount=2,combat=False,reason='typed sealed source permission')
        before = engine.state.players['B'].life
        prepared = prepare_damage_batch(engine,(proposal,))
        self.assertEqual(self_fact,prepared.events[0].payload['unpreventable'])
        commit_prepared_damage_batch(engine,prepared)
        return engine,before

    def test_sealed_self_fact_sets_existing_flag_without_consuming_shield(self):
        engine,before = self.flagged_damage(615_120_001,self_fact=True)
        self.assertEqual(before-2,engine.state.players['B'].life)
        self.assertEqual(3,engine.state.damage_prevention_shields[0].remaining)

    def test_absent_permission_keeps_preventable_damage_and_shield_consumption(self):
        engine,before = self.flagged_damage(615_120_002,self_fact=False)
        self.assertEqual(before,engine.state.players['B'].life)
        self.assertEqual(1,engine.state.damage_prevention_shields[0].remaining)

    def test_fresh_permission_to_flag_omission_is_detected(self):
        method = 'test_sealed_self_fact_sets_existing_flag_without_consuming_shield'
        with patch('quorune.damage_prevention_permission.DamagePreventionPermissionSnapshot.unpreventable',return_value=False):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


NAMES = ('Leyline of Punishment','Excruciator')


class UnpreventableDamageRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.LandSearchViewEquivalenceTests.session
    add = native.LandSearchViewEquivalenceTests.add
    prepare = native.LandSearchViewEquivalenceTests.prepare
    checkpoint = native.LandSearchViewEquivalenceTests.checkpoint
    resolve = native.LandSearchViewEquivalenceTests.resolve
    replay = native.LandSearchViewEquivalenceTests.replay
    private_replay = native.LandSearchViewEquivalenceTests.private_replay
    priority = native.LandSearchViewEquivalenceTests.priority
    actions = native.LandSearchViewEquivalenceTests.actions
    cast = native.LandSearchViewEquivalenceTests.cast
    reject = native.LandSearchViewEquivalenceTests.reject
    binding = native.LandSearchViewEquivalenceTests.binding
    activation_command = native.LandSearchViewEquivalenceTests.activation_command
    activate = native.LandSearchViewEquivalenceTests.activate
    resume = native.LandSearchViewEquivalenceTests.resume
    advance = journeys.AbilityLabelCompositionTests.advance

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory();path = Path(cls.temp.name)/'unpreventable.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/opening-hand-battlefield-cards.json',ROOT/'tests/fixtures/unpreventable-damage-cards.json'],path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Unpreventable damage',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def shield(self,s,seat,amount=3):
        from quorune.damage_modifier_state import DamageModifierDuration,DamagePreventionShield,DamageSubject,PreventionMode
        s.state.damage_prevention_shields.append(DamagePreventionShield(shield_id='native-shield-'+seat,
            source_id='fixture:native-shield-'+seat,controller=seat,subject=DamageSubject(ref=seat,kind='player',controller=seat),
            mode=PreventionMode.AMOUNT,remaining=amount,duration=DamageModifierDuration.UNTIL_USED,
            created_turn_sequence=s.state.turn_sequence))

    def remaining(self,s,seat):
        return next(x.remaining for x in s.state.damage_prevention_shields if x.shield_id == 'native-shield-'+seat)

    def test_originals_normally_bind_complete_static_permission_and_current_scope(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.damage_prevention_permission import UNPREVENTABLE_EVENT
        for i,name in enumerate(NAMES):
            s = self.session(615130000+i);source = self.add(s.engine,name,zone='hand')
            b = self.binding(s,source);self.assertTrue(b['strict_capability_ready'],b['blockers'])
            programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in programs))
            p = next(p for p in programs if p.event == UNPREVENTABLE_EVENT)
            deps = tuple(c for c in p.capability_dependencies if c != UNPREVENTABLE_CAPABILITY)
            missing = replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            result = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+UNPREVENTABLE_CAPABILITY,result['blockers'])

    def test_real_leyline_opening_hand_choice_is_private_and_enters_without_casting(self):
        helper = opening.OpeningHandBattlefieldRuntimeTests
        s = helper.session(self,615130010,{'A':[NAMES[0]]})
        card = helper.card(self,s,NAMES[0]);helper.keep(self,s)
        self.assertEqual('opening.hand',s.state.pending_decision.kind)
        self.assertFalse(s.state.started)
        command = {'action_id':'choose','choices':{'refs':[card.ref]}}
        self.reject(s,command,seat='D')
        for seat in 'BCD':self.assertNotIn(card.ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        s = self.resume(s)
        result = s.act('pilot:A',command);self.assertTrue(result.ok,result.summary)
        self.assertEqual('battlefield',s.state.cards[card.object_id].zone)
        self.assertEqual(1,s.state.turn_sequence)
        self.assertEqual(('A','upkeep'),(s.state.active_player,s.state.step))
        self.assertFalse(s.state.stack)
        self.replay(s,load=True)

    def test_real_leyline_cast_preserves_shields_counter_aftermath_and_life_restriction(self):
        s = self.session(615130020);source = self.add(s.engine,NAMES[0],zone='hand')
        shocks = [self.add(s.engine,'Shock',zone='hand',ref='leyline-shock-'+str(i)) for i in range(3)]
        heals = [self.add(s.engine,'Revitalize',zone='hand',ref='leyline-heal-'+str(i)) for i in range(2)]
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        tiger = self.add(s.engine,'Phantom Tiger',seat='B');tiger.counters['+1/+1'] = 4
        self.shield(s,'B');s.state.players['B'].mana_pool['U'] = 2
        bounce = self.add(s.engine,'Boomerang',seat='B',zone='hand')
        self.prepare(s,mana={'C':4,'R':5,'W':2});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':2});self.assertIsNone(self.resolve(s))
        self.cast(s,heals[0],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['A'].life)
        self.cast(s,shocks[0],['B'],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['B'].life);self.assertEqual(3,self.remaining(s,'B'))
        self.cast(s,shocks[1],[tiger.ref],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(3,s.state.cards[tiger.object_id].counters['+1/+1'])
        self.assertEqual(2,s.state.cards[tiger.object_id].marked_damage)
        self.cast(s,bounce,[source.ref],seat='B',payment={'U':2});self.assertIsNone(self.resolve(s))
        self.cast(s,shocks[2],['B'],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(38,s.state.players['B'].life);self.assertEqual(1,self.remaining(s,'B'))
        self.cast(s,heals[1],payment={'C':1,'W':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(43,s.state.players['A'].life)
        self.private_replay(s,private)

    def test_real_excruciator_source_lki_survives_bounce_and_humbled_new_incarnation(self):
        from quorune.rules.damage_snapshots import source_snapshot
        from quorune.rules.stack_damage_sources import SOURCE_DAMAGE_CONTEXT
        s = self.session(615130030);source = self.add(s.engine,NAMES[1],zone='hand')
        aura = self.add(s.engine,'Hermetic Study',zone='hand')
        haste = self.add(s.engine,'Expedite',zone='hand');shock = self.add(s.engine,'Shock',zone='hand')
        humble = self.add(s.engine,'Humble',zone='hand');self.add(s.engine,'Vedalken Orrery')
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.shield(s,'B');s.state.players['B'].mana_pool['U'] = 1
        self.prepare(s,mana={'C':14,'R':6,'U':1,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':6,'R':2});self.assertIsNone(self.resolve(s))
        self.cast(s,aura,[source.ref],payment={'C':1,'U':1});self.assertIsNone(self.resolve(s))
        self.cast(s,haste,[source.ref],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.cast(s,shock,['B'],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['B'].life);self.assertEqual(1,self.remaining(s,'B'))
        old_identity = s.state.cards[source.object_id].logical_object_id
        self.activate(s,source,targets=['B'])
        self.cast(s,bounce,[source.ref],seat='B',payment={'U':1})
        for _ in range(12):
            if s.state.cards[source.object_id].zone == 'hand':break
            result = s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        old_ability = s.state.stack[-1]
        self.assertIn(UNPREVENTABLE_SOURCE_FACT,old_ability.context[SOURCE_DAMAGE_CONTEXT]['scope_facts'])
        self.cast(s,source,payment={'C':6,'R':2})
        for _ in range(12):
            if s.state.cards[source.object_id].zone == 'battlefield':break
            result = s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertNotEqual(old_identity,s.state.cards[source.object_id].logical_object_id)
        self.cast(s,humble,[source.ref],payment={'C':1,'W':1})
        s = self.resume(s);self.assertIsNone(self.resolve(s))
        self.assertEqual(39,s.state.players['B'].life);self.assertEqual(1,self.remaining(s,'B'))
        self.assertNotIn(UNPREVENTABLE_SOURCE_FACT,source_snapshot(s.engine,source.ref,controller='A').scope_facts)
        self.private_replay(s,private)

    def test_synthetic_controlled_combat_rule_uses_controller_and_excludes_noncombat(self):
        s = self.session(615130040);rule = self.add(s.engine,'Generic Controlled Combat Rule',zone='hand')
        first = self.add(s.engine,'Grizzly Bears',ref='combat-permission-a');first.acquired_control_turn_count = -1
        second = self.add(s.engine,'Grizzly Bears',seat='B',ref='combat-permission-b')
        self.add(s.engine,'Fugitive Wizard',ref='combat-permission-blocker')
        shock = self.add(s.engine,'Shock',zone='hand')
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand');self.add(s.engine,'Vedalken Orrery',seat='B')
        lands = [self.add(s.engine,name,seat='B',ref='combat-permission-mana-'+str(i)) for i,name in enumerate(('Island','Island','Plains','Plains'))]
        self.shield(s,'A');self.shield(s,'B')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'G':1,'R':1});self.checkpoint(s)
        self.cast(s,rule,payment={'G':1});self.assertIsNone(self.resolve(s))
        self.cast(s,shock,['B'],payment={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual(40,s.state.players['B'].life);self.assertEqual(1,self.remaining(s,'B'))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:A',{'action_id':'attack','atk':{first.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.blockers')
        result = s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual(38,s.state.players['B'].life);self.assertEqual(1,self.remaining(s,'B'))
        for land in lands:self.activate(s,land,seat='B')
        self.cast(s,steal,[rule.ref],seat='B',payment={'U':2,'W':2});self.assertIsNone(self.resolve(s))
        self.assertEqual(('A','B'),(s.state.cards[rule.object_id].owner,s.state.cards[rule.object_id].controller))
        self.advance(s,lambda x:x.state.active_player == 'B' and getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:B',{'action_id':'attack','atk':{second.ref:'A'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.blockers')
        result = s.act('pilot:A',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual(38,s.state.players['A'].life);self.assertEqual(3,self.remaining(s,'A'))
        self.private_replay(s,private)

    def test_verified_withdrawal_blocks_exact_static_source(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed = deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id'] == UNPREVENTABLE_CAPABILITY).update(status='blocked',blockers=['withdrawn static damage permission'],dependency_fail_closed_status='not_run')
        registry = CapabilityRegistry(changed)
        index = build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+UNPREVENTABLE_CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_current_source_rule_omission_is_detected(self):
        method = 'test_real_leyline_cast_preserves_shields_counter_aftermath_and_life_restriction'
        with patch('quorune.semantic_runtime.damage_permissions._current_specs',return_value=()):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


if __name__ == '__main__':unittest.main()
