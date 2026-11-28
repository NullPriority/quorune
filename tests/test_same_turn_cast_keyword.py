"""A chapter creates an end-of-turn repeated cast trigger after its Saga leaves."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_spell_copy_model import SAME_TURN_CAST_KEYWORD_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.saga_progression import dispatch_saga_chapters
from quorune.trigger_processing import enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database


class SameTurnCastKeywordTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "same-turn-cast.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / name for name in (
                "bound-effect-program-cards.json", "scryfall-exact-lists.json",
                "attached-destruction-set-cards.json", "announced-x-public-damage-set-cards.json",
                "rediscover-the-way-pinned-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Recurring cast keyword",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    def test_original_rediscover_repeats_for_same_turn_noncreature_casts_after_saga_departure(self):
        game = self.session(603711)
        source = self.add(game.engine, "Rediscover the Way", ref="native-rediscover")
        first = self.add(game.engine, "Grizzly Bears", ref="native-cast-keyword-first")
        second = self.add(game.engine, "Grizzly Bears", ref="native-cast-keyword-second")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-cast-keyword")
        bolts = tuple(self.add(game.engine, "Lightning Bolt", zone="hand", ref=f"native-cast-bolt-{number}")
                      for number in (1, 2))
        opposing = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand", ref="foreign-cast-bolt")
        creature = self.add(game.engine, "Grizzly Bears", zone="hand", ref="native-creature-cast")
        for number in range(3):
            self.add(game.engine, "Mountain", ref=f"native-cast-red-source-{number}")
        self.add(game.engine, "Mountain", seat="B", ref="foreign-cast-red-source")
        self.assert_closed(game, source)
        for bolt in (*bolts, opposing, creature):
            self.assert_closed(game, bolt)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "R": 2, "G": 1})
        game.state.players["B"].mana_pool.update({"R": 1})
        source.counters["lore"] = 3
        chapter = []
        dispatch_saga_chapters(game.engine, source, previous_lore=2, trigger_batch=chapter)
        enqueue_trigger_batch(game.engine, chapter)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        waiting, = [trigger for trigger in game.state.delayed_triggers if SAME_TURN_CAST_KEYWORD_FIELD in trigger.condition]
        self.assertFalse(waiting.once)
        self.assertEqual(game.state.turn_sequence, waiting.expires_turn_sequence)

        self.cast(game, "A", creature)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", opposing, ("D",))
        self.assertIsNone(self.resolve(game))

        for bolt, recipient in zip(bolts, (first, second), strict=True):
            self.assertEqual(waiting.created_turn_sequence, game.state.turn_sequence)
            self.cast(game, "A", bolt, ("D",))
            self.assertEqual("semantic.target", game.state.pending_decision.kind)
            offer, = [row for row in game.packet("pilot:A", full=True)["decision"]["legal_actions"]
                       if row["id"] == "choose"]
            self.assertTrue({first.ref, second.ref} <= set(offer["target_schema"]["legal_refs"]))
            self.assertNotIn(foreign.ref, offer["target_schema"]["legal_refs"])
            self.target(game, "A", (recipient.ref,))
            self.assertIsNone(self.resolve(game))
            current = game.state.cards[recipient.object_id]
            self.assertIn("double strike", {name.casefold() for name in game.engine._effective_card_data(current)["keywords"]})
        self.assertTrue(waiting.active)
        self.replay(game, load=True)

    def test_drop_recurring_cast_carrier_leaves_only_the_original_third_chapter(self):
        card = self.db.lookup("Rediscover the Way", fuzzy=False)
        arguments = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        from quorune.compiler.target_effect_corpus_assurance import TargetEffectCorpusCollector

        ir = compile_oracle_card(card, **arguments)
        self.assertEqual("exact", ir.status)
        TargetEffectCorpusCollector().observe(card, ir)
        original = __import__("quorune.rules.event_spell_copy", fromlist=["same_turn_cast_keyword_node_capabilities"])
        with patch.object(original, "same_turn_cast_keyword_node_capabilities", return_value=()):
            modified = compile_oracle_card(card, **arguments)
        self.assertEqual(1, len(modified.material_residuals))
