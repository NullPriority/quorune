from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.banding_model import AttackBand, normalize_attack_bands, band_member_ids
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import CombatState
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_receiver_query_unions as controls
from quorune.rules.evidence_storage import read_evidence_document


CAPABILITY = 'combat.banding'
NAMES = ('Benalish Hero', 'Benalish Infantry', 'Icatian Phalanx', 'Kjeldoran Escort',
    'Kjeldoran Knight', 'Kjeldoran Phalanx', 'Kjeldoran Skycaptain', 'Kjeldoran Skyknight',
    'Kjeldoran Warrior', 'Knights of Thorn', 'Mesa Pegasus', 'Noble Elephant', 'Pikemen',
    'Shield Bearer', 'Teremko Griffin', 'Timber Wolves', 'Volunteer Reserves', 'Wall of Shields', 'War Elephant')


class BandingModelTests(unittest.TestCase):
    def test_attack_bands_are_closed_disjoint_incarnations_and_old_wire_is_unchanged(self):
        band = AttackBand((('b', 'b@1'), ('a', 'a@0')))
        self.assertEqual(band, AttackBand.from_dict(band.to_dict()))
        self.assertNotIn('attack_bands', CombatState().to_dict())
        combat = CombatState(attackers={'a': 'B', 'b': 'B'}, attack_bands=(band,))
        self.assertEqual(combat, CombatState.from_dict(combat.to_dict()))
        cards = {k: SimpleNamespace(logical_object_id=k+'@'+str(i)) for i,k in enumerate('ab')}
        self.assertEqual(('a', 'b'), band_member_ids(combat, cards, 'a'))
        cards['b'].logical_object_id = 'b@2'
        self.assertEqual(('a',), band_member_ids(combat, cards, 'a'))
        for raw in ((), (('a', ''),), (('a', 'x'), ('a', 'y')), (1,)):
            with self.assertRaises(ValueError): AttackBand(raw)
        with self.assertRaises(ValueError): normalize_attack_bands((band, band))
        for raw in ({'members': [], 'extra': True}, {'members': [{'object_id': 'a'}]}, {'members': 'a'}):
            with self.assertRaises(ValueError): AttackBand.from_dict(raw)

    def test_relationship_owner_rejects_stale_band_before_any_attack_write(self):
        from quorune.combat_relationship_state import commit_attack_declaration, AttackDeclarationAssignment, CombatRelationshipStateError
        card = SimpleNamespace(object_id='a', logical_object_id='a@1', zone='battlefield', controller='A', phased_out=False, attacking=None)
        assignment = AttackDeclarationAssignment('a', 'B', 'player', 'B')
        combat = CombatState()
        with self.assertRaises(CombatRelationshipStateError):
            commit_attack_declaration(combat, {'a':card}, controller='A', assignments=(assignment,), bands=(AttackBand((('a','a@0'),)),))
        self.assertFalse(combat.attackers)
        self.assertIsNone(card.attacking)


class BandingRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.ReceiverQueryUnionRuntimeTests.session
    add = controls.ReceiverQueryUnionRuntimeTests.add
    prepare = controls.ReceiverQueryUnionRuntimeTests.prepare
    checkpoint = controls.ReceiverQueryUnionRuntimeTests.checkpoint
    resolve = controls.ReceiverQueryUnionRuntimeTests.resolve
    replay = controls.ReceiverQueryUnionRuntimeTests.replay
    binding = controls.ReceiverQueryUnionRuntimeTests.binding
    priority = controls.ReceiverQueryUnionRuntimeTests.priority
    private_replay = controls.ReceiverQueryUnionRuntimeTests.private_replay
    advance = controls.ReceiverQueryUnionRuntimeTests.advance
    block = controls.ReceiverQueryUnionRuntimeTests.block
    blockers = controls.ReceiverQueryUnionRuntimeTests.blockers
    cast = controls.ReceiverQueryUnionRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'banding.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json', ROOT/'tests/fixtures/banding-cards.json'], path)
        cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Ordinary bands', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temp.cleanup()

    def reject(self, s, command, seat='A'):
        before = authoritative_state_hash(s.state)
        result = s.act('pilot:'+seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(s.state))

    def attack(self, s, attacks, bands=None):
        self.advance(s, lambda x: x.state.pending_decision is not None and x.state.pending_decision.kind=='combat.attackers')
        command = {'action_id':'attack', 'attacks':attacks}
        if bands is not None: command['bands']=bands
        self.reject(s, command, 'C')
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)

    def damage(self, s):
        self.advance(s, lambda x: x.state.pending_decision is not None and x.state.pending_decision.kind=='combat.damage')
        principal = s.pending_principals()[0]
        return principal, s.packet(principal,full=True)['decision']['ctx']['combat']

    def assign(self, s, seat, assignments):
        command = {'action_id':'assign_damage', 'assignments':[
            {'source':source.ref,'target':target.ref if hasattr(target,'ref') else target,'amount':amount}
            for source,target,amount in assignments]}
        self.reject(s, command, 'D')
        result = s.act('pilot:'+seat, command); self.assertTrue(result.ok, result.summary)

    def finish(self, s):
        self.advance(s, lambda x: x.state.phase=='postcombat_main')

    def test_all_originals_admit_with_independent_banding_minimum_and_actual_paid_casts(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        for group_index, start in enumerate(range(0,len(NAMES),4)):
            group=NAMES[start:start+4]
            s=self.session(786000+group_index)
            sources=[self.add(s.engine,name,zone='hand') for name in group]
            for source in sources:
                self.assertTrue(self.binding(s,source)['strict_capability_ready'],source.printed_name)
                programs=s.engine.semantics.programs_for_oracle(source.oracle_id)
                program=next(p for p in programs if CAPABILITY in p.capability_dependencies)
                deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
                missing=replace(program,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound=bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'],source.printed_name)
                self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
            self.prepare(s,mana={k:200 for k in 'CWUBRG'});self.checkpoint(s)
            for source in sources:
                self.cast(s,source);self.assertIsNone(self.resolve(s))
                self.assertEqual('battlefield',s.state.cards[source.object_id].zone,source.printed_name)
                self.assertIn('banding',s.engine._combat_keywords(s.state.cards[source.object_id]),source.printed_name)
            self.replay(s,load=True)

    def test_attack_offer_and_band_input_reject_overlap_extra_members_and_different_defenders(self):
        s=self.session(786030);hero=self.add(s.engine,'Benalish Hero');other=self.add(s.engine,'Grizzly Bears')
        third=self.add(s.engine,'Wind Drake');self.add(s.engine,'Generic Band Wall',seat='B')
        for c in (hero,other,third):c.acquired_control_turn_count=-1
        self.prepare(s);self.checkpoint(s)
        self.advance(s,lambda x:x.state.pending_decision is not None and x.state.pending_decision.kind=='combat.attackers')
        ctx=s.packet('pilot:A',full=True)['decision']['ctx']
        self.assertIn(hero.ref,ctx['banding']['banding_attackers'])
        from quorune.choice_forms import build_action_form
        form=build_action_form({'id':'attack','action':'attack'},decision_kind='combat.attackers',context=ctx)
        self.assertTrue(any(field['name']=='bands' and field['control']=='assignment_map' for field in form['fields']))
        for groups,attacks in (([[hero.ref,other.ref,third.ref]],{c.ref:'B' for c in (hero,other,third)}),
                ([[hero.ref,other.ref],[hero.ref]],{hero.ref:'B',other.ref:'B'}),
                ([[hero.ref,other.ref]],{hero.ref:'B',other.ref:'C'}),
                ([[other.ref]],{other.ref:'B'}), ([[hero.ref,third.ref]],{hero.ref:'B'})):
            self.reject(s,{'action_id':'attack','attacks':attacks,'bands':groups})
        self.attack(s,{hero.ref:'B',other.ref:'B'},{hero.ref:hero.ref,other.ref:hero.ref})
        self.assertEqual(1,len(s.state.combat.attack_bands));self.replay(s,load=True)

    def band_combat(self, seed, *, partner='Generic Band Menace', blocker='Generic Bound Body'):
        s=self.session(seed);hero=self.add(s.engine,'Benalish Hero');other=self.add(s.engine,partner)
        defender=self.add(s.engine,blocker,seat='B',ref='band-defender');private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for c in (hero,other):c.acquired_control_turn_count=-1
        self.prepare(s);self.checkpoint(s)
        self.attack(s,{hero.ref:'B',other.ref:'B'},[[hero.ref,other.ref]])
        self.blockers(s)
        return s,hero,other,defender,private

    def test_indirect_menace_and_flying_blocking_preserve_anchor_legality_and_assignment_actor(self):
        for i,partner in enumerate(('Generic Band Menace','Wind Drake')):
            s,hero,other,blocker,private=self.band_combat(786040+i,partner=partner)
            self.block(s,{blocker.ref:other.ref},valid=False)
            self.block(s,{blocker.ref:hero.ref})
            for c in (hero,other):self.assertEqual([blocker.object_id],s.state.combat.blockers.get(c.object_id,[]))
            actor,ctx=self.damage(s);self.assertEqual('pilot:A',actor)
            self.assertEqual('B',ctx['damage_sources'][blocker.ref]['source_controller'])
            self.assign(s,'A',[(hero,blocker,1),(other,blocker,3 if i==0 else 2),(blocker,other,2)])
            self.finish(s)
            self.assertEqual('battlefield',s.state.cards[hero.object_id].zone)
            self.assertEqual(40,s.state.players['B'].life)
            self.private_replay(s,private)

    def test_defensive_banding_changes_chooser_without_attack_band_or_source_control_change(self):
        s=self.session(786050);attacker=self.add(s.engine,'Generic Band Life');attacker.acquired_control_turn_count=-1
        hero=self.add(s.engine,'Benalish Hero',seat='B');wall=self.add(s.engine,'Generic Band Wall',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);self.attack(s,{attacker.ref:'B'});self.blockers(s)
        self.block(s,{hero.ref:attacker.ref,wall.ref:attacker.ref})
        actor,ctx=self.damage(s);self.assertEqual('pilot:B',actor)
        self.assertNotIn(attacker.ref,s.engine._combat_damage_source_options('A'))
        self.assertEqual('A',ctx['damage_sources'][attacker.ref]['source_controller'])
        s=self.replay(s,load=True)
        self.assign(s,'B',[(attacker,wall,4),(hero,attacker,1)])
        self.finish(s);self.assertEqual(44,s.state.players['A'].life)
        self.assertEqual('battlefield',s.state.cards[hero.object_id].zone)
        self.private_replay(s,private)

    def test_trample_spill_still_requires_lethal_when_defender_assigns_damage(self):
        s=self.session(786053);attacker=self.add(s.engine,'Colossal Dreadmaw');attacker.acquired_control_turn_count=-1
        hero=self.add(s.engine,'Benalish Hero',seat='B');wall=self.add(s.engine,'Generic Band Wall',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s);self.attack(s,{attacker.ref:'B'});self.blockers(s)
        self.block(s,{hero.ref:attacker.ref,wall.ref:attacker.ref})
        actor,_ctx=self.damage(s);self.assertEqual('pilot:B',actor)
        self.reject(s,{'action_id':'assign_damage','assignments':[
            {'source':attacker.ref,'target':'B','amount':1},
            {'source':attacker.ref,'target':wall.ref,'amount':5},
            {'source':hero.ref,'target':attacker.ref,'amount':1}]},'B')
        self.assign(s,'B',[(attacker,wall,6),(hero,attacker,1)])
        self.finish(s);self.assertEqual(40,s.state.players['B'].life)
        self.assertEqual('battlefield',s.state.cards[hero.object_id].zone);self.private_replay(s,private)

    def test_blinked_banding_member_leaves_group_and_returned_incarnation_does_not_rejoin(self):
        s=self.session(786054);hero=self.add(s.engine,'Benalish Hero');other=self.add(s.engine,'Grizzly Bears')
        blocker=self.add(s.engine,'Generic Bound Body',seat='B')
        blink=self.add(s.engine,'Cloudshift',zone='hand');self.add(s.engine,'Generic Bound Plains',ref='blink-land')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for c in (hero,other):c.acquired_control_turn_count=-1
        self.prepare(s);self.checkpoint(s);self.attack(s,{hero.ref:'B',other.ref:'B'},[[hero.ref,other.ref]])
        old=hero.logical_object_id;self.cast(s,blink,[hero.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotEqual(old,s.state.cards[hero.object_id].logical_object_id)
        self.assertNotIn(hero.object_id,s.state.combat.attackers)
        self.assertEqual(((other.object_id,other.logical_object_id),),s.state.combat.attack_bands[0].members)
        self.blockers(s);self.block(s,{blocker.ref:other.ref});self.finish(s)
        self.assertEqual('battlefield',s.state.cards[hero.object_id].zone);self.private_replay(s,private)

    def test_lifelink_uses_blocker_controller_when_attacker_chooses_damage(self):
        s,hero,other,blocker,private=self.band_combat(786051,partner='Generic Band Life',blocker='Generic Band Life')
        self.block(s,{blocker.ref:hero.ref});actor,_ctx=self.damage(s);self.assertEqual('pilot:A',actor)
        self.assign(s,'A',[(hero,blocker,1),(other,blocker,4),(blocker,other,4)])
        self.finish(s);self.assertEqual(44,s.state.players['A'].life);self.assertEqual(44,s.state.players['B'].life)
        self.assertEqual('battlefield',s.state.cards[hero.object_id].zone);self.private_replay(s,private)

    def test_current_ability_loss_keeps_group_but_ends_banding_assignment_right(self):
        s=self.session(786052);hero=self.add(s.engine,'Benalish Hero');other=self.add(s.engine,'Grizzly Bears')
        blocker=self.add(s.engine,'Generic Bound Body',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        humble=self.add(s.engine,'Humble',zone='hand')
        self.add(s.engine,'Generic Bound Plains',ref='humble-land-one')
        self.add(s.engine,'Generic Bound Plains',ref='humble-land-two')
        for c in (hero,other):c.acquired_control_turn_count=-1
        self.prepare(s);self.checkpoint(s)
        self.attack(s,{hero.ref:'B',other.ref:'B'},[[hero.ref,other.ref]])
        self.cast(s,humble,[hero.ref]);self.assertIsNone(self.resolve(s))
        self.assertNotIn('banding',s.engine._combat_keywords(s.state.cards[hero.object_id]))
        self.blockers(s);self.block(s,{blocker.ref:hero.ref})
        self.assertEqual([blocker.object_id],s.state.combat.blockers[other.object_id])
        actor,_ctx=self.damage(s);self.assertEqual('pilot:B',actor)
        self.assign(s,'B',[(blocker,other,2)]);self.finish(s)
        self.private_replay(s,private)

    def test_banding_producer_and_assignment_actor_omission_mutants_are_killed(self):
        mutant=BandingRuntimeTests('test_indirect_menace_and_flying_blocking_preserve_anchor_legality_and_assignment_actor')
        with patch('quorune.rules.banding.expanded_block_pairs',side_effect=lambda host,chosen:tuple(chosen)):
            with self.assertRaises(AssertionError):mutant.test_indirect_menace_and_flying_blocking_preserve_anchor_legality_and_assignment_actor()
        mutant=BandingRuntimeTests('test_defensive_banding_changes_chooser_without_attack_band_or_source_control_change')
        with patch('quorune.combat_damage_assignment.combat_assignment_actor',side_effect=lambda snap,p,*rest:p.controller):
            with self.assertRaises(AssertionError):mutant.test_defensive_banding_changes_chooser_without_attack_band_or_source_control_change()
        mutant=BandingRuntimeTests('test_blocking_a_band_triggers_once_for_blocker_and_per_matching_creature')
        with patch('quorune.trigger_discovery.block_trigger_already_collected',return_value=False):
            with self.assertRaises(AssertionError):mutant.test_blocking_a_band_triggers_once_for_blocker_and_per_matching_creature()

    def test_verified_banding_withdrawal_is_residual_and_quality_bands_remain_unsupported(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index, discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from test_bound_effect_programs import record
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        row=next(r for r in raw['capabilities'] if r['id']==CAPABILITY)
        row.update(status='blocked',blockers=['withdrawn ordinary Banding owner'],dependency_fail_closed_status='not_run')
        declarations=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        registry=CapabilityRegistry(raw)
        index=build_index(registry_value=raw,declaration_source=declarations,discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir=compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,name)
            blockers=[b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAPABILITY+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)
        for text in ('Bands with other Wolves','Banding with other green creatures','Banding {2}'):
            card=replace(record(text,'Creature — Wolf'),keywords=(text,),power='2',toughness='2')
            ir=compile_oracle_card(card,capability_registry=self.registry,capability_profile='commander_review')
            self.assertNotEqual('exact',ir.status,text)

    def test_current_granted_banding_is_offered_and_first_strike_band_uses_both_steps(self):
        s=self.session(786055);source=self.add(s.engine,'Generic Band Grant');source.acquired_control_turn_count=-1
        first=self.add(s.engine,'Pikemen');first.acquired_control_turn_count=-1
        wall=self.add(s.engine,'Generic Band Wall',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s);self.checkpoint(s)
        row=next(r for r in s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            if r['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':row['id'],'pay':'auto'});self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self.resolve(s));self.assertIn('banding',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.attack(s,{source.ref:'B',first.ref:'B'},[[source.ref,first.ref]])
        self.blockers(s);self.block(s,{wall.ref:first.ref})
        actor,ctx=self.damage(s);self.assertEqual('pilot:A',actor)
        self.assertEqual(2,ctx['damage_step'],ctx)
        self.assertEqual(1,s.state.cards[wall.object_id].marked_damage)
        self.assign(s,'A',[(source,wall,2)])
        self.finish(s);self.assertEqual(3,s.state.cards[wall.object_id].marked_damage)
        self.private_replay(s,private)

    def test_blocking_a_band_triggers_once_for_blocker_and_per_matching_creature(self):
        s,hero,other,blocker,private=self.band_combat(786056,partner='Wind Drake',blocker='Generic Band Block Life')
        self.block(s,{blocker.ref:hero.ref})
        decision=s.state.pending_decision
        count=len(s.packet('pilot:B',full=True)['decision']['ctx']['triggers']) if decision and decision.kind=='trigger.order' else len(s.state.stack)
        self.assertEqual(1,count,'CR509.3a counts blocking creatures, not their band relationships')
        self.assertIsNone(self.resolve(s));self.assertEqual(41,s.state.players['B'].life)
        self.private_replay(s,private)
        s=self.session(786057);first=self.add(s.engine,'Mesa Pegasus');second=self.add(s.engine,'Wind Drake')
        blocker=self.add(s.engine,'Generic Band Flying Block Life',seat='B')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for c in (first,second):c.acquired_control_turn_count=-1
        self.prepare(s);self.checkpoint(s)
        self.attack(s,{first.ref:'B',second.ref:'B'},[[first.ref,second.ref]])
        self.blockers(s);self.block(s,{blocker.ref:first.ref})
        self.assertEqual('trigger.order',s.state.pending_decision.kind)
        rows=s.packet('pilot:B',full=True)['decision']['ctx']['triggers'];self.assertEqual(2,len(rows))
        result=s.act('pilot:B',{'action_id':'order','triggers':[r['id'] for r in rows]})
        self.assertTrue(result.ok,result.summary);self.assertIsNone(self.resolve(s))
        self.assertEqual(42,s.state.players['B'].life);self.private_replay(s,private)

    def test_static_banding_grant_keeps_group_after_source_bounce_and_cannot_strip_minimum(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        s=self.session(786058);anthem=self.add(s.engine,'Generic Band Anthem',zone='hand')
        hero=self.add(s.engine,'Grizzly Bears');other=self.add(s.engine,'Wind Drake')
        blocker=self.add(s.engine,'Generic Bound Body',seat='B');bounce=self.add(s.engine,'Boomerang',zone='hand')
        private=self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        for i in range(2):self.add(s.engine,'Island',ref='band-anthem-island-'+str(i))
        for c in (hero,other):c.acquired_control_turn_count=-1
        self.prepare(s,mana={'W':2});self.checkpoint(s)
        self.assertTrue(self.binding(s,anthem)['strict_capability_ready'])
        program=next(p for p in s.engine.semantics.programs_for_oracle(anthem.oracle_id) if CAPABILITY in p.capability_dependencies)
        deps=tuple(c for c in program.capability_dependencies if c!=CAPABILITY)
        stripped=replace(program,coverage=tuple(c for c in program.coverage if c!='banding'),
            capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
        bound=bind_semantic_program_runtime(stripped,capability_registry=self.registry,profile='commander_review')
        self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAPABILITY,bound['blockers'])
        self.cast(s,anthem,payment={'W':2});self.assertIsNone(self.resolve(s))
        for c in (hero,other):self.assertIn('banding',s.engine._combat_keywords(s.state.cards[c.object_id]))
        self.attack(s,{hero.ref:'B',other.ref:'B'},[[hero.ref,other.ref]])
        self.cast(s,bounce,[anthem.ref]);self.assertIsNone(self.resolve(s))
        self.assertEqual(1,len(s.state.combat.attack_bands))
        for c in (hero,other):self.assertNotIn('banding',s.engine._combat_keywords(s.state.cards[c.object_id]))
        self.blockers(s);self.block(s,{blocker.ref:hero.ref})
        self.assertEqual([blocker.object_id],s.state.combat.blockers[other.object_id])
        actor,_ctx=self.damage(s);self.assertEqual('pilot:B',actor)
        self.assign(s,'B',[(blocker,other,2)]);self.finish(s);self.private_replay(s,private)
