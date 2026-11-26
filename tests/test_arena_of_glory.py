"""Native red-priced exert mana, paid haste, and its existing land entry."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.exert_model import EXERT_PLAYER_ANNOTATION
from quorune.fixed_mana_results import EXERT_MANA_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.exert_mana import EXERT_MANA_HANDLER_ID
from scripts.build_test_database import build_fixture_database


class ExertManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "exert-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/exert-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Exert mana", [DeckEntry(commander, 1, "commander"),
                                                DeckEntry("Plains", 30)], [commander])

    def act(self, game, command):
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def play_arena(self, game, arena):
        action, = (row for row in self.actions(game) if row["id"] == "play-land:" + arena.ref)
        self.act(game, {"action_id": action["id"]})
        return game.state.cards[arena.object_id]

    def payment_scene(self, seed, *, mountain=True, red=1):
        game = self.session(seed)
        arena = self.add(game.engine, "Arena of Glory", zone="hand")
        if mountain:
            control = self.add(game.engine, "Mountain")
            control.tapped = True
        self.prepare(game, mana={"R": red})
        self.assert_closed(game, arena)
        return game, arena

    def exert(self, game, arena):
        matches = [row for row in self.actions(game)
                   if row.get("source") == arena.ref and row.get("ability") == "ab3"]
        action, = matches
        self.act(game, {"action_id": action["id"], "pay": "auto", "mana_output": {"R": 2}})
        self.assertTrue(game.state.cards[arena.object_id].tapped)
        self.assertEqual(["A"], game.state.cards[arena.object_id].annotations[EXERT_PLAYER_ANNOTATION])
        self.assertEqual(2, game.state.players["A"].mana_pool["R"])
        self.assertEqual(1, sum(event.code == "permanent.exerted" for event in game.state.events))

    def test_arena_exert_exact_cost_and_withdrawn_typed_carrier_fail_closed(self):
        game = self.session(70143001)
        arena = self.add(game.engine, "Arena of Glory", zone="hand")
        self.assert_closed(game, arena)
        card = self.db.by_oracle_id(arena.oracle_id)
        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry,
                                       capability_profile="commander_review")
        self.assertEqual("exact", compile_card(card).status)
        for text in (card.oracle_text.replace("{R}, {T}, Exert", "{G}, {T}, Exert"),
                     card.oracle_text.replace("Exert this land:", "Exert another land:")):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_card(replace(card, oracle_text=text)).status)
        emitter, = (program for program in game.engine.semantics.programs_for_oracle(arena.oracle_id)
                    if any(row.get("handler_id") == EXERT_MANA_HANDLER_ID for row in program.handlers))
        malformed = replace(emitter,
            handlers=[row for row in emitter.handlers if row.get("handler_id") != EXERT_MANA_HANDLER_ID])
        proof = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(proof["strict"], proof)
        self.assertTrue(any("runtime_exert_mana" in blocker for blocker in proof["blockers"]), proof)
        self.assertIn(EXERT_MANA_CAPABILITY, emitter.capability_dependencies)

    def test_actual_arena_exert_red_pays_for_two_creatures_with_exact_replay(self):
        game, arena = self.payment_scene(70143002)
        first = self.add(game.engine, "Mons's Goblin Raiders", zone="hand", ref="first-paid-creature")
        second = self.add(game.engine, "Mons's Goblin Raiders", zone="hand", ref="second-paid-creature")
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        self.assertFalse(self.play_arena(game, arena).tapped)
        self.exert(game, arena)
        game = self.replay(game, load=True)
        for card in (first, second):
            self.cast(game, "A", game.state.cards[card.object_id])
            self.resolve_top(game)
            current = game.state.cards[card.object_id]
            self.assertEqual("battlefield", current.zone)
            self.assertIn("haste", game.engine._combat_keywords(current))
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        replayed = self.replay(game, load=True)
        from quorune.tap_state import expire_exert_untap_restrictions

        self.assertEqual(frozenset(), expire_exert_untap_restrictions(replayed.engine, "B"))
        self.assertEqual(frozenset({arena.object_id}), expire_exert_untap_restrictions(replayed.engine, "A"))
        self.assertEqual(frozenset(), expire_exert_untap_restrictions(replayed.engine, "A"))

    def test_actual_exert_mana_pays_noncreature_without_giving_its_tokens_haste(self):
        game, arena = self.payment_scene(70143003)
        spell = self.add(game.engine, "Krenko's Command", zone="hand")
        self.checkpoint(game)
        self.assertFalse(self.play_arena(game, arena).tapped)
        self.exert(game, arena)
        self.cast(game, "A", spell)
        self.resolve_top(game)
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(tokens))
        self.assertTrue(all("haste" not in game.engine._combat_keywords(card) for card in tokens))
        self.replay(game, load=True)

    def test_actual_arena_mountain_and_unpayable_exert_keep_ordinary_mana(self):
        for mountain in (False, True):
            with self.subTest(mountain=mountain):
                game, arena = self.payment_scene(70143004 + mountain, mountain=mountain, red=0)
                self.checkpoint(game)
                self.assertIs(not mountain, self.play_arena(game, arena).tapped)
                actions = self.actions(game)
                self.assertNotIn("activate:" + arena.ref + ":ab3", {row["id"] for row in actions})
                before = authoritative_state_hash(game.state)
                bad = game.act("pilot:A", {"a": "activate", "source": arena.ref, "ability": "ab3", "pay": "auto"})
                self.assertFalse(bad.ok, bad.summary)
                self.assertEqual(before, authoritative_state_hash(game.state))
                if mountain:
                    self.act(game, {"action_id": "activate:" + arena.ref + ":ab2", "pay": "auto"})
                    self.assertEqual(1, game.state.players["A"].mana_pool["R"])
                    self.assertNotIn(EXERT_PLAYER_ANNOTATION, game.state.cards[arena.object_id].annotations)
                self.replay(game, load=True)


    def test_actual_arena_automatic_cast_commits_exert_before_multiplied_mana_and_replays(self):
        game, arena = self.payment_scene(70143010)
        self.add(game.engine, "Mana Reflection")
        hill = self.add(game.engine, "Hill Giant", zone="hand")
        self.checkpoint(game)
        self.assertFalse(self.play_arena(game, arena).tapped)
        self.cast(game, "A", hill)
        self.assertEqual(["A"], game.state.cards[arena.object_id].annotations[EXERT_PLAYER_ANNOTATION])
        self.assertEqual(0, game.state.players["A"].mana_pool["R"])
        self.assertEqual(1, sum(event.code == "permanent.exerted" for event in game.state.events))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn("haste", game.engine._combat_keywords(game.state.cards[hill.object_id]))
        self.replay(game, load=True)
