from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.counter_permission import COUNTER_PROHIBITION_CAPABILITY as CAP
from quorune.counter_state import player_counter_snapshot
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
import test_life_gain_prohibitions as controls
import test_counter_permissions as models


NAMES = ("Melira's Keepers", 'Tatterkite', 'Melira, Sylvok Outcast', 'Blightbeetle', 'Solemnity')


class CounterProhibitionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = controls.LifeGainProhibitionRuntimeTests.session
    add = controls.LifeGainProhibitionRuntimeTests.add
    prepare = controls.LifeGainProhibitionRuntimeTests.prepare
    checkpoint = controls.LifeGainProhibitionRuntimeTests.checkpoint
    resolve = controls.LifeGainProhibitionRuntimeTests.resolve
    replay = controls.LifeGainProhibitionRuntimeTests.replay
    private_replay = controls.LifeGainProhibitionRuntimeTests.private_replay
    priority = controls.LifeGainProhibitionRuntimeTests.priority
    actions = controls.LifeGainProhibitionRuntimeTests.actions
    cast = controls.LifeGainProhibitionRuntimeTests.cast
    reject = controls.LifeGainProhibitionRuntimeTests.reject
    binding = controls.LifeGainProhibitionRuntimeTests.binding
    activation_command = controls.LifeGainProhibitionRuntimeTests.activation_command
    activate = controls.LifeGainProhibitionRuntimeTests.activate
    resume = controls.LifeGainProhibitionRuntimeTests.resume
    advance = controls.LifeGainProhibitionRuntimeTests.advance
    settle = controls.LifeGainProhibitionRuntimeTests.settle

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'counter-prohibitions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/counter-prohibition-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Counter placement permissions',[
            DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_originals_bind_whole_sources_with_required_current_permission_authority(self):
        for i,name in enumerate(NAMES):
            with self.subTest(name=name):
                s = self.session(122500000+i)
                source = self.add(s.engine,name,zone='hand')
                binding = self.binding(s,source)
                self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
                programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
                self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in programs))
                p = next(p for p in programs if CAP in p.capability_dependencies)
                deps = tuple(c for c in p.capability_dependencies if c != CAP)
                missing = replace(p,capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
                bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
                self.assertFalse(bound['strict'])
                self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])
        for name in ('Blossombind','Suncleanser','Melira, the Living Cure'):
            s = self.session(122500009)
            self.assertFalse(self.binding(s,self.add(s.engine,name,zone='hand'))['strict_capability_ready'])

    def test_verified_counter_permission_withdrawal_is_a_specific_rules_block(self):
        value = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in value['capabilities'] if r['id'] == CAP).update(status='blocked',
            blockers=['withdrawn counter-placement permission'],dependency_fail_closed_status='not_run')
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_permission_gate_omission_is_detected(self):
        method = 'test_authoritative_prepare_does_not_offer_replacements_for_prohibited_effects'
        with patch('quorune.counter_permission.CounterPermissionSnapshot.allows',return_value=True):
            with self.assertRaises(AssertionError):
                getattr(models.CounterPermissionEventTests(method),method)()

    def test_paid_self_prohibitions_ignore_quantity_replacements_and_humble_restores_placement(self):
        for i,(name,payment) in enumerate(((NAMES[0],{'C':4,'G':1}),(NAMES[1],{'C':3}))):
            with self.subTest(name=name):
                s = self.session(122500020+i)
                source = self.add(s.engine,name,zone='hand')
                growths = [self.add(s.engine,'Battlegrowth',zone='hand',ref='self-growth-'+str(j)) for j in range(3)]
                self.add(s.engine,'Hardened Scales')
                forest = self.add(s.engine,'Forest')
                humble = self.add(s.engine,'Humble',zone='hand')
                private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(s,mana={'C':6,'G':3,'W':1})
                self.checkpoint(s)
                self.cast(s,source,payment=payment)
                self.assertIsNone(self.settle(s))
                self.cast(s,growths[0],[source.ref],payment={'G':1})
                self.assertIsNone(self.settle(s))
                self.assertEqual({},dict(s.state.cards[source.object_id].counters))
                self.cast(s,humble,[source.ref],payment={'C':1,'W':1})
                self.assertIsNone(self.settle(s))
                self.cast(s,growths[1],[source.ref],payment={'G':1})
                self.assertIsNone(self.settle(s))
                self.assertEqual(2,s.state.cards[source.object_id].counters['+1/+1'])
                self.advance(s,lambda current:current.state.active_player == 'B'
                    and getattr(current.state.pending_decision,'kind',None) == 'priority')
                self.activate(s,forest)
                self.cast(s,growths[2],[source.ref],payment={'G':1})
                self.assertIsNone(self.settle(s))
                self.assertEqual(2,s.state.cards[source.object_id].counters['+1/+1'])
                if name == 'Tatterkite':
                    self.assertIn('flying',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.private_replay(s,private)

    def test_paid_solemnity_blocks_entry_and_energy_without_skipping_private_draw_or_loyalty(self):
        s = self.session(122500030)
        source = self.add(s.engine,'Solemnity',zone='hand')
        worker = self.add(s.engine,'Arcbound Worker',zone='hand')
        refiner = self.add(s.engine,'Rogue Refiner',zone='hand')
        jace = self.add(s.engine,'Jace Beleren',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':5,'W':1,'G':1,'U':3})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'W':1})
        self.assertIsNone(self.settle(s))
        self.cast(s,worker,payment={'C':1})
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[worker.object_id].zone)
        self.assertFalse(s.state.cards[worker.object_id].counters)
        before = set(s.state.players['A'].zones['hand'])
        self.cast(s,refiner,payment={'C':1,'G':1,'U':1})
        self.assertIsNone(self.settle(s))
        self.assertEqual(0,player_counter_snapshot(s.state.players['A']).get('energy',0))
        drawn = set(s.state.players['A'].zones['hand'])-before
        self.assertEqual(1,len(drawn))
        drawn_ref = s.state.cards[next(iter(drawn))].ref
        self.assertIn(drawn_ref,json.dumps(s.packet('pilot:A',full=True)))
        for seat in 'BCD':self.assertNotIn(drawn_ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.cast(s,jace,payment={'C':1,'U':2})
        self.assertIsNone(self.settle(s))
        self.assertEqual(3,s.state.cards[jace.object_id].counters['loyalty'])
        self.private_replay(s,private)

    def test_paid_melira_blocks_poison_and_counter_cost_then_humble_restores_legal_payment(self):
        s = self.session(122500050)
        source = self.add(s.engine,'Melira, Sylvok Outcast',zone='hand')
        price = self.add(s.engine,'Fugitive Wizard',ref='melira-price')
        victim = self.add(s.engine,'Fugitive Wizard',seat='B',ref='melira-victim')
        infect = self.add(s.engine,'Plague Stinger',seat='B')
        poison = self.add(s.engine,'Prologue to Phyresis',seat='B',zone='hand')
        sting = self.add(s.engine,'Lethal Sting',zone='hand')
        humble = self.add(s.engine,'Humble',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':1,'U':1})
        self.prepare(s,mana={'C':4,'G':1,'B':1,'W':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':1,'G':1})
        self.assertIsNone(self.settle(s))
        self.assertNotIn('infect',s.engine._combat_keywords(s.state.cards[infect.object_id]))
        command = {'action_id':'cast:'+sting.ref,'targets':[victim.ref],
            'counter_cost_card':price.ref,'pay':'manual','payment':{'C':2,'B':1}}
        self.assertFalse(any(row['id'] == command['action_id'] for row in self.actions(s)))
        self.reject(s,command)
        self.assertEqual('hand',s.state.cards[sting.object_id].zone)
        self.assertFalse(s.state.cards[price.object_id].counters)
        before = set(s.state.players['B'].zones['hand'])
        self.cast(s,poison,seat='B',payment={'C':1,'U':1})
        self.assertIsNone(self.settle(s))
        self.assertEqual([0,0,1,1],[player_counter_snapshot(s.state.players[seat]).get('poison',0) for seat in 'ABCD'])
        drawn = set(s.state.players['B'].zones['hand'])-before
        self.assertEqual(1,len(drawn))
        drawn_ref = s.state.cards[next(iter(drawn))].ref
        self.assertIn(drawn_ref,json.dumps(s.packet('pilot:B',full=True)))
        for seat in 'ACD':self.assertNotIn(drawn_ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.cast(s,humble,[source.ref],payment={'C':1,'W':1})
        self.assertIsNone(self.settle(s))
        self.assertIn('infect',s.engine._combat_keywords(s.state.cards[infect.object_id]))
        row = next(row for row in self.actions(s) if row['id'] == command['action_id'])
        candidates = row['cost_options'][0]['choice_schema']['counter_cost_card']['legal_refs']
        self.assertIn(price.ref,candidates)
        self.reject(s,command,seat='D')
        result = s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('graveyard',s.state.cards[price.object_id].zone)
        self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[victim.object_id].zone)
        self.assertEqual(0,s.state.players['A'].mana_pool['B'])
        self.private_replay(s,private)

    def test_real_graft_does_not_offer_impossible_transfer_and_keeps_source_counter(self):
        s = self.session(122500060)
        graft = self.add(s.engine,'Simic Initiate',zone='hand')
        recipient = self.add(s.engine,'Tatterkite',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':3,'G':1})
        self.checkpoint(s)
        self.cast(s,graft,payment={'G':1})
        self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[graft.object_id].counters['+1/+1'])
        self.cast(s,recipient,payment={'C':3})
        self.assertIsNone(self.settle(s))
        self.assertEqual(1,s.state.cards[graft.object_id].counters['+1/+1'])
        self.assertFalse(s.state.cards[recipient.object_id].counters)
        self.private_replay(s,private)

    def test_real_infect_keeps_lifelink_and_wither_keeps_damage_without_forbidden_counters(self):
        for i,wither in enumerate((False,True)):
            with self.subTest(wither=wither):
                s = self.session(122500090+i)
                source = self.add(s.engine,'Sickle Ripper' if wither else 'Plague Stinger')
                source.acquired_control_turn_count = -1
                blocker = self.add(s.engine,"Melira's Keepers",seat='B') if wither else None
                if not wither:
                    self.add(s.engine,'Solemnity')
                    collar = self.add(s.engine,'Basilisk Collar')
                private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
                self.prepare(s,mana={'C':2})
                self.checkpoint(s)
                if not wither:
                    self.activate(s,collar,targets=[source.ref])
                    self.assertIsNone(self.settle(s))
                    self.assertIn('lifelink',s.engine._combat_keywords(s.state.cards[source.object_id]))
                self.advance(s,lambda current:getattr(current.state.pending_decision,'kind',None) == 'combat.attackers')
                command = {'action_id':'attack','atk':{source.ref:'B'}}
                self.reject(s,command,seat='D')
                result = s.act('pilot:A',command)
                self.assertTrue(result.ok,result.summary)
                if wither:
                    self.advance(s,lambda current:getattr(current.state.pending_decision,'kind',None) == 'combat.blockers')
                    command = {'action_id':'block','blocks':{blocker.ref:source.ref}}
                    self.reject(s,command,seat='D')
                    result = s.act('pilot:B',command)
                    self.assertTrue(result.ok,result.summary)
                self.advance(s,lambda current:current.state.phase == 'postcombat_main')
                self.assertEqual(40,s.state.players['B'].life)
                self.assertEqual(0,player_counter_snapshot(s.state.players['B']).get('poison',0))
                self.assertEqual(40 if wither else 41,s.state.players['A'].life)
                if wither:
                    self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
                    self.assertEqual('battlefield',s.state.cards[blocker.object_id].zone)
                    self.assertFalse(s.state.cards[blocker.object_id].counters)
                    self.assertEqual(0,s.state.cards[blocker.object_id].marked_damage)
                self.private_replay(s,private)

    def test_solemnity_allows_real_suspend_counters_outside_the_battlefield(self):
        s = self.session(122500070)
        source = self.add(s.engine,'Solemnity',zone='hand')
        lotus = self.add(s.engine,'Lotus Bloom',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':2,'W':1})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'W':1})
        self.assertIsNone(self.settle(s))
        row = next(row for row in self.actions(s) if row.get('action') == 'suspend' and row.get('card') == lotus.ref)
        command = {'action_id':row['id'],'pay':'auto'}
        self.reject(s,command,seat='D')
        result = s.act('pilot:A',command)
        self.assertTrue(result.ok,result.summary)
        self.assertEqual('exile',s.state.cards[lotus.object_id].zone)
        self.assertEqual(3,s.state.cards[lotus.object_id].counters['time'])
        self.private_replay(s,private)

    def test_real_entry_copy_uses_selected_self_prohibition_and_decline_keeps_entry_counter(self):
        s = self.session(122500080)
        self.add(s.engine,'Renata, Called to the Hunt')
        target = self.add(s.engine,'Tatterkite',zone='hand')
        clones = [self.add(s.engine,'Clone',zone='hand',ref='counter-clone-'+str(i)) for i in range(2)]
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        self.prepare(s,mana={'C':9,'U':2})
        self.checkpoint(s)
        self.cast(s,target,payment={'C':3})
        self.assertIsNone(self.settle(s))
        self.assertFalse(s.state.cards[target.object_id].counters)
        for i,clone in enumerate(clones):
            self.cast(s,clone,payment={'C':3,'U':1})
            decision = self.resolve(s)
            self.assertIsNotNone(decision)
            self.assertEqual('replacement.order',decision.kind)
            s = self.resume(s)
            options = s.packet('pilot:A',full=True)['decision']['ctx']['options']
            option = next(row for row in options if (row['id'].startswith('decline:') if i
                else target.ref in row['id'] and not row['id'].startswith('decline:')))
            command = {'action_id':'choose','replacement':option['id']}
            self.reject(s,command,seat='D')
            result = s.act('pilot:A',command)
            self.assertTrue(result.ok,result.summary)
            self.assertIsNone(self.settle(s))
            self.assertEqual(i,s.state.cards[clone.object_id].counters.get('+1/+1',0))
            self.assertEqual('battlefield',s.state.cards[clone.object_id].zone)
            self.assertEqual('Clone' if i else 'Tatterkite',s.engine.display_name(clone.object_id))
        for seat in 'ABCD':
            self.assertNotIn('_counter_permissions',json.dumps(s.packet('pilot:'+seat,full=True)))
        self.private_replay(s,private)

    def test_paid_blightbeetle_uses_recipient_current_controller_and_retains_existing_counters(self):
        s = self.session(122500040)
        source = self.add(s.engine,'Blightbeetle',zone='hand')
        own = self.add(s.engine,'Fugitive Wizard',ref='own-wizard')
        other = self.add(s.engine,'Fugitive Wizard',seat='B',ref='other-wizard')
        growths = [self.add(s.engine,'Battlegrowth',zone='hand',ref='beetle-growth-'+str(j)) for j in range(4)]
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'U':2})
        self.prepare(s,mana={'C':1,'B':1,'G':4})
        self.checkpoint(s)
        self.cast(s,source,payment={'C':1,'B':1})
        self.assertIsNone(self.settle(s))
        self.assertIn('protection',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.reject(s,{'action_id':'cast:'+growths[0].ref,'targets':[source.ref],'pay':'auto'})
        for spell,target,amount in ((growths[0],own,1),(growths[1],other,0)):
            self.cast(s,spell,[target.ref],payment={'G':1})
            self.assertIsNone(self.settle(s))
            self.assertEqual(amount,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2})
        self.assertIsNone(self.settle(s))
        self.assertEqual('B',s.state.cards[source.object_id].controller)
        for spell,target,amount in ((growths[2],own,1),(growths[3],other,1)):
            self.cast(s,spell,[target.ref],payment={'G':1})
            self.assertIsNone(self.settle(s))
            self.assertEqual(amount,s.state.cards[target.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)


if __name__ == '__main__':
    unittest.main()
