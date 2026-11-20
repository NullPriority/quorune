"""One controlled creature qualifies using both stats from its declaration."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.qualified_attack_model import (
    ATTACK_FACT_EQUAL_POWER_TOUGHNESS, ATTACK_FACT_PREFIX,
    declared_attacker_has_equal_power_and_toughness,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SOURCE = 'Master Symmetrist'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
ORIGINALS = FIXTURES / 'declared-attacker-keyword-grants-cards.json'
NEIGHBOR = FIXTURES / 'declared-attacker-equal-stats-cards.json'


class DeclaredAttackerEqualStatsTests(unittest.TestCase):
    def test_equal_declared_stats_requires_two_exact_numbers(self):
        for power, toughness, expected in (
            (2, 2, True), (0, 0, True), (-1, -1, True), (3, 2, False),
            (None, None, False), (True, 1, False), ('2', '2', False),
        ):
            with self.subTest(power=power, toughness=toughness):
                self.assertIs(expected, declared_attacker_has_equal_power_and_toughness({
                    ATTACK_FACT_PREFIX + 'power': power, ATTACK_FACT_PREFIX + 'toughness': toughness,
                }))

    def test_pinned_reach_and_whole_attack_grant_admit_only_the_closed_comparison(self):
        reg = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'attacker-stats.sqlite3'
            build_fixture_database([ORIGINALS, NEIGHBOR], path)
            with CardDatabase(path) as db:
                original = db.lookup(SOURCE, fuzzy=False)
                ir = compile_oracle_card(original, capability_registry=reg, capability_profile=PROFILE)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                reach, attack = ir.faces[0].nodes
                self.assertEqual(('continuous', 'creature.attacks'), (reach.event, attack.event))
                self.assertIn('combat.block.reach', reach.capability_dependencies)
                effect, = attack.effects
                self.assertEqual((['Trample'], '$event.permanent_object'), (effect['keywords'], effect['card']))
                self.assertIsNone(attack.target_schema)
                self.assertIn({'field': ATTACK_FACT_EQUAL_POWER_TOUGHNESS, 'op': 'eq', 'value': True},
                    attack.event_condition['all'])

                admitted, semantics = register_card(db, reg, SOURCE)
                self.assertTrue(admitted['strict_capability_ready'], admitted['blockers'])
                program, = [p for p in semantics.programs_for_oracle(original.oracle_id) if p.event == 'creature.attacks']
                clauses = program.event_condition['all']
                for changes in (
                    {'event': 'creature.dies'},
                    {'event_condition': {'all': [c for c in clauses if c['field'] != ATTACK_FACT_PREFIX + 'version']}},
                    {'event_condition': {'all': [
                        {**c, 'value': False} if c['field'] == ATTACK_FACT_EQUAL_POWER_TOUGHNESS else c for c in clauses]}},
                ):
                    with self.subTest(changes=changes):
                        raw = bind_semantic_program_runtime(replace(program, **changes), capability_registry=reg, profile=PROFILE)
                        self.assertFalse(raw['strict'], raw)
                for name in ('Dreadhorde Invasion', 'Reyav, Master Smith'):
                    retained, _ = register_card(db, reg, name)
                    self.assertTrue(retained['strict_capability_ready'], retained['blockers'])
                maccready, _ = register_card(db, reg, 'MacCready, Lamplight Mayor')
                self.assertTrue(maccready['strict_capability_ready'], maccready['blockers'])
                for comparison in ('less than its toughness', 'equal to another creature’s toughness'):
                    text = original.oracle_text.replace('equal to its toughness', comparison)
                    candidate = compile_oracle_card(replace(original, oracle_text=text),
                        capability_registry=reg, capability_profile=PROFILE)
                    self.assertNotEqual('exact', candidate.status, comparison)


class DeclaredAttackerEqualStatsNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'attacker-stats-native.sqlite3'
        build_fixture_database([
            basic, FIXTURES / 'targeted-graveyard-exile-cards.json',
            FIXTURES / 'controller-creature-keyword-absence-cards.json',
            FIXTURES / 'attacking-player-quantity-cards.json', ORIGINALS, NEIGHBOR,
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Equal declared attacker stats', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def test_paid_symmetrist_grants_trample_after_declared_stats_stop_matching(self):
        game = self.session(50830091)
        provider = self.add(game.engine, SOURCE, zone='hand')
        equal = self.add(game.engine, 'Grizzly Bears')
        unequal = self.add(game.engine, 'Giant Spider')
        answer = self.add(game.engine, 'Humble', seat='B', zone='hand')
        hidden = self.add(game.engine, 'Shock', seat='D', zone='hand', ref='equal-stats-private')
        for i in range(2):
            self.add(game.engine, 'Plains', seat='B', ref=f'equal-stats-answer-mana-{i}')
        for card in (equal, unequal):
            card.acquired_control_turn_count = -1
        for card in (provider, equal, unequal, answer):
            self.assert_closed(game, card)

        self.prepare(game, mana={'C': 2, 'G': 2})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.assertIsNone(self.resolve(game))
        self.assertIn('reach', game.engine._combat_keywords(game.state.cards[provider.object_id]))
        self.attack(game, [(equal, 'B'), (unequal, 'C')])
        self.assertEqual(1, self.trigger_count(game, provider))
        trigger, = game.state.stack
        self.assertEqual((2, 2), (trigger.context[ATTACK_FACT_PREFIX + 'power'], trigger.context[ATTACK_FACT_PREFIX + 'toughness']))
        self.assertNotIn(ATTACK_FACT_EQUAL_POWER_TOUGHNESS, trigger.context)

        self.cast(game, 'B', answer, [equal.ref])
        self.resolve_top(game)
        self.assertEqual((0, 1), tuple(game.engine._numeric_stat(equal.object_id, field) for field in ('power', 'toughness')))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn('trample', game.engine._combat_keywords(game.state.cards[equal.object_id]))
        self.assertNotIn('trample', game.engine._combat_keywords(game.state.cards[unequal.object_id]))
        for seat in 'ABC':
            projection = json.dumps(game.packet('pilot:' + seat, full=True))
            self.assertNotIn(hidden.ref, projection)
        self.replay(game, load=True)

    def test_ignoring_unequal_declaration_stats_breaks_the_paid_witness(self):
        from unittest.mock import patch

        with patch('quorune.qualified_attack_model.declared_attacker_has_equal_power_and_toughness', return_value=True):
            with self.assertRaisesRegex(AssertionError, '1 != 2'):
                self.test_paid_symmetrist_grants_trample_after_declared_stats_stop_matching()

    def test_verified_attack_condition_recipient_and_reach_withdrawals_block_complete_symmetrist(self):
        from copy import deepcopy

        from quorune.qualified_attack_model import ATTACK_CHARACTERISTIC_RESULT_CAPABILITY, QUALIFIED_ATTACK_CAPABILITY
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        discovered = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=discovered)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        baseline = verified(deepcopy(source))
        names = (SOURCE, 'Grizzly Bears')
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
        for cap in (QUALIFIED_ATTACK_CAPABILITY, ATTACK_CHARACTERISTIC_RESULT_CAPABILITY, 'combat.block.reach'):
            with self.subTest(withdrawn=cap):
                rows = deepcopy(source)
                next(row for row in rows['capabilities'] if row['id'] == cap).update(
                    status='blocked', blockers=['Exact equal declaration stats dependency withdrawn'])
                registry = verified(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn('status:' + cap + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in value for value in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name != SOURCE, ready['strict_capability_ready'], (name, ready['blockers']))
