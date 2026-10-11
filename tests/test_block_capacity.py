from __future__ import annotations

from dataclasses import replace
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from common import ROOT
from quorune.block_capacity_model import BlockCapacitySpec
from quorune.rules.block_capacity import current_block_capacity
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition,DeckEntry
from quorune.model import CombatState
from quorune.engine import TURN_STEPS
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry,load_default_capability_registry
from quorune.combat_constraints import DeclarationProblem,DeclarationRequirement,DeclarationRestriction,DeclarationConstraintError
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class BlockCapacityCompilerTests(unittest.TestCase):
    def test_fixed_additional_unlimited_attachment_and_query_grammar_is_closed(self):
        registry=load_default_capability_registry()
        for text in ('This creature can block an additional creature each combat.',
                     'This creature can block an additional seven creatures each combat.',
                     'This creature can block any number of creatures.',
                     'Each creature you control can block an additional creature each combat.',
                     'Equipped creature can block an additional creature each combat.',
                     'Enchanted creature gets +2/+2, has vigilance, and can block an additional creature each combat.',
                     'Enchanted creature can block any number of creatures.'):
            self.assertEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)

    def test_open_capacity_boolean_and_missing_capability_fail_closed(self):
        registry=load_default_capability_registry()
        for text in ('This creature can block an additional creature each combat for each Equipment attached to this creature.',
                     'This creature can block an additional X creatures each combat.',
                     'This creature can block an additional creature each combat until end of turn.'):
            self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=registry).status,text)
        for values in ({'additional':True},{'additional':-1},{'additional':100},{'additional':1,'unlimited':True},{'unlimited':1}):
            with self.assertRaises(ValueError):BlockCapacitySpec(**values)
        data=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in data['capabilities'] if c['id']=='combat.block.capacity').update(status='blocked',blockers=['missing multi-block owner'])
        registry=CapabilityRegistry(data);registry.mark_evidence_verified('0'*64)
        self.assertNotEqual('exact',compile_oracle_card(query_record('This creature can block an additional creature each combat.'),capability_registry=registry).status)

    def test_set_declaration_requirements_restrictions_costs_and_capacity_share_solver(self):
        requirements=(DeclarationRequirement('one','choose_option',variable='blocker',option='a'),DeclarationRequirement('two','choose_option',variable='blocker',option='b'))
        problem=DeclarationProblem({'blocker':('a','b','c')},requirements= requirements,capacities={'blocker':2})
        self.assertFalse(problem.evaluate({'blocker':'a'}).legal)
        self.assertTrue(problem.evaluate({'blocker':['a','b']}).legal)
        self.assertEqual(2,problem.maximum_satisfied_requirements())
        with self.assertRaises(DeclarationConstraintError):problem.evaluate({'blocker':['a','a']})
        with self.assertRaises(DeclarationConstraintError):problem.evaluate({'blocker':['a','b','c']})
        ordinary=replace(problem,capacities=None)
        self.assertEqual(1,ordinary.maximum_satisfied_requirements())
        self.assertTrue(ordinary.evaluate({'blocker':'a'}).legal)
        costed=replace(problem,costed_options=frozenset({('blocker','b')}))
        self.assertEqual(1,costed.maximum_satisfied_requirements())
        self.assertTrue(costed.evaluate({'blocker':['a','b']}).legal)
        restrictions=(DeclarationRestriction('menace','minimum_option_uses',option='a',count=2,when_used=True),)
        menace=DeclarationProblem({'one':('a','b'),'two':('a',)},restrictions=restrictions,capacities={'one':2,'two':1})
        self.assertFalse(menace.evaluate({'one':['a','b']}).legal)
        self.assertTrue(menace.evaluate({'one':['a','b'],'two':'a'}).legal)

    def test_multiple_edges_keep_pair_flanking_and_once_source_bushido_model(self):
        import test_block_transitions as models
        from quorune.block_transitions import build_block_transition,derive_block_keyword_trigger_occurrences
        from quorune.ability_fragments import CombatKeywordTriggerKind
        first=models._participant('first','A',models._spec(CombatKeywordTriggerKind.FLANKING))
        second=models._participant('second','A',models._spec(CombatKeywordTriggerKind.FLANKING))
        blocker=models._participant('multi','B',models._spec(CombatKeywordTriggerKind.BUSHIDO,2))
        event=build_block_transition(models._Query((first,second,blocker),{first.object_id:(blocker.object_id,),second.object_id:(blocker.object_id,)}))
        self.assertEqual(2,len(event.assignments))
        occurrences=derive_block_keyword_trigger_occurrences(event)
        self.assertEqual(2,sum(o.kind is CombatKeywordTriggerKind.FLANKING for o in occurrences))
        self.assertEqual(1,sum(o.kind is CombatKeywordTriggerKind.BUSHIDO for o in occurrences))


class BlockCapacityRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'block-capacity.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',ROOT/'tests/fixtures/block-capacity-cards.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Block capacity',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def prepare_blocks(self,s,attackers):
        e=s.engine;e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.started=True;e.state.active_player='A';e.state.phase='combat';e.state.step='declare_blockers'
        e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        for card in attackers:card.attacking='B'
        e.state.combat=CombatState(attackers_declared=True,attackers={card.object_id:'B' for card in attackers},defending_players=['B'])
        e._begin_blocker_decisions()
        self.assertEqual('combat.blockers',e.state.pending_decision.kind)

    def test_actual_trueheart_blocks_two_distinct_attackers_and_replays(self):
        s=self.session(323001);e=s.engine
        blocker=self.add(e,'Trueheart Duelist',seat='B')
        first=self.add(e,'Generic Bound Body',ref='first-attacker');second=self.add(e,'Generic Bound Body',ref='second-attacker');third=self.add(e,'Generic Bound Body',ref='third-attacker')
        self.prepare_blocks(s,[first,second,third]);self.checkpoint(s)
        packet=s.packet('pilot:B',full=True)['decision'];self.assertEqual(2,packet['ctx']['declaration_constraints']['capacities'][blocker.ref])
        for principal,blocks in [('pilot:A',{blocker.ref:[first.ref,second.ref]}),('pilot:B',{blocker.ref:[first.ref,first.ref]}),('pilot:B',{blocker.ref:[first.ref,second.ref,third.ref]})]:
            before=authoritative_state_hash(e.state);result=s.act(principal,{'action_id':'block','blocks':blocks})
            self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[first.ref,second.ref]}});self.assertTrue(result.ok,result.summary)
        self.assertEqual([blocker.object_id],e.state.combat.blockers[first.object_id]);self.assertEqual([blocker.object_id],e.state.combat.blockers[second.object_id])
        self.replay(s,load=True)
        from quorune.compiler.direct_target import DirectPermanentTargetSpec
        schema=DirectPermanentTargetSpec(types_any=('creature',),combat_state='blocking_source').to_target_schema()
        for attacker in (first,second):
            self.assertIn(blocker.ref,e._public_target_schema('B',schema,source_ref=attacker.ref)['legal_refs'])

    def test_actual_unlimited_palace_guard_blocks_three_but_each_evasion_still_applies(self):
        s=self.session(323002);e=s.engine
        blocker=self.add(e,'Palace Guard',seat='B')
        attackers=[self.add(e,'Generic Bound Body',ref='guard-attacker-'+str(i)) for i in range(3)]
        flying=self.add(e,'Aquastrand Spider',ref='flying-boundary');flying.temporary_keywords.append('Flying')
        self.prepare_blocks(s,[*attackers,flying]);self.checkpoint(s)
        before=authoritative_state_hash(e.state)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[attackers[0].ref,flying.ref]}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[c.ref for c in attackers]}});self.assertTrue(result.ok,result.summary)
        self.assertEqual(3,sum(blocker.object_id in ids for ids in e.state.combat.blockers.values()))
        self.replay(s,load=True)

    def test_omitting_capacity_mutant_is_killed_by_actual_two_block_command(self):
        with patch('quorune.rules.block_declaration.current_block_capacity',return_value=1):
            with self.assertRaises(AssertionError):self.test_actual_trueheart_blocks_two_distinct_attackers_and_replays()

    def test_actual_two_echo_equips_accumulate_and_bounce_after_blocks_keeps_all_edges(self):
        s=self.session(323003);e=s.engine
        body=self.add(e,'Generic Bound Body');one=self.add(e,'Echo Circlet',ref='first-circlet');two=self.add(e,'Echo Circlet',ref='second-circlet')
        bounce=self.add(e,'Disenchant',zone='hand')
        attackers=[self.add(e,'Generic Bound Body',seat='B',ref='equip-attacker-'+str(i)) for i in range(3)]
        self.ready(s,bounce,{'W':2,'C':10});self.checkpoint(s)
        for equipment in (one,two):
            actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
            action=next(a for a in actions if a['id'].startswith('activate:'+equipment.ref+':'))
            result=s.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(3,current_block_capacity(e,body,eligible_attackers=3))
        # Start a new replay checkpoint at a manually constructed combat boundary.
        e.permissions.invalidate_current();e.state.pending_decision=None;e.state.priority_player=None;e.state.priority_passes=[]
        e.state.active_player='B';e.state.phase='combat';e.state.step='declare_blockers'
        e.state.phase_index=TURN_STEPS.index(('combat','declare_blockers'))
        for card in attackers:card.attacking='A'
        e.state.combat=CombatState(attackers_declared=True,attackers={c.object_id:'A' for c in attackers},defending_players=['A'])
        e._begin_blocker_decisions();self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':'block','blocks':{body.ref:[c.ref for c in attackers]}});self.assertTrue(result.ok,result.summary)
        for _ in range(8):
            if s.packet('pilot:A',full=True)['decision'] is not None:break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a.get('card')==bounce.ref)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[one.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(2,current_block_capacity(e,body,eligible_attackers=3))
        self.assertEqual(3,sum(body.object_id in ids for ids in e.state.combat.blockers.values()))
        self.replay(s,load=True)

    def test_actual_two_block_combat_damage_divides_once_and_preserves_both_recipients(self):
        s=self.session(323004);e=s.engine
        blocker=self.add(e,'Selesnya Sagittars',seat='B')
        first=self.add(e,'Generic Bound Body',ref='damage-first');second=self.add(e,'Generic Bound Body',ref='damage-second')
        self.prepare_blocks(s,[first,second]);self.checkpoint(s)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[first.ref,second.ref]}});self.assertTrue(result.ok,result.summary)
        for _ in range(16):
            if e.state.pending_decision and e.state.pending_decision.kind!='priority':break
            result=s.act(s.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual('combat.damage',e.state.pending_decision.kind)
        result=s.act('pilot:B',{'action_id':'assign_damage','assignments':[
            {'source':blocker.ref,'target':first.ref,'amount':1},
            {'source':blocker.ref,'target':second.ref,'amount':1}]})
        self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,e.state.cards[first.object_id].marked_damage)
        self.assertEqual(1,e.state.cards[second.object_id].marked_damage)
        self.assertEqual(4,e.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(40,e.state.players['B'].life)
        self.replay(s,load=True)

    def test_actual_query_source_and_tactician_counter_activation_gate_current_capacity(self):
        s=self.session(323005);e=s.engine
        source=self.add(e,"Cenn's Tactician");source.acquired_control_turn_count=-1
        blocker=self.add(e,'Trueheart Duelist')
        probe=self.add(e,'Disenchant',zone='hand')
        self.ready(s,source,{'W':3,'C':2});self.checkpoint(s)
        self.assertEqual(1,current_block_capacity(e,source,eligible_attackers=4))
        actions=s.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+source.ref+':'))
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[source.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(2,current_block_capacity(e,source,eligible_attackers=4))
        self.replay(s,load=True)
        # Real query source is evaluated with both own and foreign recipients.
        s=self.session(323006);e=s.engine
        enchantment=self.add(e,'Brave the Sands',seat='B')
        own=self.add(e,'Generic Bound Body',seat='B',ref='own-query-blocker')
        foreign=self.add(e,'Generic Bound Body',seat='C',ref='foreign-query-blocker')
        attackers=[self.add(e,'Generic Bound Body',ref='query-attacker-'+str(i)) for i in range(2)]
        self.prepare_blocks(s,attackers);self.checkpoint(s)
        self.assertEqual(2,current_block_capacity(e,own,eligible_attackers=3))
        self.assertEqual(1,current_block_capacity(e,foreign,eligible_attackers=3))
        result=s.act('pilot:B',{'action_id':'block','blocks':{own.ref:[c.ref for c in attackers]}});self.assertTrue(result.ok,result.summary)
        self.replay(s,load=True)

    def test_actual_monarch_condition_changes_capacity_before_declaration_and_not_after(self):
        s=self.session(323007);e=s.engine
        blocker=self.add(e,'Entourage of Trest',seat='B')
        attackers=[self.add(e,'Generic Bound Body',ref='monarch-attacker-'+str(i)) for i in range(2)]
        e.become_monarch('B',reason='independent public condition setup')
        self.prepare_blocks(s,attackers);self.checkpoint(s)
        self.assertEqual(2,current_block_capacity(e,blocker,eligible_attackers=3))
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[c.ref for c in attackers]}});self.assertTrue(result.ok,result.summary)
        self.replay(s,load=True)
        e.become_monarch('C',reason='owner diagnostic after accepted block')
        self.assertEqual(1,current_block_capacity(e,blocker,eligible_attackers=3))
        self.assertEqual(2,sum(blocker.object_id in ids for ids in e.state.combat.blockers.values()))

    def test_actual_monarch_condition_false_rejects_second_block_without_mutation(self):
        s=self.session(323010);e=s.engine
        blocker=self.add(e,'Entourage of Trest',seat='B')
        attackers=[self.add(e,'Generic Bound Body',ref='other-monarch-attacker-'+str(i)) for i in range(2)]
        e.become_monarch('C',reason='independent negative public condition setup')
        self.prepare_blocks(s,attackers);self.checkpoint(s)
        self.assertEqual(1,current_block_capacity(e,blocker,eligible_attackers=3))
        before=authoritative_state_hash(e.state)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[c.ref for c in attackers]}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:attackers[0].ref}})
        self.assertTrue(result.ok,result.summary);self.replay(s,load=True)

    def test_ordinary_body_cannot_use_multi_block_list_and_wrong_defender_is_atomic(self):
        s=self.session(323008);e=s.engine
        blocker=self.add(e,'Generic Bound Body',seat='B',ref='ordinary-blocker')
        first=self.add(e,'Generic Bound Body',ref='ordinary-first');second=self.add(e,'Generic Bound Body',ref='ordinary-second')
        self.prepare_blocks(s,[first,second]);self.checkpoint(s)
        before=authoritative_state_hash(e.state)
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:[first.ref,second.ref]}})
        self.assertFalse(result.ok);self.assertEqual(before,authoritative_state_hash(e.state))
        result=s.act('pilot:B',{'action_id':'block','blocks':{blocker.ref:first.ref}})
        self.assertTrue(result.ok,result.summary)
        self.replay(s,load=True)

    def test_actual_iona_aura_keeps_stats_vigilance_and_current_attached_capacity(self):
        s=self.session(323009);e=s.engine
        body=self.add(e,'Generic Bound Body')
        aura=self.add(e,"Iona's Blessing",zone='hand')
        action=self.ready(s,aura,{'W':3,'C':4});self.checkpoint(s)
        result=s.act('pilot:A',{'action_id':action['id'],'targets':[body.ref],'pay':'auto'});self.assertTrue(result.ok,result.summary);self.resolve(s)
        self.assertEqual(body.object_id,e.state.cards[aura.object_id].attached_to)
        self.assertEqual(2,current_block_capacity(e,body,eligible_attackers=3))
        self.assertIn('vigilance',e._combat_keywords(body))
        data=e._effective_card_data(body)
        self.assertEqual((4,8),(int(data['power']),int(data['toughness'])))
        self.replay(s,load=True)
