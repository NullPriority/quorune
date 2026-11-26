import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_cascade_rules as cascade_cases


class ConditionalStackCascadeNativeTests(InheritedNativeFixture, unittest.TestCase):
    cascade_occurrences = cascade_cases.BattlefieldCascadeGrantTests.cascade_occurrences
    conditional_source = "Route Surveyor"
    plain_land = "Generic Bound Plains"
    cheaper_spell = "Generic Bound Draw Loss"
    private_tail = "Generic Bound Growth"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "conditional-cascade.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database(
            [fixtures / name for name in (
                "bound-effect-program-cards.json",
                "event-spell-copy-cards.json",
                "fixed-public-zone-move-cards.json",
                "recipient-control-cards.json",
                "conditional-stack-cascade-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional Cascade", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def seed_graveyard(self, game, seat, names, label):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, zone="graveyard", ref=f"{label}-{seat}-{index}")

    def projected_cascade(self, game, card_object_id):
        card = game.state.cards[card_object_id]
        return "cascade" in {name.casefold() for name in game.engine._effective_card_data(card)["keywords"]}

    def exile_graveyard(self, game, source):
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        accepted = game.act("pilot:B", {"action_id": action["id"], "targets": ["A"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)

    def test_current_stack_keyword_and_queued_trigger_survive_their_distinct_boundaries(self):
        game = self.session(70285101)
        engine = game.engine
        spell = self.add(engine, self.conditional_source, zone="hand", ref="conditional-cascade-original")
        copier = self.add(engine, "Double Major", zone="hand")
        crypt = self.add(engine, "Tormod's Crypt", seat="B")
        self.seed_graveyard(game, "A", [self.plain_land, "Grizzly Bears", "Murder", "Donate"], "qualified")
        target = self.add(engine, self.cheaper_spell, zone="library", ref="conditional-cascade-top")
        hidden = self.add(engine, self.private_tail, zone="library", ref="conditional-cascade-private-tail")
        for card in (hidden, target):
            engine.move_card(card.object_id, "library", position="top", log=False)
            card.known_to = []
            card.revealed_to = []
        for card in (spell, copier, crypt):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "R": 1, "G": 1, "U": 1})
        self.checkpoint(game)
        self.assertFalse(self.projected_cascade(game, spell.object_id))
        self.cast(game, "A", spell)
        occurrences = self.cascade_occurrences(game, spell)
        self.assertEqual(1, len(occurrences), "one qualifying conditional Cascade")
        trigger_ref = occurrences[0].ref
        self.assertTrue(self.projected_cascade(game, spell.object_id))
        source_spell = next(item for item in game.state.stack
                            if item.kind == "spell" and item.card_object_id == spell.object_id)
        self.cast(game, "A", copier, [source_spell.ref])
        self.resolve_top(game)
        copied, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertTrue(self.projected_cascade(game, copied.card_object_id))
        self.assertEqual([trigger_ref], [item.ref for item in game.state.stack if item.semantic_key == "builtin:cascade"])
        self.exile_graveyard(game, crypt)
        self.assertFalse(self.projected_cascade(game, spell.object_id))
        self.assertFalse(self.projected_cascade(game, copied.card_object_id))
        self.assertEqual([trigger_ref], [item.ref for item in game.state.stack if item.semantic_key == "builtin:cascade"])
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "selection.exile_cast")
        self.assertEqual("exile", game.state.cards[target.object_id].zone)
        for seat in "ABCD":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        accepted = game.act("pilot:A", {"action_id": "decline"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.replay(game, load=True)

    def test_printed_cascade_is_retained_once_below_and_alongside_the_condition(self):
        for qualifies in (False, True):
            with self.subTest(qualifies=qualifies):
                game = self.session(70285102 + int(qualifies))
                source = self.add(game.engine, "Route Veteran", zone="hand")
                self.assert_closed(game, source)
                self.seed_graveyard(game, "A", ["Grizzly Bears", "Murder", "Donate"] if qualifies
                                    else [self.plain_land] * 4, "actual-caster")
                self.seed_graveyard(game, "B", [self.plain_land, "Grizzly Bears", "Murder", "Donate"], "opponent")
                self.prepare(game, mana={"C": 2, "R": 1})
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.assertEqual(2 if qualifies else 1, len(self.cascade_occurrences(game, source)))
                self.replay(game, load=True)

    def test_missing_live_stack_fragment_fails_the_native_qualification(self):
        witness = type(self)("test_current_stack_keyword_and_queued_trigger_survive_their_distinct_boundaries")
        result = unittest.TestResult()
        with patch("quorune.semantic_runtime.battlefield_cascade_grants._conditional_source_spell_effects", return_value=()):
            witness.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("one qualifying conditional Cascade", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
