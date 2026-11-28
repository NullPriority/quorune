"""Native per-player non-Saga targeting on both original linked chapters."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AnnouncedSagaExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-saga-exile.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / name for name in (
                "bound-effect-program-cards.json", "ordinary-saga-chapter-programs.json",
                "conditional-exile-cards.json", "attached-destruction-set-cards.json",
                "battle-at-the-helvault-pinned-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced Saga exile", (
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ), ("Generic Bound Commander",))

    def test_original_helvault_retains_each_players_slot_and_exact_source_departure(self):
        for early_departure in (False, True):
            with self.subTest(early_departure=early_departure):
                game = self.session(6100342 + early_departure)
                source = self.add(game.engine, "Battle at the Helvault", zone="hand")
                own = self.add(game.engine, "Grizzly Bears", ref="eligible-own")
                other_own = self.add(game.engine, "Grizzly Bears", ref="second-own-slot")
                opponent = self.add(game.engine, "Grizzly Bears", seat="B", ref="eligible-opponent")
                excluded_saga = self.add(game.engine, "Ordinary Saga Chapter Fixture", ref="excluded-saga")
                land = self.add(game.engine, "Generic Bound Plains", seat="C", ref="excluded-land")
                removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
                self.assert_closed(game, source)
                self.assert_closed(game, removal)
                self.prepare(game, mana={"C": 4, "W": 2})
                game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.resolve_top(game)
                self.assertEqual("semantic.target", game.state.pending_decision.kind)
                decision = game.packet("pilot:A", full=True)["decision"]
                choose, = [row for row in decision["legal_actions"] if row["id"] == "choose"]
                self.assertTrue({own.ref, opponent.ref} <= set(choose["target_schema"]["legal_refs"]))
                self.assertTrue({excluded_saga.ref, land.ref, source.ref}.isdisjoint(choose["target_schema"]["legal_refs"]))
                old = authoritative_state_hash(game.state)
                forged = game.act("pilot:A", {"action_id": "choose", "targets": [own.ref, other_own.ref]})
                self.assertFalse(forged.ok, forged.summary)
                self.assertEqual(old, authoritative_state_hash(game.state))
                self.target(game, "A", (own.ref, opponent.ref))
                if not early_departure:
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(["exile", "exile"], [game.state.cards[card.object_id].zone for card in (own, opponent)])
                self.cast(game, "B", removal, [source.ref])
                self.assertIsNone(self.resolve(game))
                settled = [game.state.cards[card.object_id] for card in (own, opponent)]
                self.assertEqual([("battlefield", "A"), ("battlefield", "B")],
                                 [(card.zone, card.controller) for card in settled])
                self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_helvault_requires_the_exact_printed_non_saga_grammar(self):
        original = self.db.lookup("Battle at the Helvault", fuzzy=False)
        kwargs = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(original, **kwargs)
        self.assertEqual("exact", ir.status)
        chapters = [node.event for face in ir.faces for node in face.nodes if node.event.startswith("saga.chapter.")]
        self.assertEqual(["saga.chapter.1", "saga.chapter.2", "saga.chapter.3"], chapters)
        with patch("quorune.compiler.conditional_exile_templates.saga_chapter_per_player_nonsaga_exile", return_value=None):
            incomplete = compile_oracle_card(original, **kwargs)
        self.assertFalse(incomplete.status == "exact")
