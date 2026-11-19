"""Pinned CR114/603/608: complete command-zone emblem event programs."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

FIXTURE = ROOT/'tests/fixtures/fixed-emblem-event-cards.json'
NAMES = ['Vraska, Golgari Queen', 'Sorin, Solemn Visitor', 'Jace, Unraveler of Secrets', 'Ral, Izzet Viceroy']

class FixedEmblemEventModelTests(unittest.TestCase):
    def test_scheduled_sacrifice_preserves_affected_player_choice_without_targeting(self):
        from quorune.compiler.scheduled_player_triggers import scheduled_player_binding_spec, scheduled_player_result
        from quorune.oracle_ir import _contextual_effect_templates
        binding = scheduled_player_binding_spec("At the beginning of each opponent's upkeep, that player sacrifices a creature of their choice.")
        lowered, current = scheduled_player_result(SimpleNamespace(variant=binding.variant, active_zone='battlefield', event=SimpleNamespace(value=binding.event)), binding.body,
            effect_template=_contextual_effect_templates(False, (), None)[0], card_name='Emblem')
        self.assertTrue(current); self.assertIsNone(lowered[2]); effect, = lowered[1]
        self.assertEqual(['$context.player'], effect['players']); self.assertEqual('$controller', effect['actor'])
        self.assertEqual(('sacrifice', 1), (effect['then'], effect['count'])); self.assertNotIn('target', effect)
        self.assertEqual(['creature'], effect['predicate']['types_all'])

    def test_scheduled_sacrifice_runtime_authority_is_data_derived_and_rejects_malformed_context(self):
        from quorune.compiler.affected_player_sacrifice_templates import fixed_affected_player_sacrifice_effect_template
        from quorune.rules.scheduled_player_shapes import scheduled_player_departure_runtime_dependencies
        choice = fixed_affected_player_sacrifice_effect_template('You sacrifice a creature.')
        effect = {**choice.effects[0], 'players': ['$context.player']}
        fields = dict(effects=(effect,), target_schema=None, event='step.begin', active_zone='command', coverage=(),
            event_condition={'all': [{'field':'step','op':'eq','value':'upkeep'}, {'field':'player','op':'ne','value':'$source.controller'}]})
        required = scheduled_player_departure_runtime_dependencies(SimpleNamespace(**fields))
        self.assertIn('trigger.event.scheduled_player_result', required); self.assertIn('choice.affected_player.fixed_sacrifice', required)
        self.assertFalse(scheduled_player_departure_runtime_dependencies(SimpleNamespace(**{**fields,'event':'spell.cast'})))
        for changes in ({'active_zone':'hand'}, {'event_condition':None},
                {'effects': ({**effect, 'count': True},)}, {'target_schema': {'count': 1}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                scheduled_player_departure_runtime_dependencies(SimpleNamespace(**{**fields, **changes}))

class FixedEventOutcomeModelTests(unittest.TestCase):
    def test_result_dependencies_are_derived_without_handlers_or_coverage(self):
        from quorune.rules.event_outcome_shapes import event_outcome_runtime_dependencies
        base=dict(target_schema=None, active_zone='command', coverage=(), event_condition=None)
        counter=SimpleNamespace(**base, event='spell.cast', effects=({'op':'counter_stack_target','stack':'$context.stack'},))
        self.assertIn('stack.counter.cast_event_spell', event_outcome_runtime_dependencies(counter))
        loss=SimpleNamespace(**{**base,'event_condition':{'field':'target_kind','op':'eq','value':'player'}},
            event='damage.dealt', effects=({'op':'lose_game','player':'$context.target'},))
        self.assertIn('game.loss.effect', event_outcome_runtime_dependencies(loss))
        for program in (counter,loss):
            for changes in ({'event':'step.begin'}, {'active_zone':'hand'}, {'target_schema':{'count':1}},
                    {'effects':({**program.effects[0],'extra':True},)}):
                with self.subTest(program=program,changes=changes),self.assertRaises(ValueError):
                    event_outcome_runtime_dependencies(SimpleNamespace(**{**vars(program),**changes}))
        with self.assertRaises(ValueError):
            event_outcome_runtime_dependencies(SimpleNamespace(**{**vars(loss),'event_condition':None}))

    def test_game_loss_handler_uses_the_canonical_immutable_intent_and_rejects_unknown_fields(self):
        from quorune.semantic_runtime.player_outcomes import LoseGameEffectHandler
        from quorune.semantic_runtime.context import SemanticNodeError
        active=[];context=SimpleNamespace(actor='A',default_reason='emblem occurrence',
            query=SimpleNamespace(require_known_seat=lambda player: active.append(player)))
        handler=LoseGameEffectHandler();plan=handler.lower({'op':'lose_game','player':'B'},context)
        self.assertEqual(['B'],active);self.assertEqual(('A',('B',)),(plan.intents[0].actor,plan.intents[0].players))
        for effect in ({'op':'lose_game','player':''},{'op':'lose_game','player':None},{'op':'lose_game','player':'B','reason':'override'}):
            with self.assertRaises(SemanticNodeError):handler.lower(effect,context)

class FixedEmblemEventCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'emblem-events.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def test_originals_have_normal_complete_binding_and_command_only_children(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics = SemanticRegistry(); cards = [self.db.lookup(name, fuzzy=False) for name in NAMES]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional', capability_registry=self.registry,
            capability_profile='commander_review', promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                p = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(p, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], (card.name, bound['blockers']))
                children = [n for f in ir.faces for n in f.nodes if 'compiled-emblem-child' in n.runtime_coverage]
                self.assertEqual(1, len(children)); self.assertEqual('command', children[0].active_zone)
                self.assertIn('object.emblem.compiled_abilities', children[0].capability_dependencies)

    def test_genuine_result_owner_withdrawal_is_specific_and_preserves_other_emblems(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        for capability, blocked, retained in [('stack.counter.cast_event_spell','Jace, Unraveler of Secrets','Vraska, Golgari Queen'),
                ('game.loss.effect','Vraska, Golgari Queen','Jace, Unraveler of Secrets')]:
            raw=json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
            next(r for r in raw['capabilities'] if r['id']==capability).update(status='blocked',blockers=['owner withdrawn'])
            registry=CapabilityRegistry(raw);index=build_index(registry_value=raw,
                declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),discovered_test_ids=set(discover_tests(ROOT).values()))
            registry.mark_evidence_verified(validate_capability_evidence_index(index,registry=registry))
            self.assertNotEqual('exact',compile_oracle_card(self.db.lookup(blocked),capability_registry=registry,capability_profile='commander_review').status)
            for name in (retained,'Elspeth, Knight-Errant','Sorin, Solemn Visitor','Ral, Izzet Viceroy'):
                self.assertEqual('exact',compile_oracle_card(self.db.lookup(name),capability_registry=registry,capability_profile='commander_review').status)

    def test_effect_data_keeps_result_authority_after_coverage_and_handler_annotations_are_removed(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.binding import bind_semantic_program_runtime
        semantics=SemanticRegistry();cards=[self.db.lookup(name) for name in NAMES]
        register_generated_programs(self.db,semantics,cards,trust_level='provisional',capability_registry=self.registry,
            capability_profile='commander_review',promote_exact_runtime_handlers=True,promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,promote_exact_capability_declarations=True)
        for name,capability in [('Jace, Unraveler of Secrets','stack.counter.cast_event_spell'),('Vraska, Golgari Queen','game.loss.effect'),('Sorin, Solemn Visitor','trigger.event.scheduled_player_result')]:
            card=self.db.lookup(name);program=next(p for p in semantics.programs_for_oracle(card.oracle_id)
                if p.active_zone=='command' and p.event!='characteristics.evaluate')
            deps=tuple(c for c in program.capability_dependencies if c!=capability)
            altered=replace(program,handlers=(),coverage=(),capability_dependencies=deps,
                capability_closure=self.registry.closure(deps,profile='commander_review').to_dict())
            result=bind_semantic_program_runtime(altered,capability_registry=self.registry,profile='commander_review')
            self.assertFalse(result['strict'],(name,result));self.assertTrue(any(capability in b for b in result['blockers']),result['blockers'])

class FixedEmblemEventNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'emblem-native.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Command emblem events', [DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def emblem(self, s, source, ability='ab3'):
        self.priority(s, 'A'); action_id = 'activate:'+source.ref+':'+ability
        offered = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions'] if a['id']==action_id)
        command = {'action_id':offered['id'],'pay':'auto'}; self.reject(s, 'D', command)
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(s))
        emblems = [c for c in s.state.cards.values() if c.object_kind=='emblem' and c.zone=='command']
        self.assertEqual(1, len(emblems)); self.assertEqual(('A','A'), (emblems[0].owner, emblems[0].controller))
        return emblems[0]

    def test_actual_sorin_emblem_survives_parent_and_affected_opponent_chooses_creature(self):
        s=self.session(1141001); walker=self.add(s.engine,'Sorin, Solemn Visitor'); walker.counters['loyalty']=6
        first=self.add(s.engine,'Grizzly Bears',seat='B'); second=self.add(s.engine,'Ashcoat Bear',seat='B')
        own=self.add(s.engine,'Grizzly Bears',ref='emblem-own-creature')
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='emblem-sorin-private')
        self.assert_closed(s,walker); self.prepare(s); self.checkpoint(s); emblem=self.emblem(s,walker)
        self.assertEqual('graveyard',s.state.cards[walker.object_id].zone)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        decision=self.resolve(s); self.assertEqual('choice.apnap', decision.kind)
        self.assertEqual(['B'], decision.actors)
        command={'action_id':'choose','cards':[first.ref]}; self.reject(s,'A',command)
        result=s.act('pilot:B',command); self.assertTrue(result.ok,result.summary); self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[first.object_id].zone); self.assertEqual('battlefield',s.state.cards[second.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[own.object_id].zone); self.assertEqual('command',s.state.cards[emblem.object_id].zone)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='precombat_main')
        land=next(a for a in s.packet('pilot:B',full=True)['decision']['ctx']['legal']['actions'] if a['id'].startswith('play-land:'))
        result=s.act('pilot:B',{'action_id':land['id']});self.assertTrue(result.ok,result.summary)
        self.until(s,lambda ss:ss.state.active_player=='C' and ss.state.phase=='beginning' and ss.state.step=='upkeep')
        self.assertIsNone(self.resolve(s));self.assertEqual('battlefield',s.state.cards[second.object_id].zone)
        self.assertEqual('battlefield',s.state.cards[own.object_id].zone)
        self.finish(s,private)

    def test_actual_ral_emblem_damage_is_colorless_then_draws_and_illegal_target_draws_nothing(self):
        for invalidate in (False,True):
            with self.subTest(invalidate=invalidate):
                s=self.session(1141010+invalidate); walker=self.add(s.engine,'Ral, Izzet Viceroy'); walker.counters['loyalty']=8
                target=self.add(s.engine,'Silver Knight',seat='B'); spell=self.add(s.engine,'Shock',zone='hand')
                bounce=self.add(s.engine,'Unsummon',seat='B',zone='hand'); self.add(s.engine,'Island',seat='B')
                private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='emblem-ral-private')
                self.assert_closed(s,walker); self.prepare(s,mana={'R':1}); self.checkpoint(s); self.emblem(s,walker)
                self.cast_for(s,'A',spell,['D']); self.until(s,lambda ss:ss.state.pending_decision.kind=='semantic.target')
                result=s.act('pilot:A',{'action_id':'choose','targets':[target.ref]}); self.assertTrue(result.ok,result.summary)
                before=len(s.state.players['A'].zones['hand'])
                if invalidate: self.cast_for(s,'B',bounce,[target.ref]); self.resolve_top(s)
                self.assertIsNone(self.resolve(s)); self.assertEqual(before+(0 if invalidate else 2),len(s.state.players['A'].zones['hand']))
                self.assertEqual('hand' if invalidate else 'graveyard',s.state.cards[target.object_id].zone)
                self.assertEqual(38,s.state.players['D'].life); self.finish(s,private)

    def test_actual_jace_counts_each_opponent_first_spell_per_turn_including_prior_casts(self):
        s=self.session(1141020);walker=self.add(s.engine,'Jace, Unraveler of Secrets');walker.counters['loyalty']=8
        spells={seat:[self.add(s.engine,'Shock',seat=seat,zone='hand',ref='emblem-first-'+seat+str(i)) for i in range(3)] for seat in ('B','C')}
        private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='emblem-jace-private')
        self.add(s.engine,'Mountain',seat='B')
        for seat in ('B','C'):s.state.players[seat].mana_pool['R']=3
        self.assert_closed(s,walker);self.prepare(s);self.checkpoint(s)
        self.cast_for(s,'B',spells['B'][0],['D']);self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['D'].life)
        self.emblem(s,walker);self.cast_for(s,'B',spells['B'][1],['D']);self.assertIsNone(self.resolve(s))
        self.assertEqual(36,s.state.players['D'].life)
        self.cast_for(s,'C',spells['C'][0],['D']);self.assertIsNone(self.resolve(s));self.assertEqual(36,s.state.players['D'].life)
        self.cast_for(s,'C',spells['C'][1],['D']);self.assertIsNone(self.resolve(s));self.assertEqual(34,s.state.players['D'].life)
        self.until(s,lambda ss:ss.state.active_player=='B' and ss.state.phase=='precombat_main')
        self.cast_for(s,'B',spells['B'][2],['D']);self.assertIsNone(self.resolve(s))
        self.assertEqual(34,s.state.players['D'].life)
        self.assertEqual('graveyard',s.state.cards[walker.object_id].zone)
        self.finish(s,private)

    def test_actual_jace_uncounterable_first_spell_still_consumes_that_cast_ordinal(self):
        s=self.session(1141021);walker=self.add(s.engine,'Jace, Unraveler of Secrets');walker.counters['loyalty']=8
        first=self.add(s.engine,'Void Rend',seat='B',zone='hand');second=self.add(s.engine,'Shock',seat='B',zone='hand')
        target=self.add(s.engine,'Grizzly Bears');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='emblem-uncounterable-private')
        s.state.players['B'].mana_pool.update({'W':1,'U':1,'B':1,'R':1})
        self.assert_closed(s,first);self.prepare(s);self.checkpoint(s);self.emblem(s,walker)
        self.cast_for(s,'B',first,[target.ref],reserve={'R':1});self.assertIsNone(self.resolve(s))
        self.assertEqual('graveyard',s.state.cards[target.object_id].zone)
        self.cast_for(s,'B',second,['D']);self.assertIsNone(self.resolve(s));self.assertEqual(38,s.state.players['D'].life);self.finish(s,private)

    def vraska_damage_scene(self,seed):
        s=self.session(seed);walker=self.add(s.engine,'Vraska, Golgari Queen');walker.counters['loyalty']=9
        attacker=self.add(s.engine,'Grizzly Bears');second=self.add(s.engine,'Grizzly Bears',ref='emblem-second-attacker');self.add(s.engine,'Fervor')
        spell=self.add(s.engine,'Shock',zone='hand');private=self.add(s.engine,'Counterspell',seat='C',zone='hand',ref='emblem-vraska-private')
        self.assert_closed(s,walker);self.prepare(s,mana={'R':1});self.checkpoint(s);emblem=self.emblem(s,walker)
        self.cast_for(s,'A',spell,['D']);self.assertIsNone(self.resolve(s));self.assertTrue(s.state.players['D'].in_game)
        self.attack(s,[(attacker,'B'),(second,'B')]);self.until(s,lambda ss:ss.state.pending_decision.kind=='priority' and sum(i.kind=='triggered_ability' and i.source_object_id==emblem.object_id for i in ss.state.stack)==2)
        return s,private

    def test_actual_vraska_emblem_eliminates_only_the_combat_damaged_player_after_parent_dies(self):
        s,private=self.vraska_damage_scene(1141030);self.assertIsNone(self.resolve(s))
        self.assertEqual([True,False,True,True],[s.state.players[seat].in_game for seat in 'ABCD'])
        self.assertEqual(['B'],s.state.eliminated_players);self.finish(s,private)

    def test_actual_player_loss_fault_restores_departing_players_objects_and_retries(self):
        from quorune.engine import CommanderEngine
        from quorune.errors import GameRuleError
        s,private=self.vraska_damage_scene(1141031);original=CommanderEngine._eliminate_players;calls=[]
        def fault(host,seats,*,reason):
            original(host,seats,reason=reason);calls.append(tuple(seats));raise GameRuleError('emblem loss rollback witness')
        with patch.object(CommanderEngine,'_eliminate_players',fault):
            for _ in range(12):
                before=authoritative_state_hash(s.state);actor=s.pending_principals()[0];result=s.act(actor,{'action_id':'pass'})
                if not result.ok:self.assertEqual(before,authoritative_state_hash(s.state));break
            else:self.fail('Canonical player-loss fault did not reach command boundary')
        self.assertEqual([('B',)],calls);self.assertTrue(s.state.players['B'].in_game)
        self.assertTrue(s.act(actor,{'action_id':'pass'}).ok);self.assertIsNone(self.resolve(s));self.assertFalse(s.state.players['B'].in_game);self.finish(s,private)

    def test_fresh_player_loss_omission_mutant_is_killed(self):
        from quorune.semantic_runtime.player_outcomes import LoseGameEffectHandler
        from quorune.semantic_runtime.intents import IntentPlan
        with patch.object(LoseGameEffectHandler,'lower',return_value=IntentPlan(operation='lose_game',handler_id='generic.lose-game-effect.v1',intents=(),result_shape='by_player')):
            with self.assertRaises(AssertionError):self.test_actual_vraska_emblem_eliminates_only_the_combat_damaged_player_after_parent_dies()

    def test_fresh_cast_counter_omission_mutant_is_killed(self):
        from quorune.semantic_runtime.stack_counter_handlers import CounterStackTargetHandler
        from quorune.semantic_runtime.intents import IntentPlan
        with patch.object(CounterStackTargetHandler,'lower',return_value=IntentPlan(operation='counter_stack_target',handler_id='generic.counter-stack-target.v1',intents=(),result_shape='by_player')):
            with self.assertRaises(AssertionError):self.test_actual_jace_counts_each_opponent_first_spell_per_turn_including_prior_casts()
