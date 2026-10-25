from __future__ import annotations

from dataclasses import FrozenInstanceError,replace
from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from damage_replacement_support import DamageReplacementPipelineBase
from quorune.life_gain_permission import (ALLOW_ALL_LIFE_GAIN,FixedLifeGainProhibitionSpec,
    LifeGainPermissionSnapshot)
from quorune.life_change import (LifeChangeRequest,commit_life_change_batch,
    prepare_life_change_batch,validate_life_change_batch)
from quorune.replacement import AddAmount,MultiplyAmount
from test_life_change import replacement
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_land_search_view_equivalence as native
import test_ability_label_composition as journeys
from quorune.rules.evidence_storage import read_evidence_document


class LifeGainPermissionKernelTests(DamageReplacementPipelineBase):
    def test_closed_global_and_opponent_scopes_use_current_controller_and_immutable_snapshots(self):
        opponents = FixedLifeGainProhibitionSpec('opponent')
        self.assertEqual(opponents,FixedLifeGainProhibitionSpec.from_dict(opponents.to_dict()))
        a = opponents.permissions('A',tuple('ABCD'))
        b = opponents.permissions('B',tuple('ABCD'))
        self.assertEqual(tuple('BCD'),a.denied_players)
        self.assertEqual(tuple('ACD'),b.denied_players)
        self.assertTrue(a.allows_gain('A'))
        self.assertFalse(b.allows_gain('A'))
        self.assertEqual(tuple('ABCD'),FixedLifeGainProhibitionSpec('any').permissions('B',tuple('ABCD')).denied_players)
        with self.assertRaises(FrozenInstanceError):
            a.denied_players = ()
        for value in ('source_owner','self',[],None):
            with self.assertRaises(ValueError):FixedLifeGainProhibitionSpec(value)
        for value in ({'schema_version':True,'affected_player_relation':'any'},
                {'schema_version':2,'affected_player_relation':'any'},
                {'schema_version':1,'affected_player_relation':'any','future':True}):
            with self.assertRaises(ValueError):FixedLifeGainProhibitionSpec.from_dict(value)
        for players in (('B','A'),('A','A'),['A'],(False,)):
            with self.assertRaises(ValueError):LifeGainPermissionSnapshot(players)

    def test_prohibited_gain_precedes_replacement_choice_and_commits_no_life_change(self):
        engine = self.session(119_700_001).engine
        before = engine.state.players['B'].life
        effects = (replacement('gain-add',AddAmount(field='amount',amount=1)),
            replacement('gain-double',MultiplyAmount(field='amount',factor=2)))
        prepared = prepare_life_change_batch(engine,
            (LifeChangeRequest('prohibited-gain','B',3),),effects=effects,
            require_all_selections=False,gain_permissions=LifeGainPermissionSnapshot(('B',)))
        self.assertIsNone(prepared.pending)
        self.assertEqual((),prepared.journal)
        self.assertEqual((3,0),(prepared.records[0].requested_amount,prepared.records[0].amount))
        validate_life_change_batch(engine,prepared)
        result = commit_life_change_batch(engine,prepared)
        self.assertEqual(before,engine.state.players['B'].life)
        self.assertEqual((),result.changed_players)

    def test_prohibition_does_not_block_loss_or_another_players_gain(self):
        engine = self.session(119_700_002).engine
        a,b = (engine.state.players[p].life for p in 'AB')
        prepared = prepare_life_change_batch(engine,(
            LifeChangeRequest('denied','B',3),LifeChangeRequest('loss','B',-2),
            LifeChangeRequest('allowed','A',3)),
            effects=(replacement('double',MultiplyAmount(field='amount',factor=2)),),
            gain_permissions=LifeGainPermissionSnapshot(('B',)))
        commit_life_change_batch(engine,prepared)
        self.assertEqual((a+6,b-2),(engine.state.players['A'].life,engine.state.players['B'].life))
        self.assertEqual({'denied':0,'loss':2,'allowed':6},{r.event_id:r.amount for r in prepared.records})

    def test_mixed_gain_replacement_continuation_contains_only_permitted_events(self):
        engine = self.session(119_700_005).engine
        requests = (LifeChangeRequest('denied-choice','B',3),LifeChangeRequest('allowed-choice','A',3))
        effects = (replacement('mixed-add',AddAmount(field='amount',amount=1)),
            replacement('mixed-double',MultiplyAmount(field='amount',factor=2)))
        permission = LifeGainPermissionSnapshot(('B',))
        pending = prepare_life_change_batch(engine,requests,effects=effects,
            gain_permissions=permission,require_all_selections=False)
        self.assertIsNotNone(pending.pending)
        self.assertEqual(('allowed-choice',),tuple(e.event_id for e in pending.events))
        complete = prepare_life_change_batch(engine,requests,effects=effects,
            gain_permissions=permission,selections=('mixed-add',))
        self.assertEqual({'denied-choice':0,'allowed-choice':8},{r.event_id:r.amount for r in complete.records})
        before = {p:engine.state.players[p].life for p in 'AB'}
        commit_life_change_batch(engine,complete)
        self.assertEqual((before['A']+8,before['B']),(engine.state.players['A'].life,engine.state.players['B'].life))

    def test_no_prohibition_keeps_original_event_order_and_replacement_journal(self):
        engine = self.session(119_700_003).engine
        requests = (LifeChangeRequest('gain','B',3),LifeChangeRequest('loss','A',-2))
        effects = (replacement('add',AddAmount(field='amount',amount=1)),
            replacement('double',MultiplyAmount(field='amount',factor=2)))
        old = prepare_life_change_batch(engine,requests,effects=effects,selections=('add',))
        explicit = prepare_life_change_batch(engine,requests,effects=effects,selections=('add',),
            gain_permissions=ALLOW_ALL_LIFE_GAIN)
        self.assertEqual(old,explicit)
        self.assertEqual(('gain','loss'),tuple(e.event_id for e in old.events))
        self.assertEqual((8,2),tuple(r.amount for r in old.records))

    def test_exact_static_frames_and_current_scope_are_required_by_the_descriptor(self):
        from quorune.compiler.life_templates import static_life_gain_prohibition_handler
        from quorune.life_gain_permission import (decode_life_gain_prohibition,life_gain_prohibition_payload_dependencies,
            LIFE_GAIN_PROHIBITION_CAPABILITY)
        from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
        for text,relation in (("Players can't gain life.",'any'),("Your opponents can't gain life.",'opponent')):
            result = static_life_gain_prohibition_handler(text)
            self.assertIsNotNone(result)
            self.assertEqual(relation,decode_life_gain_prohibition(result[1]).affected_player_relation)
            self.assertEqual((LIFE_GAIN_PROHIBITION_CAPABILITY,),life_gain_prohibition_payload_dependencies(result[1],coverage=(CURRENT_ABILITY_FRAGMENT_COVERAGE,)))
            with self.assertRaises(ValueError):life_gain_prohibition_payload_dependencies(result[1])
            for change in ({'schema_version':True},{'event':'life.change'},{'future':1}):
                with self.assertRaises(ValueError):decode_life_gain_prohibition({**result[1],**change})
        for text in ("You can't gain life.","Players can't gain life this turn.",
                "If you control a creature, your opponents can't gain life.",
                "Your opponents can't gain life. Draw a card."):
            self.assertIsNone(static_life_gain_prohibition_handler(text))

    def test_lifelink_permission_is_applied_before_damage_result_replacement(self):
        from test_damage_result_events import DamageResultEventTests
        from quorune.damage import prepare_damage_batch
        from quorune.damage_results import prepare_damage_results,plan_damage_result_commit,commit_damage_result_plan
        engine = self.session(119_700_004).engine
        source = DamageResultEventTests.token(self,engine,'A','Permission lifelink witness',keywords=('Lifelink',))
        proposal = DamageResultEventTests.proposal(engine,source,'B',2,event_id='permission-lifelink')
        damage = prepare_damage_batch(engine,(proposal,))
        before = {seat:engine.state.players[seat].life for seat in 'AB'}
        result = prepare_damage_results(engine,damage.events,effects=(),gain_permissions=LifeGainPermissionSnapshot(('A',)))
        self.assertFalse(any(child.kind == 'life.change' and child.payload['direction'] == 'gain'
            for root in result.events for child in root.children))
        commit_damage_result_plan(engine,plan_damage_result_commit(engine,result))
        self.assertEqual((before['A'],before['B']-2),(engine.state.players['A'].life,engine.state.players['B'].life))

    def test_fresh_life_and_damage_gate_omissions_are_detected(self):
        method = 'test_prohibited_gain_precedes_replacement_choice_and_commits_no_life_change'
        with patch('quorune.life_change._permission_events',side_effect=lambda events,permissions:(tuple(events),())):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()
        from quorune import damage_results
        original = damage_results._append_accumulated_player_results
        method = 'test_lifelink_permission_is_applied_before_damage_result_replacement'
        with patch('quorune.damage_results._append_accumulated_player_results',side_effect=lambda state,permissions:original(state,ALLOW_ALL_LIFE_GAIN)):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


