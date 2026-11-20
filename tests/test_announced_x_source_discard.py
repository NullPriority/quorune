"""CR107.3k/602: hand self-discard and independent announced activation X."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.abilities import ActivatedAbility, parse_activated_abilities, reduced_requirements
from quorune.activation_x import (ANNOUNCED_ACTIVATION_X_SOURCE_DISCARD_CAPABILITY as CAP,
    activation_x_symbols, source_discard_activation_x_program_dependencies)
from quorune.compiler.activation_x_costs import announced_x_activation_cost
from quorune.compiler.activated_costs import activated_ability_cost
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.record import authoritative_state_hash
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients

NAMES = ['Arashi, the Sky Asunder', 'Jiwari, the Earth Aflame', 'Ghost-Lit Drifter']
FIXTURE = ROOT/'tests/fixtures/announced-x-source-discard-cards.json'


class HandDiscardActivationXModelTests(unittest.TestCase):
    def ability(self, cost='{X}{G}{G}'):
        raw, = parse_activated_abilities(card_name='Generic Source',
            oracle_text='Channel — '+cost+', Discard this card: You gain X life.', keywords=())
        return announced_x_activation_cost(raw)

    def test_closed_hand_price_roundtrips_without_reinterpreting_old_costs(self):
        for cost, symbols in [('{X}{G}{G}', 1), ('{X}{X}{U}', 2)]:
            ability = self.ability(cost)
            self.assertEqual(('hand',), ability.zones); self.assertTrue(ability.discard_source)
            self.assertEqual(symbols, ability.announced_x_symbols); self.assertTrue(ability.compiled_cost)
            self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
            self.assertEqual(symbols*3, reduced_requirements(ability, announced_x=3)['GENERIC'])
        old, = parse_activated_abilities(card_name='Generic Source', oracle_text='Channel — {2}, Discard this card: Draw a card.')
        self.assertNotIn('announced_x_symbols', old.to_dict())
        self.assertIsNone(activation_x_symbols('{X}{G}, Discard this card'))

    def test_hand_price_rejects_other_origins_and_mixed_or_changed_cost_data(self):
        a = self.ability()
        for changes in ({'zones': ('battlefield',)}, {'zones': ('graveyard',)}, {'discard_source': False},
                {'tap_source': True}, {'sacrifice_source': True}, {'exile_source': True},
                {'announced_x_symbols': True}, {'announced_x_symbols': 2}, {'mana': {'G': 1}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError): replace(a, **changes)
        for text in ('{X}{G}', '{X}{G}, {T}, Discard this card, Sacrifice this creature',
                '{X}, Discard a card', '{X}, Exile this card', '{X}, Discard this card, Pay 2 life'):
            self.assertIsNone(activation_x_symbols(text, allow_source_discard=True))

    def test_runtime_minimum_comes_from_the_price_and_checks_its_origin(self):
        a = self.ability(); cost = activated_ability_cost(a)
        p = SimpleNamespace(event='activate', active_zone='hand', cost_schema=cost)
        self.assertIn(CAP, source_discard_activation_x_program_dependencies(p))
        for changes in ({'event': 'resolve'}, {'active_zone': 'battlefield'},
                {'cost_schema': {**cost, 'announced_x_symbols': True}},
                {'cost_schema': {**cost, 'mana': {'G': 1}}},
                {'cost_schema': {**cost, 'tap_source': True}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                source_discard_activation_x_program_dependencies(SimpleNamespace(**{**vars(p), **changes}))

    def test_announced_keyword_targets_keep_exact_count_and_old_any_number_shape(self):
        from quorune.compiler.announced_target_sets import announced_target_set_effect_template
        from quorune.compiler.any_number_target_sets import any_number_target_set_template
        from quorune.rules.fixed_homogeneous_target_set_capability_shapes import announced_target_set_node_capabilities
        from quorune.target_cardinality import bind_announced_target_schema
        compiled = announced_target_set_effect_template('X target creatures gain flying until end of turn.')
        self.assertIsNotNone(compiled); effect = compiled[1][0]; schema = compiled[2]
        self.assertEqual('x', schema['count_parameter']['parameter'])
        self.assertEqual(0, bind_announced_target_schema(schema, 0)['count'])
        self.assertEqual(8, bind_announced_target_schema(schema, 8)['count'])
        deps = announced_target_set_node_capabilities(effects=compiled[1], target_schema=schema, mechanic_ids=compiled[3])
        self.assertIn('continuous.resolution.fixed_target_characteristic_set', deps)
        self.assertNotIn('resolution.effect.fixed_homogeneous_target_set', deps)
        changed = {**effect, 'maximum_targets': {'kind': 'cast_x_effect_amount', 'schema_version': 1, 'coefficient': -1}}
        self.assertFalse(announced_target_set_node_capabilities(effects=(changed,), target_schema=schema, mechanic_ids=compiled[3]))
        old = any_number_target_set_template('Any number of target creatures gain flying until end of turn.')
        self.assertEqual('any_number', old[2]['count_parameter']['parameter'])


class HandDiscardActivationXCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'hand-x.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls): cls.db.close(); cls.temporary.cleanup()

    def test_three_complete_originals_and_their_other_printed_abilities(self):
        from quorune.semantics import SemanticRegistry
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime
        semantics = SemanticRegistry(); cards = [self.db.lookup(n, fuzzy=False) for n in NAMES]
        register_generated_programs(self.db, semantics, cards, trust_level='provisional',
            capability_registry=self.registry, capability_profile='commander_review',
            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(name=card.name):
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
                self.assertEqual('exact', ir.status, ir.material_residuals)
                for face in ir.faces:
                    for node in face.nodes: self.assertEqual(node.text, card.oracle_text[node.span.start:node.span.end])
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics,
                    capability_registry=self.registry, capability_profile='commander_review')
                bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
                self.assertTrue(bound['strict_capability_ready'], (card.name, bound['blockers']))
                hand = next(p for p in semantics.programs_for_oracle(card.oracle_id, event='activate') if p.active_zone == 'hand')
                deps = tuple(c for c in hand.capability_dependencies if c != CAP)
                changed = replace(hand, handlers=(), coverage=(), capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile='commander_review').to_dict())
                check = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile='commander_review')
                self.assertFalse(check['strict']); self.assertIn('capability:undeclared_runtime_dependency:'+CAP, check['blockers'])

    def test_unrelated_channel_siblings_remain_residual(self):
        card = self.db.lookup('Shigeki, Jukai Visionary', fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile='commander_review')
        self.assertNotEqual('exact', ir.status); self.assertTrue(ir.material_residuals)

    def test_verified_withdrawal_keeps_old_hand_and_battlefield_costs_and_concrete_target_owners_distinct(self):
        from copy import deepcopy
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT/'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())
        for missing in (CAP, 'resolution.effect.fixed_homogeneous_target_set'):
            changed = deepcopy(raw)
            next(r for r in changed['capabilities'] if r['id'] == missing).update(status='blocked', blockers=['owner withdrawn'])
            registry = CapabilityRegistry(changed)
            index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            for name in NAMES:
                ir = compile_oracle_card(self.db.lookup(name), capability_registry=registry, capability_profile='commander_review')
                self.assertEqual('exact' if missing != CAP else 'partial', ir.status, ir.material_residuals)
            for name in ("Belbe's Armor", 'Mnemonic Sphere'):
                self.assertEqual('exact', compile_oracle_card(self.db.lookup(name), capability_registry=registry,
                    capability_profile='commander_review').status)


class HandDiscardActivationXNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(); path = Path(cls.temporary.name)/'hand-x-native.sqlite3'
        build_fixture_database([FIXTURE], path); cls.db = CardDatabase(path); cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Hand activation X', [DeckEntry('Sliver Hivelord', 1, 'commander'),
            DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def offer_x(self, s, source):
        self.priority(s, 'A')
        offered = [a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
            if a['id'].startswith('activate:'+source.ref+':') and a.get('choice_schema', {}).get('x')]
        self.assertTrue(offered, 'Expected the supported printed X activation')
        return offered[0]

    def activate_x(self, s, source, x, targets=(), *, reserve=None):
        offer = self.offer_x(s, source)
        command = {'action_id': offer['id'], 'x': x, 'targets': list(targets), 'pay': 'auto', 'reserve': reserve or {}}
        self.reject(s, 'B', command); result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary)
        self.assertEqual(x, s.state.stack[-1].x_value)

    def test_actual_arashi_and_jiwari_pay_discard_even_at_zero_and_filter_current_creatures(self):
        for name, color, fixed, flying in [(NAMES[0], 'G', 2, True), (NAMES[1], 'R', 3, False)]:
            for x in (0, 2):
                with self.subTest(name=name, x=x):
                    s = self.session(810010+len(name)+x); source = self.add(s.engine, name, zone='hand')
                    air = self.add(s.engine, "Healer's Hawk", seat='B'); ground = self.add(s.engine, 'Grizzly Bears', seat='D')
                    self.add(s.engine, 'Whip of Erebos'); own = self.add(s.engine, 'Colossal Dreadmaw')
                    private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-filter-private')
                    self.assert_closed(s, source); self.prepare(s, mana={'C': x, color: fixed}); self.checkpoint(s)
                    offer = self.offer_x(s, source)
                    for bad in (None, True, -1, 1.5, '2', 8):
                        self.reject(s, 'A', {'action_id': offer['id'], 'x': bad, 'targets': [], 'pay': 'auto'})
                    self.activate_x(s, source, x); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
                    self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.assertIsNone(self.resolve(s))
                    self.assertEqual('graveyard' if flying and x else 'battlefield', s.state.cards[air.object_id].zone)
                    self.assertEqual('graveyard' if not flying and x else 'battlefield', s.state.cards[ground.object_id].zone)
                    self.assertEqual(0 if flying else x, s.state.cards[own.object_id].marked_damage)
                    self.assertEqual(40, s.state.players['A'].life)
                    self.assertEqual(0, len(s.engine._current_turn_history('spell_cast')))
                    self.reject(s, 'A', {'action_id': offer['id'], 'x': 0, 'pay': 'auto'}); self.finish(s, private)

    def test_actual_ghost_exact_targets_reject_before_discard_and_revalidate_after_bounce(self):
        s = self.session(810100); source = self.add(s.engine, NAMES[2], zone='hand')
        first = self.add(s.engine, 'Grizzly Bears', ref='hand-x-first-bear')
        second = self.add(s.engine, 'Grizzly Bears', seat='B', ref='hand-x-second-bear')
        bounce = self.add(s.engine, 'Unsummon', seat='B', zone='hand'); self.add(s.engine, 'Island', seat='B')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-ghost-private')
        self.assert_closed(s, source); self.prepare(s, mana={'C': 2, 'U': 1}); self.checkpoint(s); offer = self.offer_x(s, source)
        for refs in ([first.ref], [first.ref, first.ref], [first.ref, source.ref]):
            self.reject(s, 'A', {'action_id': offer['id'], 'x': 2, 'targets': refs, 'pay': 'auto'})
        self.activate_x(s, source, 2, [first.ref, second.ref]); self.cast(s, 'B', bounce, [second.ref]); self.resolve_top(s)
        s = self.replay(s, load=True); self.assertIsNone(self.resolve(s))
        self.assertEqual('hand', s.state.cards[second.object_id].zone)
        self.assertIn('flying', s.engine._combat_keywords(s.state.cards[first.object_id]))
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.step == 'upkeep')
        self.assertNotIn('flying', s.engine._combat_keywords(s.state.cards[first.object_id])); self.finish(s, private)

    def test_actual_battlefield_siblings_keep_their_prices_and_predicates(self):
        for index, name in enumerate(NAMES):
            s = self.session(810200+index); source = self.add(s.engine, name); self.add(s.engine, 'Fervor')
            air = self.add(s.engine, "Healer's Hawk", seat='B'); ground = self.add(s.engine, 'Grizzly Bears', seat='D')
            private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-battlefield-private')
            self.assert_closed(s, source); self.prepare(s, mana={'C': 2, 'G': 1, 'R': 1, 'U': 1}); self.checkpoint(s)
            if index < 2:
                offer = self.offer_x(s, source); correct, wrong = (air, ground) if index == 0 else (ground, air)
                self.reject(s, 'A', {'action_id': offer['id'], 'x': 1, 'targets': [wrong.ref], 'pay': 'auto'})
                self.activate_x(s, source, 1, [correct.ref]); self.assertIsNone(self.resolve(s))
                self.assertTrue(s.state.cards[source.object_id].tapped)
                self.assertEqual('graveyard' if index == 0 else 'battlefield', s.state.cards[correct.object_id].zone)
            else:
                self.assertIn('flying', s.engine._combat_keywords(source))
                offer = next(a for a in s.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
                    if a['id'].startswith('activate:'+source.ref+':'))
                self.reject(s, 'A', {'action_id': offer['id'], 'targets': [source.ref], 'pay': 'auto'})
                result = s.act('pilot:A', {'action_id': offer['id'], 'targets': [ground.ref], 'pay': 'auto'})
                self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s))
                self.assertIn('flying', s.engine._combat_keywords(ground))
            self.assertEqual('battlefield', s.state.cards[source.object_id].zone); self.finish(s, private)

    def test_actual_creature_permanent_reducers_do_not_discount_hand_channel(self):
        s = self.session(810300); source = self.add(s.engine, NAMES[0], zone='hand')
        self.add(s.engine, 'Heartstone'); self.add(s.engine, 'Training Grounds')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-reducer-private')
        self.prepare(s, mana={'C': 1, 'G': 2}); self.checkpoint(s); offer = self.offer_x(s, source)
        self.reject(s, 'A', {'action_id': offer['id'], 'x': 2, 'targets': [], 'pay': 'auto'})
        self.activate_x(s, source, 1); self.assertIsNone(self.resolve(s)); self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(s.state.players['A'].mana_pool.values())); self.finish(s, private)

    def test_actual_new_battlefield_incarnation_cannot_lend_lifelink_to_old_channel(self):
        s = self.session(810400); source = self.add(s.engine, NAMES[1], zone='hand')
        self.add(s.engine, 'Whip of Erebos'); self.add(s.engine, 'Vedalken Orrery')
        victim = self.add(s.engine, 'Grizzly Bears', seat='B'); self.add(s.engine, 'Colossal Dreadmaw')
        reanimate = self.add(s.engine, 'Zombify', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-incarnation-private')
        self.prepare(s, mana={'C': 5, 'R': 3, 'B': 1}); self.checkpoint(s)
        self.activate_x(s, source, 2, reserve={'C': 3, 'B': 1}); self.cast(s, 'A', reanimate, [source.ref]); self.resolve_top(s)
        current = s.state.cards[source.object_id]; self.assertEqual('battlefield', current.zone)
        self.assertIn('lifelink', s.engine._combat_keywords(current)); self.assertIsNone(self.resolve(s))
        self.assertEqual(40, s.state.players['A'].life); self.assertEqual('graveyard', s.state.cards[victim.object_id].zone)
        self.finish(s, private)

    def test_actual_source_discard_failure_rolls_back_mana_and_stack(self):
        from quorune.errors import GameRuleError
        s = self.session(810500); source = self.add(s.engine, NAMES[0], zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='hand-x-fault-private')
        self.prepare(s, mana={'C': 2, 'G': 2}); self.checkpoint(s); offer = self.offer_x(s, source)
        command = {'action_id': offer['id'], 'x': 2, 'targets': [], 'pay': 'auto'}
        before = authoritative_state_hash(s.state); move = s.engine.move_card; calls = []
        def failed_move(object_id, zone, **kwargs):
            if object_id == source.object_id and zone == 'graveyard':
                calls.append(True); raise GameRuleError('injected self-discard failure')
            return move(object_id, zone, **kwargs)
        with patch.object(s.engine, 'move_card', side_effect=failed_move):
            self.assertFalse(s.act('pilot:A', command).ok)
        self.assertTrue(calls); self.assertEqual(before, authoritative_state_hash(s.state))
        result = s.act('pilot:A', command); self.assertTrue(result.ok, result.summary); self.assertIsNone(self.resolve(s)); self.finish(s, private)

    def test_existing_copy_transition_retains_x_without_paying_source_again(self):
        # This is a rule-owner boundary check, not an admitted ability-copy card.
        s = self.session(810600); source = self.add(s.engine, NAMES[0], zone='hand')
        self.prepare(s, mana={'C': 2, 'G': 2}); self.activate_x(s, source, 2)
        original = s.state.stack[-1]; mana = dict(s.state.players['A'].mana_pool)
        copy = s.engine._copy_stack_item(controller='A', target=original, targets=[], target_groups={}, reason='typed X copy boundary')
        self.assertEqual(2, copy.x_value); self.assertEqual(original.semantic_key, copy.semantic_key)
        self.assertEqual('graveyard', s.state.cards[source.object_id].zone)
        self.assertEqual(mana, s.state.players['A'].mana_pool)

    def test_fresh_live_missing_x_price_mutant_is_killed(self):
        import quorune.rules.activation.x_costs as prices
        original = prices.activation_requirements
        def free_x(host, seat, source, ability, response=None, *, targets=None):
            requirements, value = original(host, seat, source, ability, response, targets=targets)
            if ability.zones == ('hand',) and ability.announced_x_symbols is not None:
                requirements['GENERIC'] = 0
            return requirements, value
        with patch.object(prices, 'activation_requirements', free_x):
            with self.assertRaises(AssertionError):
                self.test_actual_creature_permanent_reducers_do_not_discount_hand_channel()
