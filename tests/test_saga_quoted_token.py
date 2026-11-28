"""The printed Saga's final chapter carries a copiable attack ability."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.saga_progression import dispatch_saga_chapters
from quorune.trigger_processing import enqueue_trigger_batch
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class SagaQuotedTokenTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "quoted-saga-token.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / name for name in (
                "bound-effect-program-cards.json", "coming-of-galactus-pinned-cards.json",
            )], path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga quoted token",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    def test_original_galactus_last_chapter_creates_its_copiable_land_attack_and_replays(self):
        game = self.session(7144041)
        source = self.add(game.engine, "The Coming of Galactus", ref="native-galactus-saga")
        own = self.add(game.engine, "Generic Bound Plains", ref="native-galactus-own-land")
        opponent = self.add(game.engine, "Generic Bound Plains", seat="B", ref="native-galactus-opposing-land")
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={})
        source.counters["lore"] = 4
        chapters = []
        dispatch_saga_chapters(game.engine, source, previous_lore=3, trigger_batch=chapters)
        self.assertEqual(1, len(chapters))
        enqueue_trigger_batch(game.engine, chapters)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)

        self.assertIsNone(self.resolve(game))
        token, = [card for card in game.state.cards.values() if card.is_token and card.printed_name == "Galactus"]
        effective = game.engine._effective_card_data(token)
        self.assertEqual((16, 16), (int(effective["power"]), int(effective["toughness"])))
        self.assertTrue({"flying", "trample"} <= {word.casefold() for word in effective["keywords"]})
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

        token.acquired_control_turn_count = -1
        self.checkpoint(game)
        self.attack(game, ((token, "B"),))
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        choices, = [row for row in game.packet("pilot:A", full=True)["decision"]["legal_actions"]
                    if row["id"] == "choose"]
        self.assertTrue({own.ref, opponent.ref} <= set(choices["target_schema"]["legal_refs"]))
        self.assertNotIn(token.ref, choices["target_schema"]["legal_refs"])
        self.target(game, "A", (opponent.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[opponent.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.replay(game, load=True)

    def test_removed_saga_token_carrier_leaves_the_original_last_chapter_unresolved(self):
        record = self.db.lookup("The Coming of Galactus", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        with patch("quorune.compiler.saga_chapter_grants._saga_token_chapter_plan", return_value=None):
            damaged = compile_oracle_card(record, **options)
        self.assertEqual(1, len(damaged.material_residuals))
        self.assertIn("Whenever Galactus attacks", damaged.material_residuals[0].text)
