"""Current half-starting-life facts drive exact stat and casting consumers."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.characteristic_fragments import (
    CharacteristicQuantityScope, CharacteristicQuantitySpec, characteristic_quantity_capabilities,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.live_modifier_quantities import current_half_starting_life_count
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ANYA = "Anya, Merciless Angel"
SPELL = "Game Over"
CAPABILITY = "quantity_expression.player_half_starting_life_threshold"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"
OPPONENTS_BELOW = CharacteristicQuantityScope.OPPONENTS_BELOW_HALF_STARTING_LIFE
PLAYERS_AT_OR_BELOW = CharacteristicQuantityScope.PLAYERS_AT_OR_BELOW_HALF_STARTING_LIFE


class StartingLifeHalfThresholdTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "starting-life-half.sqlite3"
        build_fixture_database([basic, FIXTURES / "starting-life-half-threshold-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Half starting life thresholds", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        record = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return record, semantics.programs_for_oracle(record.oracle_id)

    def test_both_originals_keep_all_material_programs_and_distinct_live_thresholds(self):
        anya, angel = self.programs(ANYA)
        spell, game_over = self.programs(SPELL)
        angel_live = [program for program in angel if CAPABILITY in program.capability_dependencies]
        discount, = [program for program in game_over if CAPABILITY in program.capability_dependencies]
        self.assertEqual(2, len(angel_live))
        self.assertTrue(any("combat.block.flying" in program.capability_dependencies for program in angel))
        self.assertTrue(any("permanent.indestructible.ordinary" in program.capability_dependencies for program in angel))
        self.assertEqual("cast.cost.modify", discount.event)
        self.assertTrue(any("permanent.destroy.fixed_set" in program.capability_dependencies for program in game_over))
        for record, phrase, replaced in (
            (anya, "opponent whose", "player whose"),
            (spell, "less than or equal to half", "strictly below one third of"),
        ):
            changed = replace(record, oracle_text=record.oracle_text.replace(phrase, replaced))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            expected_remaining = 1 if record is anya else 0
            self.assertEqual(expected_remaining, sum(CAPABILITY in node.capability_dependencies
                             for face in ir.faces for node in face.nodes))

    def test_raw_half_life_rejects_wrong_version_and_missing_authority(self):
        self.assertEqual((), characteristic_quantity_capabilities({"scope": {"kind": "controller_zone"}}))
        typed = CharacteristicQuantitySpec(OPPONENTS_BELOW, schema_version=4).to_dict()
        self.assertEqual((CAPABILITY,), characteristic_quantity_capabilities({"scope": {"quantity": typed}}))
        for scope in (OPPONENTS_BELOW, PLAYERS_AT_OR_BELOW):
            with self.assertRaises(ValueError):
                CharacteristicQuantitySpec(scope)
            with self.assertRaises(ValueError):
                CharacteristicQuantitySpec(scope, schema_version=4, multiplier=2)
        for name in (ANYA, SPELL):
            _, programs = self.programs(name)
            for item in programs:
                if CAPABILITY not in item.capability_dependencies:
                    continue
                dependencies = [cap for cap in item.capability_dependencies if cap != CAPABILITY]
                changed = replace(item, capability_dependencies=dependencies,
                                  capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, bound["blockers"])

    def test_half_life_count_has_exact_integer_boundaries_and_active_roster(self):
        player = lambda life, in_game=True: SimpleNamespace(life=life, in_game=in_game)
        state = SimpleNamespace(config=SimpleNamespace(starting_life=41),
                                players={"A": player(19), "B": player(20), "C": player(21), "D": player(1, False)})
        self.assertEqual(1, current_half_starting_life_count(state, "A", OPPONENTS_BELOW))
        self.assertEqual(2, current_half_starting_life_count(state, "A", PLAYERS_AT_OR_BELOW))
        state.config.starting_life = 40
        self.assertEqual(0, current_half_starting_life_count(state, "A", OPPONENTS_BELOW))
        self.assertEqual(2, current_half_starting_life_count(state, "A", PLAYERS_AT_OR_BELOW))
        with self.assertRaises(ValueError):
            current_half_starting_life_count(state, "A", OPPONENTS_BELOW.value)
        state.players["B"].life = True
        with self.assertRaises(ValueError):
            current_half_starting_life_count(state, "A", OPPONENTS_BELOW)

    def stats(self, game, card):
        return tuple(game.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_anya_uses_unrounded_odd_half_and_loses_marked_damage_protection(self):
        game = self.session(103040041)
        engine = game.engine
        game.state.config = replace(game.state.config, starting_life=41)
        for seat, life in {"A": 20, "B": 21, "C": 20, "D": 41}.items():
            game.state.players[seat].life = life
        anya = self.add(engine, ANYA, zone="hand")
        bolt = self.add(engine, "Lightning Bolt", zone="hand")
        disaster = self.add(engine, "Blasphemous Act", zone="hand")
        b_gain = self.add(engine, "Revitalize", seat="B", zone="hand", ref="anya-b-recovery")
        c_gain = self.add(engine, "Revitalize", seat="C", zone="hand", ref="anya-c-recovery")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="half-life-private")
        for card in (anya, bolt, disaster, b_gain, c_gain):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 10, "R": 3, "W": 1})
        for seat in ("B", "C"):
            game.state.players[seat].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)

        self.cast_for(game, "A", anya, reserve={"C": 7, "R": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((7, 7), self.stats(game, anya))
        self.assertIn("indestructible", game.engine._combat_keywords(game.state.cards[anya.object_id]))
        self.cast_for(game, "A", bolt, ["B"], reserve={"C": 7, "R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual((10, 10), self.stats(game, anya))
        self.cast_for(game, "A", disaster)
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[anya.object_id]
        self.assertEqual(("battlefield", 13), (current.zone, current.marked_damage))
        game = self.replay(game, load=True)

        self.cast_for(game, "B", b_gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(21, game.state.players["B"].life)
        self.assertEqual((7, 7), self.stats(game, anya))
        self.assertEqual(13, game.state.cards[anya.object_id].marked_damage)
        self.cast_for(game, "C", c_gain)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(23, game.state.players["C"].life)
        self.assertEqual("graveyard", game.state.cards[anya.object_id].zone)
        self.assertEqual(20, game.state.players["A"].life)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def normal_price(self, game, spell):
        self.priority(game, "A")
        offered = self.action(game, "A", spell)
        normal, = [row for row in offered["cost_options"] if row["id"] == "normal"]
        return offered["id"], normal["requirements"]["GENERIC"]

    def test_actual_game_over_requotes_at_inclusive_boundary_and_pays_one_discount(self):
        game = self.session(601020641)
        engine = game.engine
        for seat in ("A", "C"):
            game.state.players[seat].life = 23
        spell = self.add(engine, SPELL, zone="hand")
        first = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="game-over-drain-c-first")
        second = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="game-over-drain-a")
        third = self.add(engine, "Lightning Bolt", seat="B", zone="hand", ref="game-over-drain-c-next")
        recovery = self.add(engine, "Revitalize", seat="C", zone="hand")
        own = self.add(engine, "Grizzly Bears")
        other = self.add(engine, "Wind Drake", seat="B")
        private = self.add(engine, "Counterspell", seat="D", zone="hand", ref="game-over-private")
        for card in (spell, first, recovery):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "B": 2})
        game.state.players["B"].mana_pool["R"] = 3
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)

        self.cast_for(game, "B", first, ["C"], reserve={"R": 2})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(20, game.state.players["C"].life)
        action, price = self.normal_price(game, spell)
        self.assertEqual(1, price)
        self.cast_for(game, "C", recovery)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(23, game.state.players["C"].life)
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": action, "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast_for(game, "B", second, ["A"], reserve={"R": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(20, game.state.players["A"].life)
        self.assertEqual(1, self.normal_price(game, spell)[1])
        self.cast_for(game, "B", third, ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(20, game.state.players["C"].life)
        self.assertEqual(1, self.normal_price(game, spell)[1])
        game = self.replay(game, load=True)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertEqual(["graveyard", "graveyard"], [game.state.cards[card.object_id].zone for card in (own, other)])
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_odd_anya_witness_kills_rounding_down_before_strict_comparison(self):
        def rounded(state, controller, scope):
            if scope is OPPONENTS_BELOW:
                config = replace(state.config, starting_life=2 * (state.config.starting_life // 2))
                state = SimpleNamespace(config=config, players=state.players)
            return current_half_starting_life_count(state, controller, scope)

        with patch("quorune.rules.live_modifier_quantities.current_half_starting_life_count", side_effect=rounded):
            with self.assertRaisesRegex(AssertionError, "Tuples differ"):
                self.test_actual_anya_uses_unrounded_odd_half_and_loses_marked_damage_protection()
