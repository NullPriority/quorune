"""Native retained-target combat watches and the resolution-locked hand bonus."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.damage import DamageEvent
from quorune.deck import DeckDefinition, DeckEntry
from quorune.goad_instruction import WATCHED_COMBAT_DRAW_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.block_prohibitions import block_prohibition_verdict
from quorune.rules.capabilities import load_default_capability_registry
from quorune.saga_progression import dispatch_saga_chapters
from quorune.trigger_processing import additional_damage_batch_triggers, enqueue_trigger_batch
from scripts.build_test_database import build_fixture_database


class SagaGoadAndHandCountTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "saga-goad-watch.sqlite3"
        build_fixture_database(
            [ROOT / "tests/fixtures" / name for name in (
                "bound-effect-program-cards.json", "attached-destruction-set-cards.json",
                "conditional-exile-cards.json", "announced-x-public-damage-set-cards.json",
                "kang-dynasty-pinned-cards.json",
            )],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Saga goad watch",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    def chapter(self, game, source, number):
        source.counters["lore"] = number
        items = []
        dispatch_saga_chapters(game.engine, source, previous_lore=number - 1, trigger_batch=items)
        self.assertEqual(1, len(items))
        enqueue_trigger_batch(game.engine, items)
        game.engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.engine._grant_priority("A")
        game.engine.pump()
        self.checkpoint(game)

    def test_original_goad_chapter_watches_current_targets_after_saga_departure_and_replays(self):
        game = self.session(611211)
        saga = self.add(game.engine, "Kang Dynasty", ref="native-goad-watch")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="watched-bear")
        bear.acquired_control_turn_count = -1
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="already-tapped-watched-bear")
        other.tapped = True
        extra = self.add(game.engine, "Grizzly Bears", seat="B", ref="same-opponent-other-target")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand", ref="watch-source-removal")
        self.assert_closed(game, saga)
        self.prepare(game, mana={})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        game.state.config.semantic_policy = "trusted_only"
        self.chapter(game, saga, 1)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        before = authoritative_state_hash(game.state)
        refused = game.act("pilot:A", {"action_id": "choose", "targets": [bear.ref, extra.ref]})
        self.assertFalse(refused.ok, refused.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "A", (bear.ref, other.ref))
        self.assertIsNone(self.resolve(game))
        selected = [game.state.cards[card.object_id] for card in (bear, other)]
        self.assertTrue(all(card.tapped and any(x.player == "A" for x in card.goaded_by) for card in selected))
        waiting, = [row for row in game.state.delayed_triggers if WATCHED_COMBAT_DRAW_FIELD in row.condition]
        self.assertEqual(game.state.players["A"].turns_begun + 1,
                         waiting.condition[WATCHED_COMBAT_DRAW_FIELD]["expires_at_turns_begun"])

        self.cast(game, "B", removal, (saga.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[saga.object_id].zone)
        hand = set(game.state.players["A"].zones["hand"])
        self.until(game, lambda state: state.state.active_player == "B" and state.state.pending_decision is not None
                   and state.state.pending_decision.kind == "combat.attackers")
        current = game.state.cards[bear.object_id]
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {current.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.until(game, lambda state: len(state.state.players["A"].zones["hand"]) == len(hand) + 1)
        drawn, = set(game.state.players["A"].zones["hand"]) - hand
        self.assertNotIn("B", game.state.cards[drawn].known_to)
        self.replay(game, load=True)

    def test_second_chapter_excludes_other_damage_and_expires_on_actual_next_extra_turn(self):
        game = self.session(611212)
        saga = self.add(game.engine, "Kang Dynasty", ref="native-second-chapter")
        bear = self.add(game.engine, "Grizzly Bears", seat="B", ref="second-chapter-watched-bear")
        self.assert_closed(game, saga)
        self.prepare(game, mana={})
        game.state.config.semantic_policy = "trusted_only"
        game.engine.schedule_extra_turn("A", source="fixture-extra-turn")
        self.chapter(game, saga, 2)
        self.target(game, "A", (bear.ref,))
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[bear.object_id]
        hit = DamageEvent(
            source=current.ref, source_object_id=current.object_id, source_logical_object_id=current.logical_object_id,
            source_oracle_id=current.oracle_id, source_commander_designation_id=None, source_controller="B",
            source_owner="B", source_types=("creature",), source_subtypes=("bear",), source_colors=("G",),
            source_keywords=(), source_is_commander=False, target="C", target_kind="player",
            target_object_id=None, target_controller="C", target_types=(), target_subtypes=(),
            assigned_amount=2, dealt_amount=2, prevented_amount=0, combat=True, source_zone="battlefield",
        )
        before = authoritative_state_hash(game.state)
        exclusions = (
            replace(hit, combat=False),
            replace(hit, dealt_amount=0, prevented_amount=2),
            replace(hit, source_logical_object_id="returned-incarnation"),
            replace(hit, target=current.ref, target_kind="permanent", target_object_id=current.object_id,
                    target_logical_object_id=current.logical_object_id, target_controller="B", target_types=("creature",)),
        )
        self.assertEqual((), additional_damage_batch_triggers(game.engine, exclusions))
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(2, len(additional_damage_batch_triggers(game.engine, (hit, replace(hit, target="D")))))
        origin_turn = game.state.turn_sequence
        self.until(game, lambda state: state.state.turn_sequence > origin_turn)
        self.assertEqual("A", game.state.active_player)
        self.assertEqual((), additional_damage_batch_triggers(game.engine, (hit,)))

    def test_original_hand_count_bonus_locks_at_resolution_and_zero_is_unblockable(self):
        for cast_before in (False, True):
            with self.subTest(cast_before=cast_before):
                game = self.session(611213 + cast_before)
                saga = self.add(game.engine, "Kang Dynasty", ref="native-hand-count")
                creature = self.add(game.engine, "Grizzly Bears", ref="native-hand-count-target")
                blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="native-hand-count-blocker")
                for card_id in tuple(game.state.players["A"].zones["hand"]):
                    game.engine.move_card(card_id, "library", log=False)
                bolts = [self.add(game.engine, "Lightning Bolt", zone="hand", ref=f"hand-count-bolt-{index}")
                         for index in range(2)] if cast_before else []
                self.assert_closed(game, saga)
                self.prepare(game, mana={"R": 2})
                game.state.config.semantic_policy = "trusted_only"
                self.chapter(game, saga, 3)
                self.target(game, "A", (creature.ref,))
                if bolts:
                    self.cast(game, "A", bolts[0], ("D",))
                    self.resolve_top(game)
                before = len(game.state.players["A"].zones["hand"])
                self.assertEqual(int(cast_before), before)
                self.assertIsNone(self.resolve(game))
                current = game.state.cards[creature.object_id]
                self.assertEqual(2 + before, game.engine._numeric_stat(current.object_id, "power"))
                if bolts:
                    self.cast(game, "A", bolts[1], ("D",))
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(2 + before, game.engine._numeric_stat(current.object_id, "power"))
                self.assertIsNotNone(block_prohibition_verdict(game.engine, current, game.state.cards[blocker.object_id]))
                self.replay(game, load=True)

    def test_original_chapters_require_the_two_complete_exact_shapes(self):
        card = self.db.lookup("Kang Dynasty", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        with patch("quorune.compiler.saga_chapter_nodes._goad_and_hand_count_chapter", return_value=None):
            ir = compile_oracle_card(card, **options)
        self.assertEqual(2, len(ir.material_residuals))
