"""The original spell uses one announced X and one same-target damage proposal."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.query_effect_amount_model import CastXAmountSpec, PublicQueryAmountError
from quorune.record import authoritative_state_hash
from quorune.resolution_conditions import validate_resolution_condition_instruction
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.query_effect_amounts import resolve_cast_x_amount
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Crater's Claws"


class PublicConditionAnnouncedXDamageBonusTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "public-condition-announced-x-damage-bonus.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "public-condition-announced-x-damage-bonus-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced X and one damage result", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_full_original_binds_one_unchanged_target_and_two_closed_announced_amounts(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        spell, = semantics.programs_for_oracle(self.db.lookup(ORIGINAL, fuzzy=False).oracle_id)
        self.assertIn("quantity_expression.declared_effect_amount", spell.capability_dependencies)
        self.assertIn("resolution.effect.public_same_target_self_replacement", spell.capability_dependencies)
        wrapper, = spell.effects
        self.assertEqual(8, wrapper["schema_version"])
        self.assertEqual(spell.target_schema, wrapper["target_binding"])
        condition, (replacement,) = validate_resolution_condition_instruction(wrapper)
        self.assertEqual(4, condition.predicate.numeric_characteristic.value)
        normal, = wrapper["otherwise_effects"]
        self.assertEqual(CastXAmountSpec().to_dict(), normal["amount"])
        self.assertEqual(CastXAmountSpec(schema_version=3, bonus=2).to_dict(), replacement["amount"])
        for invalid in (
            {**wrapper, "effects": [{**replacement, "target": "$target.1"}]},
            {**wrapper, "effects": [replacement, replacement]},
            {**wrapper, "effects": [{**replacement, "amount": {**replacement["amount"], "bonus": 1}}]},
            {**wrapper, "effects": [normal], "otherwise_effects": [replacement]},
        ):
            with self.assertRaises(ValueError):
                validate_resolution_condition_instruction(invalid)

    def test_cast_x_bonus_is_versioned_and_the_earlier_amount_encodings_round_trip(self):
        for version, coefficient in ((1, 1), (2, 3)):
            old = {"kind": "cast_x_effect_amount", "schema_version": version, "coefficient": coefficient}
            self.assertEqual(old, CastXAmountSpec.from_dict(old).to_dict())
            self.assertEqual(3 * coefficient, resolve_cast_x_amount(old, SimpleNamespace(x_value=3)))
        bonus = CastXAmountSpec(schema_version=3, bonus=2).to_dict()
        self.assertEqual(2, resolve_cast_x_amount(bonus, SimpleNamespace(x_value=0)))
        with self.assertRaises(PublicQueryAmountError):
            resolve_cast_x_amount(bonus, SimpleNamespace(x_value=None))
        for invalid in (
            {key: value for key, value in bonus.items() if key != "bonus"},
            {**bonus, "bonus": True}, {**bonus, "bonus": -2}, {**bonus, "schema_version": 1},
            {**bonus, "coefficient": 2},
        ):
            with self.assertRaises(PublicQueryAmountError):
                CastXAmountSpec.from_dict(invalid)

    def cast_x(self, game, source, x, target, *, reserve=None):
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        response = {
            "action_id": offer["id"], "x": x, "targets": [target], "pay": "auto", "reserve": reserve or {},
        }
        cast = game.act("pilot:A", response)
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)

    def test_actual_zero_is_payable_and_positive_only_when_the_printed_condition_matches(self):
        for qualified in (False, True):
            with self.subTest(qualified=qualified):
                game = self.session(10730001 + qualified)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                if qualified:
                    self.add(game.engine, "Rumbling Baloth")
                self.assert_closed(game, source)
                self.prepare(game, mana={"R": 1})
                self.checkpoint(game)
                self.priority(game, "A")
                offer = self.action(game, "A", source)
                before = authoritative_state_hash(game.state)
                for invalid_x in (-1, 1):
                    rejected = game.act("pilot:A", {"action_id": offer["id"], "x": invalid_x, "targets": ["B"], "pay": "auto"})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                self.cast_x(game, source, 0, "B")
                self.assertEqual(0, game.state.players["A"].mana_pool["R"])
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual(38 if qualified else 40, game.state.players["B"].life)
                self.replay(game, load=True)

    def test_actual_response_growth_selects_one_bonused_result_before_an_external_addition(self):
        game = self.session(61415020)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        qualifier = self.add(game.engine, "Centaur Courser")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        modifier = self.add(game.engine, "Torbran, Thane of Red Fell")
        for card in (source, growth, modifier):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "C": 2, "G": 1})
        self.checkpoint(game)
        self.cast_x(game, source, 2, "B", reserve={"G": 1})
        self.cast_for(game, "A", growth, [qualifier.ref])
        self.resolve_top(game)
        self.assertEqual(6, game.engine._numeric_stat(qualifier.object_id, "power"))
        game = self.replay(game, load=True)
        self.assertEqual(2, game.state.stack[-1].x_value)
        self.resolve_top(game)
        self.assertEqual(34, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_removing_the_qualifier_preserves_the_original_paid_x(self):
        game = self.session(61415021)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        qualifier = self.add(game.engine, "Rumbling Baloth")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "C": 3, "U": 1})
        self.checkpoint(game)
        self.cast_x(game, source, 3, "B", reserve={"U": 1})
        self.assertEqual((0, 0, 1), tuple(game.state.players["A"].mana_pool[color] for color in ("R", "C", "U")))
        self.cast_for(game, "A", bounce, [qualifier.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[qualifier.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(37, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_invalidated_target_cannot_receive_the_bonus_or_fall_back_to_a_player(self):
        game = self.session(61415022)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        self.add(game.engine, "Rumbling Baloth")
        target = self.add(game.engine, "Centaur Courser", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        for card in (source, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"R": 1, "U": 1})
        self.checkpoint(game)
        self.cast_x(game, source, 0, target.ref)
        self.cast_for(game, "A", bounce, [target.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)
