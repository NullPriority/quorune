"""Original Ring chapter and persisted source-controller block legality."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.block_prohibitions import block_prohibition_verdict
from quorune.rules.capabilities import load_default_capability_registry
from quorune.saga_progression import dispatch_saga_chapters
from quorune.trigger_processing import enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database


class SagaSourceControlledBlockTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-controlled-block.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / name for name in (
                "bound-effect-program-cards.json", "attached-destruction-set-cards.json",
                "permanent-control-exchange-cards.json", "inherited-rule-composition-cards.json",
                "conditional-exile-cards.json",
                "there-and-back-again-pinned-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Source-controlled block",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    def exchange(self, game, seat, totem, source):
        self.priority(game, seat)
        offer, = [row for row in game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"].startswith(f"activate:{totem.ref}:")]
        used = game.act(f"pilot:{seat}", {"action_id": offer["id"], "targets": [source.ref],
                                         "pay": "manual", "payment": {"C": 5}})
        self.assertTrue(used.ok, used.summary)
        self.assertIsNone(self.resolve(game))

    def test_original_block_lifetime_and_ring_choice_replay(self):
        for index, scenario in enumerate(("control_exchange", "no_target", "early_departure")):
            choose_blocker = scenario != "no_target"
            with self.subTest(scenario=scenario):
                game = self.session(6110210 + index)
                saga = self.add(game.engine, "There and Back Again", zone="hand", ref="original-saga")
                attacker = self.add(game.engine, "Grizzly Bears", ref="ring-eligible")
                blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="blocker")
                totem = self.add(game.engine, "Avarice Totem", seat="B", ref="control-exchange")
                removal = self.add(game.engine, "Disenchant", seat="B", zone="hand", ref="early-departure")
                self.assert_closed(game, saga)
                self.prepare(game, mana={"C": 8, "R": 2})
                game.state.players["B"].mana_pool.update({"C": 5, "W": 1})
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                self.cast(game, "A", saga)
                self.resolve_top(game)
                self.target(game, "A", (blocker.ref,) if choose_blocker else ())
                if scenario == "early_departure":
                    self.cast(game, "B", removal, (saga.ref,))
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[saga.object_id].zone)
                waiting = self.resolve(game)
                self.assertEqual("semantic.choice", waiting.kind)
                game = self.replay(game, load=True)
                result = game.act("pilot:A", {"action_id": "choose", "objects": [attacker.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                from quorune.ring_state import current_ring_state
                self.assertEqual(1, current_ring_state(game.engine, "A").temptation_count)

                army = lambda card: game.state.cards[card.object_id]
                verdict = block_prohibition_verdict(game.engine, army(attacker), army(blocker))
                self.assertEqual(scenario == "control_exchange", verdict is not None)
                if scenario == "control_exchange":
                    self.exchange(game, "B", totem, saga)
                    self.assertEqual("B", army(saga).controller)
                    self.assertIsNone(block_prohibition_verdict(game.engine, army(attacker), army(blocker)))
                    self.exchange(game, "A", totem, saga)
                    self.assertEqual("A", army(saga).controller)
                    self.assertIsNone(block_prohibition_verdict(game.engine, army(attacker), army(blocker)))
                self.replay(game, load=True)

    def test_actual_smaug_dies_and_creates_fourteen_treasures(self):
        game = self.session(7140314)
        saga = self.add(game.engine, "There and Back Again", ref="smaug-final-chapter")
        removal = self.add(game.engine, "Doom Blade", seat="B", zone="hand", ref="kill-smaug")
        self.assert_closed(game, saga)
        self.prepare(game, mana={})
        game.state.players["B"].mana_pool.update({"C": 1, "B": 1})
        game.state.config.semantic_policy = "trusted_only"
        saga.counters["lore"] = 3
        items = []
        dispatch_saga_chapters(game.engine, saga, previous_lore=2, trigger_batch=items)
        enqueue_trigger_batch(game.engine, items)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)
        self.assertIsNone(self.resolve(game))
        smaug, = [card for card in game.state.cards.values() if card.is_token and card.printed_name == "Smaug"]
        self.cast(game, "B", removal, (smaug.ref,))
        self.assertIsNone(self.resolve(game))
        treasures = [card for card in game.state.cards.values()
                     if card.is_token and card.controller == "A" and card.zone == "battlefield"
                     and "treasure" in game.engine._effective_type_parts(card)[1]]
        self.assertEqual(14, len(treasures))
        self.replay(game, load=True)

    def test_omitting_the_original_duration_leaves_only_its_chapter_unresolved(self):
        record = self.db.lookup("There and Back Again", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(record, **options).status)
        with patch("quorune.compiler.saga_chapter_nodes._source_controlled_block_chapter", return_value=None):
            incomplete = compile_oracle_card(record, **options)
        self.assertEqual(1, len(incomplete.material_residuals))