NAMES = ('Giant Cindermaw',"Knight of Dusk's Shadow",'Erebos, God of the Dead','Tibalt, Rakish Instigator')


class LifeGainProhibitionRuntimeTests(unittest.TestCase):
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
        cls.temp = tempfile.TemporaryDirectory()
        path = Path(cls.temp.name)/'life-prohibitions.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/life-gain-prohibition-cards.json'],path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Static life-gain prohibitions',[DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temp.cleanup()

    def settle(self,s,*,target=None):
        for _ in range(100):
            d = s.state.pending_decision
            if d is None:s.engine.pump();continue
            principal = s.pending_principals()[0]
            seat = principal.split(':')[-1]
            packet = s.packet(principal,full=True)
            if d.kind == 'priority':
                if not s.state.stack:return None
                command = {'action_id':'pass'}
            elif d.kind == 'trigger.order':
                command = {'action_id':'order','triggers':[r['id'] for r in packet['decision']['ctx']['triggers']]}
            elif d.kind == 'replacement.order':
                options = packet['decision']['ctx']['options']
                self.assertTrue(options)
                command = {'action_id':'choose','replacement':options[0]['id']}
                self.reject(s,command,seat='D' if seat != 'D' else 'C')
            elif d.kind == 'semantic.target' and target is not None:
                command = {'action_id':'choose','targets':[target]}
                self.reject(s,command,seat='D' if seat != 'D' else 'C')
            else:return d
            result = s.act(principal,command);self.assertTrue(result.ok,result.summary)
        self.fail('Life-gain native stack did not settle')

    def heal(self,s,spell,*,seat='A',gain=0,payment=None):
        before = s.state.players[seat].life
        hand = set(s.state.players[seat].zones['hand'])
        self.cast(s,spell,seat=seat,payment={'C':1,'W':1} if payment is None else payment)
        self.assertIsNone(self.settle(s))
        self.assertEqual(before+gain,s.state.players[seat].life)
        drawn = set(s.state.players[seat].zones['hand'])-hand
        self.assertEqual(1,len(drawn),'A prohibited gain must not skip the draw instruction')
        ref = s.state.cards[next(iter(drawn))].ref
        for other in 'ABCD':
            if other != seat:self.assertNotIn(ref,json.dumps(s.packet('pilot:'+other,full=True)))
        self.assertIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))

    def test_originals_normally_bind_complete_sources_and_current_permission_scopes(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.life_gain_permission import LIFE_GAIN_PROHIBITION_CAPABILITY as CAP,LIFE_GAIN_PROHIBITION_EVENT
        for i,name in enumerate(NAMES):
            s = self.session(119710000+i);source = self.add(s.engine,name,zone='hand')
            binding = self.binding(s,source)
            self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
            programs = s.engine.semantics.programs_for_oracle(source.oracle_id)
            self.assertTrue(all(p.trust_level == 'trusted' and not p.requires_arbiter for p in programs))
            p = next(p for p in programs if p.event == LIFE_GAIN_PROHIBITION_EVENT)
            deps = tuple(c for c in p.capability_dependencies if c != CAP)
            missing = replace(p,capability_dependencies=deps,capability_closure=self.registry.closure(deps,profile='commander_review').to_dict() if deps else None)
            bound = bind_semantic_program_runtime(missing,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(bound['strict']);self.assertIn('capability:undeclared_runtime_dependency:'+CAP,bound['blockers'])

    def test_real_knight_control_and_departure_change_gain_scope_without_skipping_draws(self):
        s = self.session(119710010);source = self.add(s.engine,NAMES[1],zone='hand')
        own = [self.add(s.engine,'Revitalize',zone='hand',ref='knight-own-heal-'+str(i)) for i in range(3)]
        other = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='knight-foreign-heal-'+str(i)) for i in range(2)]
        own_listener = self.add(s.engine,'Bloodthirsty Aerialist',ref='knight-own-listener')
        other_listener = self.add(s.engine,'Bloodthirsty Aerialist',seat='B',ref='knight-other-listener')
        steal = self.add(s.engine,'Control Magic',seat='B',zone='hand')
        self.add(s.engine,'Vedalken Orrery',seat='B')
        bounce = self.add(s.engine,'Unsummon',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':4,'U':2,'W':2})
        self.prepare(s,mana={'C':5,'B':2,'W':3,'U':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':1,'B':1});self.assertIsNone(self.settle(s))
        self.assertIn('menace',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.activate(s,source);self.assertIsNone(self.settle(s))
        self.assertEqual(3,s.engine._numeric_stat(source.object_id,'power'))
        self.heal(s,own[0],gain=3);self.heal(s,other[0],seat='B',gain=0)
        self.assertEqual(0,s.state.cards[other_listener.object_id].counters.get('+1/+1',0))
        self.cast(s,steal,[source.ref],seat='B',payment={'C':2,'U':2});self.assertIsNone(self.settle(s))
        self.assertEqual(('A','B'),(s.state.cards[source.object_id].owner,s.state.cards[source.object_id].controller))
        s = self.resume(s)
        self.heal(s,own[1],gain=0);self.heal(s,other[1],seat='B',gain=3)
        self.cast(s,bounce,[source.ref],payment={'U':1});self.assertIsNone(self.settle(s))
        self.assertEqual('hand',s.state.cards[source.object_id].zone)
        self.heal(s,own[2],gain=3)
        self.assertEqual((46,43),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual((2,1),(s.state.cards[own_listener.object_id].counters.get('+1/+1',0),s.state.cards[other_listener.object_id].counters.get('+1/+1',0)))
        self.private_replay(s,private)

    def test_real_global_source_blocks_doublers_and_lifelink_then_humble_revokes_it(self):
        s = self.session(119710020);source = self.add(s.engine,NAMES[0],zone='hand')
        heals = [self.add(s.engine,'Revitalize',zone='hand',ref='global-own-heal-'+str(i)) for i in range(2)]
        other_heal = self.add(s.engine,'Revitalize',seat='B',zone='hand')
        hawk = self.add(s.engine,"Healer's Hawk");hawk.acquired_control_turn_count = -1
        for i in range(2):self.add(s.engine,'Boon Reflection',ref='global-double-'+str(i))
        listener = self.add(s.engine,'Bloodthirsty Aerialist')
        humble = self.add(s.engine,'Humble',zone='hand')
        lands = [self.add(s.engine,'Plains',ref='global-white-land-'+str(i)) for i in range(4)]
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':1,'W':1})
        self.prepare(s,mana={'C':3,'R':1,'W':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.assertIn('trample',s.engine._combat_keywords(s.state.cards[source.object_id]))
        self.heal(s,heals[0],gain=0);self.heal(s,other_heal,seat='B',gain=0)
        self.assertEqual(0,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:A',{'action_id':'attack','atk':{hawk.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual((40,39),(s.state.players['A'].life,s.state.players['B'].life))
        for land in lands[:2]:self.activate(s,land)
        self.cast(s,humble,[source.ref],payment={'W':2});self.assertIsNone(self.settle(s))
        self.assertNotIn('trample',s.engine._combat_keywords(s.state.cards[source.object_id]))
        for land in lands[2:]:self.activate(s,land)
        self.cast(s,heals[1],payment={'W':2})
        decision = self.resolve(s);self.assertIsNotNone(decision);self.assertEqual('replacement.order',decision.kind)
        s = self.resume(s);self.assertIsNone(self.settle(s))
        self.assertEqual(52,s.state.players['A'].life)
        self.assertEqual(1,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_real_erebos_noncreature_permission_life_price_and_live_devotion(self):
        s = self.session(119710030);source = self.add(s.engine,NAMES[2],zone='hand')
        bodies = [self.add(s.engine,'Walking Corpse',zone='hand',ref='erebos-devotion-'+str(i)) for i in range(4)]
        heals = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='erebos-other-heal-'+str(i)) for i in range(2)]
        bounce = self.add(s.engine,'Unsummon',seat='B',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'W':2,'U':1})
        self.prepare(s,mana={'C':8,'B':6});self.checkpoint(s)
        self.cast(s,source,payment={'C':3,'B':1});self.assertIsNone(self.settle(s))
        self.assertNotIn('Creature',s.engine._effective_card_data(source)['type_line'])
        self.assertIn('indestructible',s.engine._combat_keywords(s.state.cards[source.object_id]))
        before = set(s.state.players['A'].zones['hand'])
        self.activate(s,source);self.assertIsNone(self.settle(s))
        self.assertEqual(38,s.state.players['A'].life)
        drawn = set(s.state.players['A'].zones['hand'])-before;self.assertEqual(1,len(drawn))
        ref = s.state.cards[next(iter(drawn))].ref
        for seat in 'BCD':self.assertNotIn(ref,json.dumps(s.packet('pilot:'+seat,full=True)))
        self.heal(s,heals[0],seat='B',gain=0)
        for body in bodies:self.cast(s,body,payment={'C':1,'B':1});self.assertIsNone(self.settle(s))
        self.assertIn('Creature',s.engine._effective_card_data(source)['type_line'])
        self.assertEqual((5,7),(s.engine._numeric_stat(source.object_id,'power'),s.engine._numeric_stat(source.object_id,'toughness')))
        self.cast(s,bounce,[bodies[0].ref],seat='B',payment={'U':1});self.assertIsNone(self.settle(s))
        self.assertNotIn('Creature',s.engine._effective_card_data(source)['type_line'])
        s = self.resume(s);self.heal(s,heals[1],seat='B',gain=0)
        self.assertEqual(40,s.state.players['B'].life)
        self.private_replay(s,private)

    def test_real_global_source_blocks_canonical_prevention_aftermath(self):
        from quorune.damage_modifier_state import (DamageModifierDuration,DamagePreventionShield,
            DamageSubject,GainLifePreventionAftermath,PreventionMode)
        s = self.session(119710035);source = self.add(s.engine,NAMES[0],zone='hand')
        attacker = self.add(s.engine,'Grizzly Bears');attacker.acquired_control_turn_count = -1
        listener = self.add(s.engine,'Bloodthirsty Aerialist',seat='B')
        for i in range(2):self.add(s.engine,'Boon Reflection',seat='B',ref='aftermath-double-'+str(i))
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        # A valid pre-existing typed prevention effect is part of the saved
        # starting position; this does not claim an Awe Strike compiler path.
        s.state.damage_prevention_shields.append(DamagePreventionShield(
            shield_id='life-prohibition-aftermath',source_id='fixture:life-prevention',controller='B',
            subject=DamageSubject(ref='B',kind='player',controller='B'),mode=PreventionMode.AMOUNT,
            remaining=2,duration=DamageModifierDuration.UNTIL_END_OF_TURN,
            created_turn_sequence=s.state.turn_sequence,aftermath=(GainLifePreventionAftermath(player='B',per_prevented=1),)))
        self.prepare(s,mana={'C':2,'R':1});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.attackers')
        result = s.act('pilot:A',{'action_id':'attack','atk':{attacker.ref:'B'}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:getattr(x.state.pending_decision,'kind',None) == 'combat.blockers')
        result = s.act('pilot:B',{'action_id':'block','blocks':{}});self.assertTrue(result.ok,result.summary)
        self.advance(s,lambda x:x.state.phase == 'postcombat_main')
        self.assertEqual((40,40),(s.state.players['A'].life,s.state.players['B'].life))
        self.assertEqual(0,s.state.cards[listener.object_id].counters.get('+1/+1',0))
        self.private_replay(s,private)

    def test_real_tibalt_loyalty_devil_death_and_source_removal(self):
        s = self.session(119710040);source = self.add(s.engine,NAMES[3],zone='hand')
        heals = [self.add(s.engine,'Revitalize',seat='B',zone='hand',ref='tibalt-other-heal-'+str(i)) for i in range(2)]
        shock = self.add(s.engine,'Shock',zone='hand');bolt = self.add(s.engine,'Lightning Bolt',zone='hand')
        private = self.add(s.engine,'Generic Bound Growth',seat='C',zone='hand')
        s.state.players['B'].mana_pool.update({'C':2,'W':2})
        self.prepare(s,mana={'C':2,'R':3});self.checkpoint(s)
        self.cast(s,source,payment={'C':2,'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual(5,s.state.cards[source.object_id].counters['loyalty'])
        self.activate(s,source)
        self.assertEqual(3,s.state.cards[source.object_id].counters['loyalty'])
        self.assertIsNone(self.settle(s))
        token = next(c for c in s.state.cards.values() if c.is_token and c.zone == 'battlefield' and c.printed_name == 'Devil')
        self.assertEqual((1,1),(s.engine._numeric_stat(token.object_id,'power'),s.engine._numeric_stat(token.object_id,'toughness')))
        self.heal(s,heals[0],seat='B',gain=0)
        self.cast(s,shock,[token.ref],payment={'R':1});self.assertIsNone(self.settle(s,target='C'))
        self.assertEqual(39,s.state.players['C'].life)
        self.cast(s,bolt,[source.ref],payment={'R':1});self.assertIsNone(self.settle(s))
        self.assertEqual('graveyard',s.state.cards[source.object_id].zone)
        s = self.resume(s);self.heal(s,heals[1],seat='B',gain=3)
        self.private_replay(s,private)

    def test_verified_capability_withdrawal_is_a_specific_rules_block(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from scripts.update_capability_evidence import build_index,discover_tests
        from quorune.oracle_ir import compile_oracle_card
        from quorune.life_gain_permission import LIFE_GAIN_PROHIBITION_CAPABILITY as CAP
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'));changed = deepcopy(raw)
        next(r for r in changed['capabilities'] if r['id'] == CAP).update(status='blocked',blockers=['withdrawn life-gain permission'],dependency_fail_closed_status='not_run')
        registry = CapabilityRegistry(changed)
        index = build_index(registry_value=changed,declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        for name in NAMES:
            ir = compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review')
            blockers = [b for r in ir.material_residuals for b in r.blockers]
            self.assertTrue(any('status:'+CAP+':blocked' in b for b in blockers),blockers)
            self.assertFalse(any('evidence_index:unverified' in b for b in blockers),blockers)

    def test_fresh_current_ability_omission_is_detected(self):
        method = 'test_real_global_source_blocks_doublers_and_lifelink_then_humble_revokes_it'
        with patch('quorune.semantic_runtime.life_prohibitions.program_has_current_ability_fragments',return_value=True):
            with self.assertRaises(AssertionError):getattr(type(self)(method),method)()


if __name__ == '__main__':unittest.main()
