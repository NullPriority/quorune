from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.activation_usage import commit_trigger_usage, trigger_usage_available
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.trigger_limits import OPTIONAL_RESULT_LIMIT_CAPABILITY, OPTIONAL_RESULT_USED_HISTORY, TriggerLimitSpec
from quorune.semantics import SemanticProgram, SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_qualified_zone_event_queries import query_record
import test_bound_effect_programs as witnesses
import test_counter_placement_events as counter_witnesses


class LimitedEventTriggerCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'limit-shapes.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures/limited-event-triggers.json'], path)
        cls.db = CardDatabase(path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_closed_limit_preserves_span_and_dependency(self):
        text='Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.'
        ir=compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry())
        self.assertEqual('exact',ir.status,ir.material_residuals)
        node=ir.faces[0].nodes[0]
        self.assertEqual(TriggerLimitSpec().to_dict(),node.trigger_limit)
        self.assertEqual((0,len(text)),(node.span.start,node.span.end))
        self.assertEqual(text,node.text)
        self.assertIn('trigger.usage.once_per_turn',node.capability_dependencies)
        self.assertIn('current_ability_fragment_required',node.runtime_coverage)

    def test_unsupported_action_limits_and_departures_remain_residual(self):
        for text in (
            'Whenever you gain life, draw a card. Do this only once each turn.',
            'Whenever a creature dies, draw a card. This ability triggers only twice each turn.',
            'Whenever a creature dies or you gain life, draw a card. This ability triggers only once each turn.',
            'Whenever a spell you control is countered, draw a card. This ability triggers only once each turn.',
            'Whenever you gain life, you win the game. This ability triggers only once each turn.',
            'Whenever you gain life, draw a card. This ability triggers only once during your turn.',
        ):
            with self.subTest(text=text):
                self.assertNotEqual('exact',compile_oracle_card(query_record(text),capability_registry=load_default_capability_registry()).status)

    def test_optional_use_descriptor_and_withdrawal_are_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime

        registry = load_default_capability_registry()
        record = self.db.lookup('Terrasymbiosis')
        ir = compile_oracle_card(record, capability_registry=registry, capability_profile='commander_review')
        self.assertEqual('exact', ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        descriptor = {'schema_version': 3, 'kind': 'optional_result_once_per_turn'}
        self.assertEqual(descriptor, node.trigger_limit)
        self.assertEqual(record.oracle_text, node.text)
        self.assertEqual((0, len(record.oracle_text)), (node.span.start, node.span.end))
        program, = (p for p in generated_programs(self.db, record, trust_level='trusted',
            capability_registry=registry, capability_profile='commander_review') if p.trigger_limit is not None)
        serialized = program.to_dict()
        self.assertEqual(serialized, SemanticProgram.from_dict(serialized).to_dict())
        self.assertTrue(bind_semantic_program_runtime(program, capability_registry=registry, profile='commander_review')['strict'])
        dependencies = tuple(value for value in program.capability_dependencies if value != OPTIONAL_RESULT_LIMIT_CAPABILITY)
        forged = SimpleNamespace(**{field.name: getattr(program, field.name) for field in fields(program)})
        forged.capability_dependencies = dependencies
        forged.capability_closure = registry.closure(dependencies, profile='commander_review').to_dict()
        denied = bind_semantic_program_runtime(forged, capability_registry=registry, profile='commander_review')
        self.assertFalse(denied['strict'])
        self.assertIn('capability:undeclared_runtime_dependency:' + OPTIONAL_RESULT_LIMIT_CAPABILITY, denied['blockers'])
        with self.assertRaisesRegex(ValueError, 'controller choice'):
            SemanticProgram.from_dict({**serialized, 'effects': list(node.effects[0]['effects'])})
        with self.assertRaises(ValueError):
            TriggerLimitSpec.from_dict({**descriptor, 'kind': 'once_per_turn'})

    def test_missing_usage_capability_keeps_trigger_residual(self):
        from quorune.rules.capabilities import CapabilityRegistry
        raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(c for c in raw['capabilities'] if c['id']=='trigger.usage.once_per_turn').update(
            status='blocked',blockers=['Ordinary trigger usage authority withdrawn'])
        registry=CapabilityRegistry(raw)
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index,discover_tests
        index=build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
        text='Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.'
        ir=compile_oracle_card(query_record(text),capability_registry=registry)
        self.assertNotEqual('exact',ir.status)
        self.assertTrue(any('trigger.usage.once_per_turn' in str(r.blockers) for r in ir.material_residuals))
        self.assertEqual('exact',compile_oracle_card(query_record('When this creature enters, draw a card.'),
            capability_registry=registry).status)

    def test_codec_rejects_malformed_limit_and_preserves_old_shape(self):
        for value in ({'schema_version':True,'kind':'once_per_turn'}, {'schema_version':2,'kind':'once_per_turn'},
                      {'schema_version':1,'kind':'once_per_game'}, {'schema_version':1,'kind':'once_per_turn','extra':1}):
            with self.assertRaises(ValueError):TriggerLimitSpec.from_dict(value)
        old=SemanticProgram('old','old').to_dict()
        self.assertNotIn('trigger_limit',old)
        self.assertEqual(old,SemanticProgram.from_dict(old).to_dict())
        old['trigger_limit']='once_per_turn'
        with self.assertRaises(ValueError):SemanticProgram.from_dict(old)
        old['trigger_limit']=TriggerLimitSpec().to_dict();old['active_zone']='battlefield';old['event']='life.gained'
        with self.assertRaises(ValueError):SemanticProgram.from_dict(old)

    def test_departure_shape_and_forged_trust_binding_fail_closed(self):
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.rules.trigger_limits import validate_program_trigger_limit
        registry = load_default_capability_registry()
        record = query_record('Whenever one or more +1/+1 counters are put on this creature, draw a card. This ability triggers only once each turn.')
        program = next(program for program in generated_programs(self.db, record, trust_level='trusted',
            capability_registry=registry, capability_profile='commander_review') if program.trigger_limit is not None)
        serialized = program.to_dict()
        self.assertEqual(serialized, SemanticProgram.from_dict(serialized).to_dict(), 'Supported historical v1 shapes remain readable')
        for event in ('permanent.sacrificed', 'permanent.sacrificed.self', 'permanent.leave.self', 'creature.dies', 'multi.event'):
            with self.subTest(event=event):
                with self.assertRaisesRegex(ValueError, 'nondeparture'):
                    SemanticProgram.from_dict({**serialized, 'event': event})
                forged = SimpleNamespace(**{**{field.name: getattr(program, field.name) for field in fields(program)}, 'event': event})
                with self.assertRaisesRegex(ValueError, 'nondeparture'):
                    validate_program_trigger_limit(forged)
                binding = bind_semantic_program_runtime(forged, capability_registry=registry, profile='commander_review')
                self.assertFalse(binding['strict'])
                self.assertTrue(any(row.startswith('runtime_trigger_limit:invalid:') for row in binding['blockers']))


class LimitedEventTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory();path=Path(cls.temporary.name)/'limits.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/counter-placement-event-cards.json',ROOT/'tests/fixtures/limited-event-triggers.json'],path)
        cls.db=CardDatabase(path);cls.registry=load_default_capability_registry()
        cls.deck=DeckDefinition('Trigger limits',[DeckEntry('Generic Bound Commander',1,'commander'),DeckEntry('Generic Bound Plains',30)],['Generic Bound Commander'])
    @classmethod
    def tearDownClass(cls):cls.db.close();cls.temporary.cleanup()
    session=witnesses.BoundEffectProgramRuntimeTests.session
    add=witnesses.BoundEffectProgramRuntimeTests.add
    ready=witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint=witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay=witnesses.BoundEffectProgramRuntimeTests.replay
    order_triggers=counter_witnesses.CounterPlacementEventRuntimeTests.order_triggers
    resolve=counter_witnesses.CounterPlacementEventRuntimeTests.resolve

    def cast(self,session,spell,*,targets=()):
        actions=session.packet(session.pending_principals()[0],full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a.get('card')==spell.ref)
        result=session.act(session.pending_principals()[0],{'action_id':action['id'],'targets':list(targets),'pay':'auto'})
        self.assertTrue(result.ok,result.summary)

    def test_limited_sacrifice_departure_cards_require_explicit_event_incarnation_admission(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        for name in ('Generic Limited Self Sacrifice Observer', 'Generic Limited External Sacrifice Observer'):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=self.registry,
                    capability_profile='commander_review')
                self.assertEqual('exact', ir.status,ir.material_residuals)
                limits=[node.trigger_limit for face in ir.faces for node in face.nodes if node.trigger_limit is not None]
                self.assertEqual([{'schema_version':2,'kind':'once_per_turn','source_scope':'event_incarnation'}],limits)
                program = compile_best_available_card_program(self.db, record,
                    semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile='commander_review')
                binding = bind_card_program_runtime(program, capability_registry=self.registry,
                    profile='commander_review')
                self.assertTrue(binding['strict_capability_ready'],binding['blockers'])
                self.assertFalse(binding['blockers'])

    def test_actual_counter_events_draw_once_and_replay(self):
        session=self.session(301001);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        before=authoritative_state_hash(session.state)
        denied=session.act('pilot:B',{'action_id':'cast:'+spells[0].ref,'targets':[observer.ref],'pay':'auto'})
        self.assertFalse(denied.ok);self.assertEqual(before,authoritative_state_hash(session.state))
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(4,session.state.cards[observer.object_id].counters['+1/+1'])
        resumed=self.replay(session,load=True)
        self.assertEqual(session.state.cards[observer.object_id].annotations,resumed.state.cards[observer.object_id].annotations)

    def test_source_usage_continuity_is_per_ability_and_global_turn(self):
        source=SimpleNamespace(annotations={})
        commit_trigger_usage(source,ability_id='trigger:front:1',turn_sequence=4)
        self.assertFalse(trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=4))
        self.assertTrue(trigger_usage_available(source,ability_id='trigger:front:2',turn_sequence=4))
        self.assertTrue(trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=5))
        source.annotations['once_per_turn_triggers']={'trigger:front:1':True}
        with self.assertRaises(ValueError):trigger_usage_available(source,ability_id='trigger:front:1',turn_sequence=4)

    def test_actual_blink_renews_limit_but_distinct_sources_keep_their_own_usage(self):
        session=self.session(301003);engine=session.engine
        first=self.add(engine,'Dusk Legion Duelist',ref='first-observer')
        other=self.add(engine,'Dusk Legion Duelist',ref='other-observer')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        blink=self.add(engine,'Cloudshift',zone='hand')
        self.ready(session,spells[0],{'W':1});self.checkpoint(session)
        initial=first.logical_object_id;hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[0],targets=[first.ref]);self.resolve(session)
        self.cast(session,spells[1],targets=[other.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['A'].zones['hand']))
        self.cast(session,blink,targets=[first.ref]);self.resolve(session)
        self.assertNotEqual(initial,session.state.cards[first.object_id].logical_object_id)
        self.cast(session,spells[2],targets=[first.ref]);self.resolve(session)
        self.assertEqual(hand-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_trigger_multiplier_does_not_bypass_limit_but_stack_copy_resolves(self):
        session=self.session(301004);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        throne=self.add(engine,'Roaming Throne')
        throne.annotations['chosen_creature_type']='Vampire'
        throne.annotations['chosen_creature_type_adds_subtype']=True
        resonator=self.add(engine,'Strionic Resonator')
        spell=self.add(engine,'Generic Counter Event Two',zone='hand')
        self.ready(session,spell,{'C':2});self.checkpoint(session)
        hand=len(session.state.players['A'].zones['hand'])
        self.cast(session,spell,targets=[observer.ref])
        for _ in range(12):
            if any(i.kind=='triggered_ability' and i.source_object_id==observer.object_id for i in session.state.stack):break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        triggers=[i for i in session.state.stack if i.kind=='triggered_ability' and i.source_object_id==observer.object_id]
        self.assertEqual(1,len(triggers))
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        actions=session.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
        action=next(a for a in actions if a['id'].startswith('activate:'+resonator.ref+':'))
        self.assertIn(triggers[0].ref,action['target_schema']['legal_refs'])
        accepted=session.act('pilot:A',{'action_id':action['id'],'targets':[triggers[0].ref],'pay':'auto'})
        self.assertTrue(accepted.ok,accepted.summary);self.resolve(session)
        self.assertEqual(hand+1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_countered_first_trigger_still_consumes_allowance(self):
        session=self.session(301005);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        stifle=self.add(engine,'Stifle',zone='hand')
        self.ready(session,spells[0],{'U':1});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref])
        for _ in range(12):
            triggers=[i for i in session.state.stack if i.kind=='triggered_ability' and i.source_object_id==observer.object_id]
            if triggers:break
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.assertEqual(1,len(triggers))
        while session.pending_principals()[0]!='pilot:A':
            result=session.act(session.pending_principals()[0],{'action_id':'pass'});self.assertTrue(result.ok,result.summary)
        self.cast(session,stifle,targets=[triggers[0].ref]);self.resolve(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_control_change_and_next_players_turn_use_same_object_allowance(self):
        from quorune.control_effects import change_control
        from quorune.model import TurnEntry
        session=self.session(301006);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        self.ready(session,spells[0],{});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session);self.replay(session)
        identity=observer.logical_object_id
        change_control(engine,observer.object_id,'B')
        self.checkpoint(session)
        bhand=len(session.state.players['B'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(bhand,len(session.state.players['B'].zones['hand']))
        self.assertEqual(identity,session.state.cards[observer.object_id].logical_object_id)
        self.replay(session)
        engine._begin_turn(TurnEntry(turn_id='limit-next-turn',player='B'))
        third=session.state.cards[spells[2].object_id]
        engine.state.players['A'].zones['hand'].remove(third.object_id)
        third.owner='B';third.controller='B';engine.state.players['B'].zones['hand'].append(third.object_id)
        engine.permissions.invalidate_current();engine.state.pending_decision=None
        engine.state.phase='precombat_main';engine.state.step='main';engine._grant_priority('B');engine.pump()
        self.checkpoint(session)
        hand=len(session.state.players['B'].zones['hand'])
        self.cast(session,third,targets=[observer.ref]);self.resolve(session)
        self.assertEqual(hand,len(session.state.players['B'].zones['hand']))
        self.replay(session)

    def test_ability_loss_and_restoration_do_not_clear_consumed_use(self):
        session=self.session(301007);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(3)]
        frog=self.add(engine,'Turn to Frog',zone='hand')
        self.ready(session,spells[0],{'U':1,'C':1});self.checkpoint(session)
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        usage=deepcopy(session.state.cards[observer.object_id].annotations['once_per_turn_triggers'])
        self.cast(session,frog,targets=[observer.ref]);self.resolve(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.assertEqual(usage,session.state.cards[observer.object_id].annotations['once_per_turn_triggers'])
        self.replay(session)
        # Remove the temporary layer-six effect as an owner diagnostic; replay the
        # subsequent action from this explicit checkpoint, not across setup mutation.
        engine.state.continuous_effects.clear();engine.permissions.invalidate_current()
        engine.state.pending_decision=None;engine._grant_priority('A');engine.pump();self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,spells[2],targets=[observer.ref]);self.resolve(session)
        self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))
        self.replay(session)

    def test_missing_usage_mutant_is_killed_by_second_real_event(self):
        session=self.session(301002);engine=session.engine
        observer=self.add(engine,'Dusk Legion Duelist')
        spells=[self.add(engine,'Generic Counter Event Two',zone='hand',ref='counter-'+str(i)) for i in range(2)]
        self.ready(session,spells[0],{})
        self.cast(session,spells[0],targets=[observer.ref]);self.resolve(session)
        with patch('quorune.activation_usage.trigger_usage_available',return_value=True),patch('quorune.trigger_discovery.commit_trigger_usage'):
            before=len(session.state.players['A'].zones['hand'])
            self.cast(session,spells[1],targets=[observer.ref]);self.resolve(session)
            with self.assertRaises(AssertionError):self.assertEqual(before-1,len(session.state.players['A'].zones['hand']))

    def test_false_intervening_condition_does_not_consume_limit(self):
        session=self.session(301008);engine=session.engine
        observer=self.add(engine,'Generic Limited Life Observer')
        gains=[self.add(engine,'Generic Limited Life Gain',zone='hand',ref='gain-'+str(i)) for i in range(2)]
        self.ready(session,gains[0],{'W':2});self.checkpoint(session)
        self.cast(session,gains[0]);self.resolve(session)
        self.assertNotIn('once_per_turn_triggers',session.state.cards[observer.object_id].annotations)
        self.replay(session)
        self.add(engine,'Generic Bound Body',ref='condition-body');self.checkpoint(session)
        before=len(session.state.players['A'].zones['hand'])
        self.cast(session,gains[1]);self.resolve(session)
        self.assertEqual(before,len(session.state.players['A'].zones['hand']))
        self.assertIn('once_per_turn_triggers',session.state.cards[observer.object_id].annotations)
        self.replay(session)


class OptionalResultTriggerRuntimeTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'optional-result-limits.sqlite3'
        build_fixture_database([ROOT / 'tests/fixtures' / name for name in (
            'bound-effect-program-cards.json', 'limited-event-triggers.json', 'counter-placement-event-cards.json',
            'discover-cards.json', 'event-spell-copy-cards.json', 'static-player-restriction-cards.json',
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Optional trigger results', [DeckEntry('Generic Bound Commander', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Generic Bound Commander'])

    def resolve_choice(self, session, operation='offer_optional_effect'):
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual('semantic.choice', decision.kind)
        self.assertEqual(operation, decision.continuation['effect']['op'])
        return decision.continuation['effect']

    def choose(self, session, **response):
        result = session.act('pilot:A', {'action_id': 'choose', **response})
        self.assertTrue(result.ok, result.summary)

    def uses(self, session):
        return [event for event in session.state.turn_history.events if event.kind == OPTIONAL_RESULT_USED_HISTORY]

    def test_terrasymbiosis_spends_one_use_for_departed_source_and_resets_on_return(self):
        session = self.session(825001)
        source = self.add(session.engine, 'Terrasymbiosis')
        bear = self.add(session.engine, 'Grizzly Bears')
        self.add(session.engine, 'Forest')
        resonator = self.add(session.engine, 'Strionic Resonator')
        blink = self.add(session.engine, 'Flicker of Fate', zone='hand')
        growth = [self.add(session.engine, 'Battlegrowth', zone='hand', ref=f'optional-limit-growth-{i}') for i in range(5)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 4, 'W': 1, 'G': 5})
        self.checkpoint(session)
        identity = source.logical_object_id
        for spell in growth[:2]:
            self.cast(session, 'A', spell, [bear.ref])
            self.resolve_top(session)
        self.assertEqual(2, self.trigger_count(session, source))
        self.resolve_choice(session)
        self.choose(session, choice='decline')
        self.assertEqual([], self.uses(session))
        self.priority(session, 'A')
        original = session.state.stack[-1].ref
        actions = session.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        action = next(row for row in actions if row['id'].startswith('activate:' + resonator.ref + ':'))
        self.assertIn(original, action['target_schema']['legal_refs'])
        accepted = session.act('pilot:A', {'action_id': action['id'], 'targets': [original], 'pay': 'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(session)
        self.assertEqual(2, self.trigger_count(session, source))
        self.cast(session, 'A', blink, [source.ref])
        self.resolve_top(session)
        replacement = session.state.cards[source.object_id].logical_object_id
        self.assertNotEqual(identity, replacement)
        self.resolve_choice(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act('pilot:C', {'action_id': 'choose', 'choice': 'apply'}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        draw_ref = session.state.cards[session.state.players['A'].zones['library'][-1]].ref
        self.choose(session, choice='apply')
        self.assertEqual([identity], [event.object_incarnation for event in self.uses(session)],
            'An accepted choice must record the consumed original usage')
        self.assertIsNone(self.resolve(session))
        self.assertTrue(any(session.state.cards[oid].ref == draw_ref for oid in session.state.players['A'].zones['hand']))
        for seat in 'BCD':
            self.assertNotIn(draw_ref, json.dumps(session.packet('pilot:' + seat, full=True)))
        self.cast(session, 'A', growth[2], [bear.ref])
        self.resolve_choice(session)
        self.choose(session, choice='apply')
        self.assertIsNone(self.resolve(session))
        self.assertEqual([identity, replacement], [event.object_incarnation for event in self.uses(session)])
        self.cast(session, 'A', growth[3], [bear.ref])
        self.resolve_top(session)
        self.assertEqual(0, self.trigger_count(session, source))
        self.finish_turn(session)
        self.cast(session, 'A', growth[4], [bear.ref])
        self.resolve_choice(session)
        self.choose(session, choice='apply')
        self.assertEqual([replacement], [event.object_incarnation for event in self.uses(session)])
        self.replay(session, load=True)

    def test_optional_use_commit_omission_is_detected(self):
        with patch('quorune.rules.trigger_limits.commit_optional_result_usage'), self.assertRaisesRegex(
                AssertionError, 'An accepted choice must record the consumed original usage'):
            self.test_terrasymbiosis_spends_one_use_for_departed_source_and_resets_on_return()

    def test_terrasymbiosis_impossible_draw_keeps_the_use_available(self):
        session = self.session(825002)
        source = self.add(session.engine, 'Terrasymbiosis')
        bear = self.add(session.engine, 'Grizzly Bears')
        doubler = self.add(session.engine, 'Branching Evolution')
        self.add(session.engine, 'Narset, Parter of Veils', seat='B')
        destroy = self.add(session.engine, 'Disenchant', zone='hand')
        first = self.add(session.engine, 'Battlegrowth', zone='hand', ref='impossible-double')
        second = self.add(session.engine, 'Battlegrowth', zone='hand', ref='possible-single')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 1, 'W': 1, 'G': 3})
        self.checkpoint(session)
        self.cast(session, 'A', first, [bear.ref])
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.assertEqual(2, session.state.cards[bear.object_id].counters['+1/+1'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.uses(session))
        self.cast(session, 'A', destroy, [doubler.ref])
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', second, [bear.ref])
        effect = self.resolve_choice(session)
        self.assertEqual(1, effect['effects'][0]['count'])
        self.choose(session, choice='apply')
        self.assertEqual(1, len(self.uses(session)))
        self.replay(session, load=True)

    def test_cosmic_crucible_checks_event_spell_and_shares_pending_use(self):
        session = self.session(825003)
        source = self.add(session.engine, 'Cosmic Crucible')
        first = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='first-cosmic-spell')
        second = self.add(session.engine, 'Lightning Bolt', zone='hand', ref='second-cosmic-spell')
        counter = self.add(session.engine, 'Counterspell', seat='B', zone='hand')
        self.assert_closed(session, source)
        self.prepare(session, mana={'R': 2})
        session.state.players['B'].mana_pool.update({'U': 2})
        self.checkpoint(session)
        self.cast(session, 'A', first, ['C'])
        self.cast(session, 'A', second, ['D'])
        event_spell = next(item for item in session.state.stack if item.card_object_id == second.object_id)
        self.assertEqual(2, self.trigger_count(session, source))
        self.cast(session, 'B', counter, [event_spell.ref])
        self.resolve_top(session)
        self.assertEqual('graveyard', session.state.cards[second.object_id].zone)
        self.resolve_choice(session)
        self.choose(session, choice='apply')
        self.resolve_choice(session, 'copy_stack_item')
        self.choose(session, targets=['B'])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([37, 37, 40], [session.state.players[seat].life for seat in 'BCD'])
        self.assertEqual(1, len(self.uses(session)))
        self.replay(session, load=True)

    def test_irreverent_gremlin_declines_then_discards_only_once(self):
        session = self.session(825004)
        source = self.add(session.engine, 'Irreverent Gremlin')
        large = self.add(session.engine, 'Pouncing Cheetah', zone='hand')
        bears = [self.add(session.engine, 'Grizzly Bears', zone='hand', ref=f'gremlin-entrant-{i}') for i in range(3)]
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 5, 'G': 4})
        self.checkpoint(session)
        self.cast(session, 'A', large)
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', bears[0])
        self.resolve_choice(session)
        self.choose(session, choice='decline')
        self.assertEqual([], self.uses(session))
        self.cast(session, 'A', bears[1])
        self.resolve_choice(session)
        discard = session.state.cards[session.state.players['A'].zones['hand'][0]]
        self.choose(session, choice='apply')
        self.resolve_choice(session, 'offer_optional_mana_payment')
        self.assertNotIn(discard.ref, json.dumps(session.packet('pilot:B', full=True)))
        self.choose(session, cards=[discard.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual('graveyard', session.state.cards[discard.object_id].zone)
        self.cast(session, 'A', bears[2])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.uses(session)))
        self.replay(session)

    def test_pantlaza_reads_live_toughness_and_reuses_turn_allowance(self):
        session = self.session(825005)
        source = self.add(session.engine, 'Pantlaza, Sun-Favored', zone='hand')
        dinosaur = self.add(session.engine, 'Thrashing Brontodon', zone='hand')
        growth = self.add(session.engine, 'Battlegrowth', zone='hand')
        hit = self.add(session.engine, 'Lightning Bolt', zone='library', ref='pantlaza-private-hit')
        self.assert_closed(session, source)
        self.prepare(session, mana={'C': 3, 'W': 1, 'R': 1, 'G': 7})
        self.checkpoint(session)
        self.cast(session, 'A', source)
        self.resolve_top(session)
        self.assertEqual(1, self.trigger_count(session, source))
        self.cast(session, 'A', growth, [source.ref])
        self.assertEqual(5, self.resolve_choice(session)['effects'][0]['amount'])
        self.choose(session, choice='apply')
        self.resolve_choice(session, 'discover_cast_choice')
        self.choose(session, choice='decline')
        self.assertIsNone(self.resolve(session))
        self.assertEqual('hand', session.state.cards[hit.object_id].zone)
        self.cast(session, 'A', dinosaur)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(1, len(self.uses(session)))
        self.replay(session)

    def test_earth_kingdom_general_uses_only_its_controllers_placements(self):
        session = self.session(825006)
        source = self.add(session.engine, 'Earth Kingdom General')
        bear = self.add(session.engine, 'Grizzly Bears')
        self.add(session.engine, 'Branching Evolution')
        opponent = self.add(session.engine, 'Battlegrowth', seat='B', zone='hand', ref='earth-opponent-growth')
        first = self.add(session.engine, 'Battlegrowth', zone='hand', ref='earth-controller-growth')
        second = self.add(session.engine, 'Battlegrowth', zone='hand', ref='earth-post-usage-growth')
        self.assert_closed(session, source)
        self.prepare(session, mana={'G': 2})
        session.state.players['B'].mana_pool.update({'G': 1})
        self.checkpoint(session)
        self.cast(session, 'B', opponent, [bear.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual([], self.uses(session))
        life = session.state.players['A'].life
        self.cast(session, 'A', first, [bear.ref])
        self.assertEqual(2, self.resolve_choice(session)['effects'][0]['delta'])
        self.choose(session, choice='apply')
        self.assertIsNone(self.resolve(session))
        self.cast(session, 'A', second, [bear.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(life + 2, session.state.players['A'].life)
        self.assertEqual(1, len(self.uses(session)))
        self.replay(session)


if __name__=='__main__':unittest.main()
