"""An actual own-main-phase cast adds an exact result after its ordinary effect."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_ADDITIVE_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhaseAddendumResultTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-addendum-results.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-addendum-results-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exact Addendum result", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_preserve_fixed_base_and_append_only_the_printed_bonus(self):
        for name, result in (
            ("Sphinx's Insight", "life"),
            ("Arrester's Admonition", "draw"),
            ("Arrester's Zeal", "grant_keyword_until_end_of_turn"),
        ):
            with self.subTest(name=name):
                admitted, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantics.programs_for_oracle(card.oracle_id)
                instruction, = spell.effects
                self.assertEqual(MAIN_PHASE_CAST_ADDITIVE_VERSION, instruction["schema_version"])
                self.assertEqual(instruction["otherwise_effects"], instruction["effects"][:1])
                self.assertEqual(result, instruction["effects"][1]["op"])
                for changed in (
                    {**instruction, "cast_fact": True},
                    {**instruction, "effects": list(reversed(instruction["effects"]))},
                    {**instruction, "otherwise_effects": []},
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        replace(spell, effects=[changed]), capability_registry=self.registry,
                        profile="commander_review",
                    )["strict"])
                changed_text = card.oracle_text.replace("your main phase", "your upkeep")
                self.assertNotEqual("exact", compile_oracle_card(
                    replace(card, oracle_text=changed_text), capability_registry=self.registry,
                    capability_profile="commander_review",
                ).status)

    def hand(self, game, seat):
        return set(game.state.players[seat].zones["hand"])

    def test_actual_sphinx_copy_only_draws_and_the_original_draws_before_gaining_life(self):
        game = self.session(60127001)
        original = self.add(game.engine, "Sphinx's Insight", zone="hand")
        copy = self.add(game.engine, "Twincast", seat="B", zone="hand")
        for card in (original, copy):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "U": 1})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", original)
        stack = game.state.stack[-1]
        self.cast_for(game, "B", copy, [stack.ref])
        a_before, b_before = self.hand(game, "A"), self.hand(game, "B")
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(2, len(self.hand(game, "B") - b_before))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual(a_before, self.hand(game, "A"))
        start = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, len(self.hand(game, "A") - a_before))
        self.assertEqual(42, game.state.players["A"].life)
        codes = [event.code for event in game.state.events[start:]]
        draw = [index for index, code in enumerate(codes) if code == "card.draw"]
        life = [index for index, code in enumerate(codes) if code == "effect.life"]
        self.assertEqual((2, 1), (len(draw), len(life)))
        self.assertLess(max(draw), life[0])
        self.replay(game, load=True)

    def test_actual_admonition_draws_after_the_return_but_never_if_the_only_target_leaves(self):
        game = self.session(60127002)
        original = self.add(game.engine, "Arrester's Admonition", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand", seat="B")
        for card in (original, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", original, [creature.ref])
        hand_before = self.hand(game, "A")
        altered = self.replay(game, load=True)
        self.cast_for(altered, "B", bounce, [creature.ref])
        self.resolve_top(altered)
        self.assertIsNone(self.resolve(altered))
        self.assertEqual(hand_before, self.hand(altered, "A"))
        self.assertIn(creature.object_id, self.hand(altered, "B"))
        self.replay(altered, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(self.hand(game, "A") - hand_before))
        self.assertIn(creature.object_id, self.hand(game, "B"))
        self.replay(game, load=True)

    def test_actual_zeal_always_pumps_and_only_own_main_phase_adds_flying(self):
        for actor in ("A", "B"):
            with self.subTest(actor=actor):
                game = self.session(60127003 + (actor == "A"))
                spell = self.add(game.engine, "Arrester's Zeal", zone="hand", seat=actor)
                creature = self.add(game.engine, "Grizzly Bears")
                self.assert_closed(game, spell)
                self.prepare(game, actor=actor, active="A", mana={"W": 1})
                self.checkpoint(game)
                self.cast_for(game, actor, spell, [creature.ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual((4, 4), tuple(game.engine._numeric_stat(creature.object_id, key)
                                              for key in ("power", "toughness")))
                self.assertEqual(actor == "A", "flying" in
                                 game.engine._combat_keywords(game.state.cards[creature.object_id]))
                self.replay(game, load=True)
