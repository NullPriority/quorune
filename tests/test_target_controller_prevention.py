"""A targeted source controller is separate from the shield's creator (CR 615)."""
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import json
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.damage_modifier_state import DamagePreventionScope
from quorune.rules.target_controller_prevention import (
    CAPABILITY, MECHANIC, VERSION_FIELD, TARGET_FIELD, bound_prevention_player,
    player_target_schema, targeted_controller_prevention_effect,
    targeted_controller_prevention_capabilities, targeted_prevention_scope,
    targeted_prevention_player_unavailable,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from test_bound_effect_programs import record


class TargetControllerPreventionValueTests(unittest.TestCase):
    def test_fixed_player_scope_preserves_legacy_encoding_and_rejects_ambiguous_bindings(self):
        old = DamagePreventionScope().to_dict()
        self.assertNotIn('source_controller', old)
        self.assertEqual(old, DamagePreventionScope.from_dict(old).to_dict())
        scope = DamagePreventionScope(source_controller='B', source_characteristics_all=('creature',))
        self.assertEqual(scope, DamagePreventionScope.from_dict(scope.to_dict()))
        self.assertEqual({'source_controller': {'eq': 'B'},
                          'source_characteristics': {'contains_all': ['creature']}},
                         scope.event_conditions(controller='A'))
        for change in ({'source_controller': None}, {'source_controller': ''},
                       {'source_controller': True}, {'source_controller': ['B']},
                       {'source_controller': 'B', 'source_controller_relation': 'opponent'},
                       {'source_controller': 'B', 'unknown': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                DamagePreventionScope.from_dict({**old, **change})

    def test_instruction_is_closed_and_missing_or_departed_player_cannot_widen_the_scope(self):
        effect = targeted_controller_prevention_effect()
        self.assertEqual('$target.0', bound_prevention_player(effect))
        self.assertIn(CAPABILITY, targeted_controller_prevention_capabilities(
            effects=(effect,), target_schema=player_target_schema(), mechanic_ids=(MECHANIC,)))
        for change in ({VERSION_FIELD: True}, {VERSION_FIELD: 2}, {TARGET_FIELD: None},
                       {TARGET_FIELD: 'B'}, {'damage_kind': 'any'}, {'subject': '$target.0'},
                       {'mode': 'amount'}, {'scope': {}}, {'unknown': 1}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                bound_prevention_player({**effect, **change})
        host = SimpleNamespace(state=SimpleNamespace(players={
            'A': SimpleNamespace(in_game=True), 'B': SimpleNamespace(in_game=False)}))
        runtime = {**effect, 'source': 'fissure', TARGET_FIELD: 'B'}
        self.assertTrue(targeted_prevention_player_unavailable(host, runtime))
        self.assertEqual('B', targeted_prevention_scope(runtime).source_controller)
        self.assertTrue(targeted_prevention_player_unavailable(host, {**runtime, TARGET_FIELD: None}))
        for target in (None, 'unknown'):
            with self.assertRaises(ValueError):
                if target is None: targeted_prevention_scope({**runtime, TARGET_FIELD: target})
                else: targeted_prevention_player_unavailable(host, {**runtime, TARGET_FIELD: target})

    def test_runtime_minimum_requires_the_binding_without_producer_annotations(self):
        from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
        effect = targeted_controller_prevention_effect('$target_group.clause_0.0')
        self.assertIn(CAPABILITY, typed_result_amount_runtime_dependencies(effect))
        self.assertIn('target.revalidate_resolution', typed_result_amount_runtime_dependencies(effect))
        for schema in (None, {**player_target_schema(), 'count': 2}, {**player_target_schema(), 'count': True},
                       {**player_target_schema(), 'player_relation': 'controller'}):
            self.assertFalse(targeted_controller_prevention_capabilities(
                effects=(targeted_controller_prevention_effect(),), target_schema=schema, mechanic_ids=(MECHANIC,)))
        missing_version = dict(effect)
        missing_version.pop(VERSION_FIELD)
        with self.assertRaises(ValueError): typed_result_amount_runtime_dependencies(missing_version)

    def test_source_controller_omission_mutant_is_killed_by_an_independent_other_controller_control(self):
        def witness():
            conditions = DamagePreventionScope(source_controller='B').event_conditions(controller='A')
            self.assertEqual({'eq': 'B'}, conditions.get('source_controller'))
        witness()
        original = DamagePreventionScope.event_conditions
        def omitted(scope, *, controller):
            return {key: value for key, value in original(scope, controller=controller).items()
                    if key != 'source_controller'}
        with patch.object(DamagePreventionScope, 'event_conditions', omitted), self.assertRaises(AssertionError):
            witness()


class TargetControllerPreventionCompilerTests(unittest.TestCase):
    def test_closed_player_grammar_preserves_target_relation_and_unknown_neighbors_stay_residual(self):
        registry = load_default_capability_registry()
        for relation in ('opponent', 'player'):
            for body in (f'Prevent all combat damage that would be dealt this turn by creatures target {relation} controls.',
                         f'Prevent all combat damage that would be dealt by creatures target {relation} controls this turn.'):
                ir = compile_oracle_card(record(body), capability_registry=registry)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertIn(CAPABILITY, node.capability_dependencies)
                self.assertEqual('opponent' if relation == 'opponent' else 'any', node.target_schema['player_relation'])
        for body in ('Prevent all combat damage that would be dealt this turn by creatures target opponent owns.',
                     'Prevent all combat damage that would be dealt this turn by creatures two target opponents control.',
                     'Prevent all combat damage that would be dealt this turn by creatures target opponent controls. Rewrite history.'):
            self.assertNotEqual('exact', compile_oracle_card(record(body), capability_registry=registry).status)

    def test_withdrawing_bound_player_authority_fails_closed_without_withdrawing_ordinary_prevention(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests
        raw = json.loads((ROOT/'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        next(r for r in raw['capabilities'] if r['id'] == CAPABILITY).update(status='blocked', blockers=['player owner withdrawn'])
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
            declaration_source=read_evidence_document(ROOT/'platform/capability-evidence-declarations.json'),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        text = 'Prevent all combat damage that would be dealt this turn by creatures target opponent controls.'
        self.assertNotEqual('exact', compile_oracle_card(record(text), capability_registry=registry).status)
        self.assertEqual('exact', compile_oracle_card(record('Prevent all combat damage that would be dealt this turn.'),
                                                   capability_registry=registry).status)


from pathlib import Path
import tempfile
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from scripts.build_test_database import build_fixture_database
import test_target_recipient_results as recipients


class TargetControllerPreventionNativeTests(InheritedNativeFixture, unittest.TestCase):
    reject = recipients.TargetRecipientNativeTests.reject
    finish = recipients.TargetRecipientNativeTests.finish

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((ROOT/'tests/fixtures/bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [row for row in base['cards'] if row['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        (folder/'basic.json').write_text(json.dumps(base), encoding='utf-8')
        path = folder/'target-controller.sqlite3'
        build_fixture_database([folder/'basic.json', ROOT/'tests/fixtures/awaken-cards.json',
                                ROOT/'tests/fixtures/target-controller-prevention-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Target controller prevention',
            [DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30)], ['Sliver Hivelord'])

    def scene(self):
        s = self.session(6151007)
        source = self.add(s.engine, 'Encircling Fissure', zone='hand')
        lands = [self.add(s.engine, 'Plains', ref='fissure-mana-'+str(i)) for i in range(5)]
        for i in range(4): self.add(s.engine, 'Mountain', seat='B', ref='fissure-red-'+str(i))
        attacker = self.add(s.engine, 'Grizzly Bears', seat='B', ref='fissure-B-attacker')
        blocker = self.add(s.engine, 'Grizzly Bears', seat='C', ref='fissure-C-blocker')
        stolen = self.add(s.engine, 'Grizzly Bears', seat='C', ref='fissure-C-to-B')
        theft = self.add(s.engine, 'Act of Treason', seat='B', zone='hand')
        shock = self.add(s.engine, 'Shock', seat='B', zone='hand')
        private = self.add(s.engine, 'Counterspell', seat='C', zone='hand', ref='fissure-private-C')
        attacker.acquired_control_turn_count = -1
        s.state.config.semantic_policy = 'trusted_only'
        for card in (source, theft, shock): self.assert_closed(s, card)
        self.prepare(s)
        self.checkpoint(s)
        self.play_land(s, 'A')
        self.until(s, lambda game: game.state.active_player == 'B' and game.state.phase == 'precombat_main'
                   and game.state.pending_decision.kind == 'priority')
        self.play_land(s, 'B')
        return s, source, lands[0], attacker, blocker, stolen, theft, shock, private

    def play_land(self, s, seat):
        self.priority(s, seat)
        row = next(row for row in s.packet('pilot:'+seat, full=True)['decision']['ctx']['legal']['actions']
                   if row['id'].startswith('play-land:'))
        result = s.act('pilot:'+seat, {'action_id': row['id']})
        self.assertTrue(result.ok, result.summary)

    def cast_fissure(self, s, source, land, *, paid):
        self.priority(s, 'A')
        row = self.action(s, 'A', s.state.cards[source.object_id])
        option = 'awaken' if paid else next(x['id'] for x in row['cost_options'] if x['id'] != 'awaken')
        command = {'action_id': row['id'], 'cost_option': option,
                   'targets': ['B', land.ref] if paid else ['B'], 'pay': 'auto'}
        self.reject(s, 'D', command)
        self.reject(s, 'A', {**command, 'targets': ['A', land.ref] if paid else ['A']})
        result = s.act('pilot:A', command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(5 if paid else 3, s.state.stack[-1].context['mana_spent_total'])

    def combat(self, *, paid, replay=True):
        s, source, land, attacker, blocker, stolen, theft, shock, private = self.scene()
        life = {seat: s.state.players[seat].life for seat in 'ABCD'}
        self.cast_fissure(s, source, land, paid=paid)
        self.assertIsNone(self.resolve(s))
        shield, = s.state.damage_prevention_shields
        self.assertEqual('A', shield.controller)
        self.assertEqual('B', shield.scope.source_controller)
        self.assertEqual(2 if paid else 0, s.state.cards[land.object_id].counters.get('+1/+1', 0))
        self.cast(s, 'B', theft, [stolen.ref])
        self.assertIsNone(self.resolve(s))
        self.assertEqual('B', s.state.cards[stolen.object_id].controller)
        self.assertEqual('C', s.state.cards[stolen.object_id].owner)
        self.cast(s, 'B', shock, ['A'])
        self.assertIsNone(self.resolve(s))
        self.assertEqual(life['A']-2, s.state.players['A'].life)
        self.until(s, lambda game: game.state.pending_decision.kind == 'combat.attackers')
        attack = {'action_id': 'attack', 'attackers': {attacker.ref: 'C', stolen.ref: 'D'}}
        self.reject(s, 'A', attack)
        result = s.act('pilot:B', attack)
        self.assertTrue(result.ok, result.summary)
        self.until(s, lambda game: game.state.pending_decision.kind == 'combat.blockers'
                   and game.state.pending_decision.actors == ['C'])
        command = {'action_id': 'block', 'blocks': {blocker.ref: attacker.ref}}
        self.reject(s, 'B', command)
        result = s.act('pilot:C', command)
        self.assertTrue(result.ok, result.summary)
        if replay: s = self.replay(s, load=True)
        self.until(s, lambda game: game.state.phase == 'postcombat_main')
        self.assertEqual('graveyard', s.state.cards[attacker.object_id].zone)
        self.assertEqual('battlefield', s.state.cards[blocker.object_id].zone)
        self.assertEqual(0, s.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(life['D'], s.state.players['D'].life)
        if replay:
            self.until(s, lambda game: game.state.active_player == 'C' and game.state.phase == 'precombat_main')
            self.assertFalse(s.state.damage_prevention_shields)
            self.assertEqual('C', s.state.cards[stolen.object_id].controller)
            self.finish(s, private)

    def test_actual_ordinary_and_awaken_prevent_only_selected_controller_combat_after_control_change(self):
        for paid in (False, True):
            with self.subTest(paid=paid): self.combat(paid=paid)

    def test_actual_departed_opponent_cannot_become_an_unrestricted_shield_when_land_target_remains_legal(self):
        for paid in (False, True):
            with self.subTest(paid=paid):
                s, source, land, attacker, blocker, stolen, theft, shock, private = self.scene()
                from quorune.errors import GameRuleError
                from quorune.record import authoritative_state_hash
                runtime = {**targeted_controller_prevention_effect(), 'source': source.ref, TARGET_FIELD: 'B'}
                missing_version = dict(runtime)
                missing_version.pop(VERSION_FIELD)
                for malformed in ({**runtime, VERSION_FIELD: True}, {**runtime, TARGET_FIELD: 'unknown'},
                                  {**runtime, TARGET_FIELD: None, 'damage_kind': 'any'}, missing_version):
                    before = authoritative_state_hash(s.state)
                    with self.assertRaises(GameRuleError): s.engine.apply_effect(malformed, actor='A')
                    self.assertEqual(before, authoritative_state_hash(s.state))
                self.cast_fissure(s, source, land, paid=paid)
                self.priority(s, 'B')
                result = s.act('pilot:B', {'action_id': 'concede', 'choices': {'confirm_concede': True}})
                self.assertTrue(result.ok, result.summary)
                s = self.replay(s, load=True)
                self.assertIsNone(self.resolve(s))
                self.assertFalse(s.state.damage_prevention_shields)
                self.assertEqual(2 if paid else 0, s.state.cards[land.object_id].counters.get('+1/+1', 0))
                self.finish(s, private)

    def test_actual_other_controller_blocker_kills_the_attacker_when_source_predicate_is_omitted(self):
        original = DamagePreventionScope.event_conditions
        def omitted(scope, **kwargs):
            return {key: value for key, value in original(scope, **kwargs).items()
                    if not (scope.source_controller is not None and key == 'source_controller')}
        with patch.object(DamagePreventionScope, 'event_conditions', omitted), self.assertRaisesRegex(AssertionError, "'graveyard' != 'battlefield'"):
            self.combat(paid=False, replay=False)
