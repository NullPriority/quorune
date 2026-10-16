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


class TemporaryBlockCapacityRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'temporary-capacity.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/bound-effect-program-cards.json',
            ROOT / 'tests/fixtures/block-capacity-cards.json',
            ROOT / 'tests/fixtures/creature-power-departure-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Temporary blocking capacity',
            [DeckEntry('Generic Bound Commander', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)],
            ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        BlockCapacityRuntimeTests.tearDownClass.__func__(cls)

    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay = witnesses.BoundEffectProgramRuntimeTests.replay
    resolve = counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def assert_closed_capacity_original(self, name):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.semantics import SemanticRegistry
        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile='commander_review')
        binding = bind_card_program_runtime(program, capability_registry=self.registry,
            profile='commander_review')
        self.assertTrue(binding['strict_capability_ready'], name + ': ' + binding['trust_basis'])

    def capacity_activation(self, session, source, seat='B'):
        for _ in range(16):
            self.assertEqual('priority', session.state.pending_decision.kind)
            principal = session.pending_principals()[0]
            if principal == 'pilot:' + seat:
                actions = session.packet(principal, full=True)['decision']['ctx']['legal']['actions']
                return next(row for row in actions
                    if row['id'].startswith('activate:' + source.ref + ':'))
            result = session.act(principal, {'action_id': 'pass'})
            self.assertTrue(result.ok, result.summary)
        self.fail('The source controller never received the activation window')

    def test_actual_mounted_archers_repeat_permissions_and_full_combat(self):
        self.assert_closed_capacity_original('Mounted Archers')
        session = self.session(523001)
        engine = session.engine
        blocker = self.add(engine, 'Mounted Archers', seat='B')
        attackers = [self.add(engine, 'Generic Bound Body', ref='capacity-attacker-' + str(index))
            for index in range(4)]
        private = self.add(engine, 'Generic Bound Body', seat='C', zone='hand', ref='capacity-private-hand')
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.started = True
        engine.state.active_player = 'A'
        engine.state.phase = 'combat'
        engine.state.step = 'declare_attackers'
        engine.state.phase_index = TURN_STEPS.index(('combat', 'declare_attackers'))
        engine.state.players['B'].mana_pool['W'] = 2
        for attacker in attackers:
            attacker.attacking = 'B'
        engine.state.combat = CombatState(attackers_declared=True,
            attackers={card.object_id: 'B' for card in attackers}, defending_players=['B'])
        engine._grant_priority('B')
        engine.pump()
        self.checkpoint(session)
        self.assertEqual(1, current_block_capacity(engine, blocker, eligible_attackers=4))
        for count in (1, 2):
            action = self.capacity_activation(session, blocker)
            if count == 1:
                before = authoritative_state_hash(engine.state)
                rejected = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(engine.state))
            accepted = session.act('pilot:B', {'action_id': action['id'], 'pay': 'auto'})
            self.assertTrue(accepted.ok, accepted.summary)
            self.replay(session, load=True)
            self.resolve(session)
            self.assertEqual(1 + count, current_block_capacity(engine, blocker, eligible_attackers=4))
            self.assertEqual(2 - count, engine.state.players['B'].mana_pool.get('W', 0))
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:A', full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        for _ in range(24):
            if engine.state.pending_decision.kind != 'priority':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('combat.blockers', engine.state.pending_decision.kind)
        packet = session.packet('pilot:B', full=True)['decision']
        self.assertEqual(3, packet['ctx']['declaration_constraints']['capacities'][blocker.ref])
        before = authoritative_state_hash(engine.state)
        rejected = session.act('pilot:B', {'action_id': 'block',
            'blocks': {blocker.ref: [card.ref for card in attackers]}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act('pilot:B', {'action_id': 'block',
            'blocks': {blocker.ref: [card.ref for card in attackers[:3]]}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(24):
            if engine.state.pending_decision and engine.state.pending_decision.kind != 'priority':
                break
            accepted = session.act(session.pending_principals()[0], {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('combat.damage', engine.state.pending_decision.kind)
        accepted = session.act('pilot:B', {'action_id': 'assign_damage',
            'assignments': [{'source': blocker.ref, 'target': attackers[0].ref, 'amount': 2}]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual('graveyard', engine.state.cards[blocker.object_id].zone)
        self.assertEqual([2, 0, 0, 0], [engine.state.cards[card.object_id].marked_damage for card in attackers])
        self.assertEqual(38, engine.state.players['B'].life)
        self.replay(session, load=True)

    def test_temporary_rule_journal_omission_is_killed_by_original_activation(self):
        with patch('quorune.rules.block_capacity_effect.commit_continuous_effect'):
            with self.assertRaises(AssertionError):
                self.test_actual_mounted_archers_repeat_permissions_and_full_combat()

    def cast_capacity_spell(self, session, spell, target, seat='A'):
        for _ in range(16):
            self.assertEqual('priority', session.state.pending_decision.kind)
            principal = session.pending_principals()[0]
            if principal == 'pilot:' + seat:
                actions = session.packet(principal, full=True)['decision']['ctx']['legal']['actions']
                action = next(row for row in actions if row.get('card') == spell.ref and row['id'].startswith('cast:'))
                accepted = session.act(principal, {'action_id': action['id'], 'targets': [target.ref], 'pay': 'auto'})
                self.assertTrue(accepted.ok, accepted.summary)
                self.replay(session, load=True)
                self.resolve(session)
                return
            accepted = session.act(principal, {'action_id': 'pass'})
            self.assertTrue(accepted.ok, accepted.summary)
        self.fail('The spell controller never received its cast window')

    def test_actual_valor_and_fixed_stats_survive_humble_then_blink_forgets_rules(self):
        for name in ('Valor Made Real', 'Give No Ground'):
            self.assert_closed_capacity_original(name)
        session = self.session(523002)
        engine = session.engine
        body = self.add(engine, 'Fencing Ace', seat='B')
        valor = self.add(engine, 'Valor Made Real', zone='hand')
        humble = self.add(engine, 'Humble', zone='hand')
        stats = self.add(engine, 'Give No Ground', zone='hand')
        blink = self.add(engine, 'Cloudshift', seat='B', zone='hand')
        private = self.add(engine, 'Generic Bound Body', seat='C', zone='hand', ref='target-capacity-private')
        engine.state.players['B'].mana_pool['W'] = 1
        witnesses.BoundEffectProgramRuntimeTests.ready(self, session, valor, {'W': 3, 'C': 4})
        self.checkpoint(session)
        self.cast_capacity_spell(session, valor, body)
        self.assertEqual(4, current_block_capacity(engine, body, eligible_attackers=4))
        self.cast_capacity_spell(session, humble, body)
        self.assertNotIn('double strike', engine._combat_keywords(body))
        self.assertEqual(4, current_block_capacity(engine, body, eligible_attackers=4))
        self.cast_capacity_spell(session, stats, body)
        data = engine._effective_card_data(body)
        self.assertEqual((2, 7), (int(data['power']), int(data['toughness'])))
        self.assertEqual(4, current_block_capacity(engine, body, eligible_attackers=4))
        self.assertNotIn(private.ref, json.dumps(session.packet('pilot:A', full=True)))
        self.assertIn(private.ref, json.dumps(session.packet('pilot:C', full=True)))
        prior = body.logical_object_id
        self.cast_capacity_spell(session, blink, body, seat='B')
        body = engine.state.cards[body.object_id]
        self.assertNotEqual(prior, body.logical_object_id)
        self.assertEqual(1, current_block_capacity(engine, body, eligible_attackers=4))
        self.assertIn('double strike', engine._combat_keywords(body))
        data = engine._effective_card_data(body)
        self.assertEqual((1, 1), (int(data['power']), int(data['toughness'])))
        self.replay(session, load=True)

    def test_actual_heroism_untap_stats_and_rule_repeat_then_expire_together(self):
        from quorune.continuous_effect_state import expire_end_of_turn_continuous_effects
        self.assert_closed_capacity_original('Act of Heroism')
        session = self.session(523003)
        engine = session.engine
        body = self.add(engine, 'Generic Bound Body', seat='B')
        body.tapped = True
        first = self.add(engine, 'Act of Heroism', zone='hand', ref='capacity-heroism-first')
        second = self.add(engine, 'Act of Heroism', zone='hand', ref='capacity-heroism-second')
        witnesses.BoundEffectProgramRuntimeTests.ready(self, session, first, {'W': 2, 'C': 2})
        self.checkpoint(session)
        for count, spell in enumerate((first, second), start=1):
            self.cast_capacity_spell(session, spell, body)
            self.assertFalse(body.tapped)
            self.assertEqual(count + 1, current_block_capacity(engine, body, eligible_attackers=4))
            data = engine._effective_card_data(body)
            self.assertEqual((2 + 2 * count, 6 + 2 * count), (int(data['power']), int(data['toughness'])))
        self.replay(session, load=True)
        expire_end_of_turn_continuous_effects(engine.state)
        self.assertEqual([], engine.state.continuous_effects)
        self.assertEqual(1, current_block_capacity(engine, body, eligible_attackers=4))
        data = engine._effective_card_data(body)
        self.assertEqual((2, 6), (int(data['power']), int(data['toughness'])))

    def test_pending_original_source_blink_does_not_grant_returned_incarnation(self):
        self.assert_closed_capacity_original('Mounted Archers')
        session = self.session(523004)
        engine = session.engine
        source = self.add(engine, 'Mounted Archers')
        blink = self.add(engine, 'Cloudshift', zone='hand')
        second_blink = self.add(engine, 'Cloudshift', zone='hand', ref='pending-capacity-target-blink')
        targeted = self.add(engine, 'Valor Made Real', zone='hand')
        action = witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, {'W': 4})
        self.checkpoint(session)
        prior = source.logical_object_id
        accepted = session.act('pilot:A', {'action_id': action['id'], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.cast_capacity_spell(session, blink, source)
        source = engine.state.cards[source.object_id]
        self.assertNotEqual(prior, source.logical_object_id)
        self.assertEqual(1, current_block_capacity(engine, source, eligible_attackers=4))
        self.assertEqual([], engine.state.continuous_effects)
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        cast = next(row for row in actions if row.get('card') == targeted.ref and row['id'].startswith('cast:'))
        accepted = session.act('pilot:A', {'action_id': cast['id'], 'targets': [source.ref], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        prior = source.logical_object_id
        self.cast_capacity_spell(session, second_blink, source)
        source = engine.state.cards[source.object_id]
        self.assertNotEqual(prior, source.logical_object_id)
        self.assertEqual(1, current_block_capacity(engine, source, eligible_attackers=4))
        self.assertEqual([], engine.state.continuous_effects)
        self.assertEqual(0, engine.state.players['A'].mana_pool.get('W', 0))
        self.replay(session, load=True)


class TemporaryBlockCapacityValueTests(unittest.TestCase):
    def test_resolved_capacity_round_trips_accumulates_and_ignores_ability_removal(self):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        from quorune.block_capacity_rule_model import ResolvedBlockCapacityRule
        from quorune.continuous_effect_model import ContinuousObjectIdentity, ContinuousEffectDuration
        from quorune.declaration_rule_effects import continuous_journal_effect_from_dict
        card = CardInstance(object_id='capacity-object', ref='capacity-ref', oracle_id='fixture:capacity',
            printed_name='Capacity diagnostic', owner='A', controller='B', zone='battlefield')
        identity = ContinuousObjectIdentity(card.object_id, card.logical_object_id)
        first = ResolvedBlockCapacityRule('capacity-rule-1', 'source-1', 1, identity, BlockCapacitySpec(additional=1))
        second = replace(first, effect_id='capacity-rule-2', timestamp=2)
        host = SimpleNamespace(state=SimpleNamespace(continuous_effects=[first, second]),
            _effective_card_data=lambda _: {'ability_fragments': []})
        restored = continuous_journal_effect_from_dict(first.to_dict())
        self.assertEqual(first, restored)
        self.assertEqual(first.fingerprint, restored.fingerprint)
        self.assertEqual(3, current_block_capacity(host, card, eligible_attackers=5))
        card.controller = 'C'
        self.assertEqual(3, current_block_capacity(host, card, eligible_attackers=5))
        host.state.continuous_effects = [replace(first, subject=ContinuousObjectIdentity(card.object_id, 'old-incarnation'))]
        self.assertEqual(1, current_block_capacity(host, card, eligible_attackers=5))
        host.state.continuous_effects = [replace(first, capacity=BlockCapacitySpec(unlimited=True))]
        self.assertEqual(5, current_block_capacity(host, card, eligible_attackers=5))
        card.phased_out = True
        self.assertEqual(1, current_block_capacity(host, card, eligible_attackers=5))
        for change in ({'timestamp': True}, {'subject': False}, {'duration': ContinuousEffectDuration.WHILE_SOURCE_PRESENT}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(first, **change)
        for raw in ({**first.to_dict(), 'extra': True}, {**first.to_dict(), 'capacity': {'schema_version': 1, 'additional': True, 'unlimited': False}}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                ResolvedBlockCapacityRule.from_dict(raw)

    def test_temporary_capacity_shapes_reject_open_fields_and_unbound_subjects(self):
        from quorune.block_capacity_rule_model import TEMPORARY_BLOCK_CAPACITY, TEMPORARY_BLOCK_CAPACITY_MECHANIC
        from quorune.rules.block_capacity_effect import decode_temporary_block_capacity, temporary_block_capacity_capabilities, apply_temporary_block_capacity
        from quorune.compiler.direct_target import direct_permanent_target_spec
        effect = {'op': 'apply_source_characteristics_until_end_of_turn', 'schema_version': 10,
            'card': '$source.zone_object', 'capacity': BlockCapacitySpec(additional=1).to_dict(), 'power': 0, 'toughness': 0}
        def dependencies(value, schema=None):
            return temporary_block_capacity_capabilities(effects=(value,), target_schema=schema,
                mechanic_ids=(TEMPORARY_BLOCK_CAPACITY_MECHANIC,))
        self.assertIn(TEMPORARY_BLOCK_CAPACITY, dependencies(effect))
        schema = direct_permanent_target_spec('Target creature').to_target_schema()
        self.assertIn('target.revalidate_resolution', dependencies({**effect, 'card': '$target.0'}, schema))
        for changed in ({'schema_version': True}, {'schema_version': 9}, {'card': None},
                {'card': '$target.1'}, {'power': True}, {'extra': 1},
                {'capacity': {'schema_version': 1, 'additional': 0, 'unlimited': False}}):
            with self.subTest(changed=changed):
                self.assertEqual((), dependencies({**effect, **changed}))
        self.assertEqual((), apply_temporary_block_capacity(None, {**effect, 'card': None}, actor='A', reason='departed'))
        with self.assertRaises(ValueError):
            decode_temporary_block_capacity({**effect, 'card': None})


class TemporaryBlockCapacityCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BlockCapacityRuntimeTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        BlockCapacityRuntimeTests.tearDownClass.__func__(cls)

    assert_closed_capacity_original = TemporaryBlockCapacityRuntimeTests.assert_closed_capacity_original

    def test_fixed_source_and_targeted_temporary_capacity_originals_close(self):
        for name in ('Luminous Guardian', 'Mounted Archers', 'Coastline Chimera',
                'Anurid Swarmsnapper', 'Valor Made Real', 'Give No Ground', 'Act of Heroism'):
            with self.subTest(name=name):
                self.assert_closed_capacity_original(name)

    def test_variable_live_set_quoted_and_combat_conditional_capacity_stays_residual(self):
        for text in ('{1}: This creature can block an additional X creatures this turn.',
                '{1}: This creature can block an additional creature this turn if you control an Island.',
                'Each creature you control can block an additional creature this turn.',
                'Target player can block any number of creatures this turn.',
                'Target creature gains "This creature can block an additional creature each combat" until end of turn.'):
            with self.subTest(text=text):
                self.assertNotEqual('exact', compile_oracle_card(query_record(text), capability_registry=self.registry).status)

    def test_temporary_capacity_dependencies_and_lowerer_fail_closed(self):
        from copy import deepcopy
        raw = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        card = self.db.lookup('Mounted Archers')
        for missing in (None, 'combat.block.capacity.temporary', 'combat.block.capacity',
                'continuous.resolution.fixed_characteristics_until_end_of_turn'):
            with self.subTest(missing=missing):
                changed = deepcopy(raw)
                if missing is not None:
                    next(row for row in changed['capabilities'] if row['id'] == missing).update(
                        status='blocked', blockers=['controlled negative dependency fixture'])
                registry = CapabilityRegistry(changed)
                registry.mark_evidence_verified('0' * 64)
                status = compile_oracle_card(card, capability_registry=registry).status
                if missing is None:
                    self.assertEqual('exact', status)
                else:
                    self.assertNotEqual('exact', status)
        with patch('quorune.compiler.resolution_effect_templates.temporary_block_capacity_template', return_value=None):
            self.assertNotEqual('exact', compile_oracle_card(card, capability_registry=self.registry).status)
