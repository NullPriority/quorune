"""A separate reflexive stat result reads its public count at resolution."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import _reviewed_effect_template, compile_oracle_card
from quorune.query_effect_amount_model import PUBLIC_QUERY_AMOUNT_CAPABILITY, PublicQueryAmountSpec
from quorune.record import authoritative_state_hash
from quorune.reflexive_trigger_model import REFLEXIVE_PAYMENT_CAPABILITY, TOKEN_CREATION_REFLEXIVE_FIELD
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.token_creation_reflexive import token_creation_reflexive_parts
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FARMER = 'Eastfarthing Farmer'
PROFILE = 'commander_review'
FIXTURES = ROOT / 'tests' / 'fixtures'
ORIGINALS = FIXTURES / 'token-creation-reflexive-results-cards.json'


class ReflexiveLiveCountStatTests(unittest.TestCase):
    def test_complete_farmer_preserves_the_food_and_one_paired_live_count(self):
        reg = load_default_capability_registry()
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'stat-reflexive.sqlite3'
            build_fixture_database([ORIGINALS], path)
            with CardDatabase(path) as db:
                card = db.lookup(FARMER, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=reg, capability_profile=PROFILE)
                self.assertEqual('exact', ir.status, ir.material_residuals)
                entry, = ir.faces[0].nodes
                self.assertEqual('permanent.enter.self', entry.event)
                self.assertIsNone(entry.target_schema)
                self.assertTrue({PUBLIC_QUERY_AMOUNT_CAPABILITY, REFLEXIVE_PAYMENT_CAPABILITY,
                    'token.creation.fixed_definition', 'life.change.effect'} <= set(entry.capability_dependencies))
                creation, child = token_creation_reflexive_parts(entry.effects[0])
                self.assertEqual('Food Token', creation['name'])
                modifier, = child.to_dict()['effects']
                self.assertEqual('modify_stats_until_end_of_turn', modifier['op'])
                self.assertEqual('$target.0', modifier['card'])
                self.assertEqual(modifier['power'], modifier['toughness'])
                amount = PublicQueryAmountSpec.from_dict(modifier['power'])
                self.assertEqual(('food',), amount.quantity.query.subtypes_all)
                self.assertEqual(('battlefield',), amount.quantity.query.zones)
                self.assertEqual('you', child.target_schema['controller_relation'])
                ready, semantics = register_card(db, reg, FARMER)
                self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
                program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
                    if p.event == 'permanent.enter.self']
                changed = {**child.to_dict(), 'effects': [{
                    **modifier, 'toughness': {**modifier['toughness'], 'coefficient': 2},
                }]}
                raw = bind_semantic_program_runtime(replace(program, effects=[
                    {**program.effects[0], TOKEN_CREATION_REFLEXIVE_FIELD: changed},
                ]), capability_registry=reg, profile=PROFILE)
                self.assertFalse(raw['strict'], raw)
                for left, right in (('+1/+1', '+1/+2'), ('each Food you control', 'each Food your opponents control')):
                    altered = replace(card, oracle_text=card.oracle_text.replace(left, right, 1))
                    self.assertNotEqual('exact', compile_oracle_card(altered, capability_registry=reg,
                        capability_profile=PROFILE).status)


class ReflexiveLiveCountStatNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / 'bound-effect-program-cards.json').read_text(encoding='utf-8'))
        base['cards'] = [card for card in base['cards'] if card['name'] == 'Generic Bound Plains']
        base['rulings'] = []
        basic = folder / 'basic.json'
        basic.write_text(json.dumps(base), encoding='utf-8')
        path = folder / 'stat-reflexive-native.sqlite3'
        build_fixture_database([
            basic, ORIGINALS, FIXTURES / 'targeted-graveyard-exile-cards.json',
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Reflexive live public stat count', [
            DeckEntry('Sliver Hivelord', 1, 'commander'), DeckEntry('Generic Bound Plains', 30),
        ], ['Sliver Hivelord'])

    def paid_parent(self, seed):
        game = self.session(seed)
        provider = self.add(game.engine, FARMER, zone='hand')
        target = self.add(game.engine, 'Grizzly Bears', ref='food-stat-target')
        foreign = self.add(game.engine, 'Grizzly Bears', seat='B', ref='food-stat-foreign')
        private = self.add(game.engine, 'Shock', seat='D', zone='hand', ref='food-stat-private')
        _, recipes, _, _ = _reviewed_effect_template('Create a Food token.', card_name='Fixture')
        recipe, = recipes
        game.engine.create_token('B', name=recipe['name'], characteristics=recipe['characteristics'],
            quantity=recipe['quantity'])
        self.assert_closed(game, provider)
        self.prepare(game, mana={'C': 4, 'W': 1})
        self.checkpoint(game)
        self.cast(game, 'A', provider)
        self.resolve_top(game)
        parent, = game.state.stack
        self.assertEqual([], parent.targets)
        self.resolve_top(game)
        child, = game.state.stack
        self.assertEqual(parent.ref, child.context['reflexive_parent'])
        food, = [c for c in game.state.cards.values()
            if c.zone == 'battlefield' and c.controller == 'A' and c.is_token and c.printed_name == 'Food Token']
        self.assertEqual('semantic.target', game.state.pending_decision.kind)
        legal = game.packet('pilot:A', full=True)['decision']['ctx']['target_schema']['legal_refs']
        self.assertIn(target.ref, legal)
        self.assertNotIn(foreign.ref, legal)
        return game, food, target, foreign, private

    def select_target(self, game, target):
        result = game.act('pilot:A', {'action_id': 'choose', 'targets': [target.ref]})
        self.assertTrue(result.ok, result.summary)

    def finish_private(self, game, private):
        for seat in 'ABC':
            self.assertNotIn(private.ref, json.dumps(game.packet('pilot:' + seat, full=True)))
        self.replay(game, load=True)

    def test_paid_reflexive_count_excludes_the_opponents_food(self):
        game, _food, target, foreign, private = self.paid_parent(60820710)
        command = {'action_id': 'choose', 'targets': [foreign.ref]}
        state = authoritative_state_hash(game.state)
        self.assertFalse(game.act('pilot:A', command).ok)
        self.assertEqual(state, authoritative_state_hash(game.state))
        self.select_target(game, target)
        self.resolve_top(game)
        self.assertEqual((3, 3), tuple(game.engine._numeric_stat(target.object_id, field)
            for field in ('power', 'toughness')))
        self.finish_private(game, private)

    def test_paid_food_sacrifice_keeps_the_child_and_resolves_with_zero(self):
        game, food, target, _foreign, private = self.paid_parent(60820720)
        self.select_target(game, target)
        self.priority(game, 'A')
        actions = game.packet('pilot:A', full=True)['decision']['ctx']['legal']['actions']
        offered = next(action for action in actions if action['id'].startswith('activate:' + food.ref + ':'))
        activated = game.act('pilot:A', {'action_id': offered['id'], 'pay': 'auto'})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        self.assertEqual(43, game.state.players['A'].life)
        self.assertEqual('outside', game.state.cards[food.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((2, 2), tuple(game.engine._numeric_stat(target.object_id, field)
            for field in ('power', 'toughness')))
        self.finish_private(game, private)

    def test_using_the_creation_count_instead_of_the_live_count_breaks_the_paid_witness(self):
        with patch('quorune.semantic_runtime.values.resolve_public_query_amount', return_value=1):
            with self.assertRaisesRegex(AssertionError, 'Tuples differ'):
                self.test_paid_food_sacrifice_keeps_the_child_and_resolves_with_zero()

    def test_verified_live_quantity_and_reflexive_withdrawals_block_the_right_originals(self):
        from copy import deepcopy

        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / 'quorune/rules/capability-registry.json').read_text(encoding='utf-8'))
        declarations = read_evidence_document(ROOT / 'platform/capability-evidence-declarations.json')
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (FARMER, 'Faebloom Trick', 'Grizzly Bears')
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready['strict_capability_ready'], ready['blockers'])
        for cap, blocked in (
            (PUBLIC_QUERY_AMOUNT_CAPABILITY, {FARMER}), (REFLEXIVE_PAYMENT_CAPABILITY, {FARMER, 'Faebloom Trick'}),
        ):
            with self.subTest(withdrawn=cap):
                rows = deepcopy(source)
                next(r for r in rows['capabilities'] if r['id'] == cap).update(
                    status='blocked', blockers=['Exact live stat reflexive dependency withdrawn'])
                registry = verified(rows)
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn('status:' + cap + ':blocked', closure.blockers)
                self.assertFalse(any('unverified' in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready['strict_capability_ready'], (name, ready['blockers']))
