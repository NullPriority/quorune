"""One actual cast fact scales the original affected player's private discard."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import MAIN_PHASE_CAST_DISCARD_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FixedMainPhaseTargetDiscardTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "fixed-main-phase-target-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/fixed-main-phase-target-discard-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed private affected-player discard", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_preserves_one_target_and_gates_four_to_its_exact_cast_condition(self):
        admitted, semantics = register_card(self.db, self.registry, "Haunting Hymn")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        record = self.db.lookup("Haunting Hymn", fuzzy=False)
        spell, = semantics.programs_for_oracle(record.oracle_id)
        node, = spell.effects
        self.assertEqual(MAIN_PHASE_CAST_DISCARD_VERSION, node["schema_version"])
        normal, = node["otherwise_effects"]
        qualified, = node["effects"]
        self.assertEqual((2, 4), (normal["count"], qualified["count"]))
        self.assertEqual(normal, {**qualified, "count": 2})
        wrong_target = {**qualified, "target": "$target.1"}
        self.assertFalse(bind_semantic_program_runtime(
            replace(spell, effects=[{**node, "effects": [wrong_target]}]),
            capability_registry=self.registry, profile="commander_review",
        )["strict"])
        for text in ("Target player discards four cards.",
                     record.oracle_text.replace("that player discards four", "each opponent discards four"),
                     record.oracle_text.replace("your main phase", "your end step")):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry,
                capability_profile="commander_review",
            ).status)

    def hand(self, game, seat):
        return [game.state.cards[object_id] for object_id in game.state.players[seat].zones["hand"]]

    def private_choice(self, game, count):
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        self.assertEqual(count, pending.payload_by_actor["B"]["count"])
        hand = self.hand(game, "B")
        refs = [card.ref for card in hand[:count]]
        for viewer in ("pilot:A", "pilot:C", "spectator"):
            packet = game.packet(viewer, full=True)
            self.assertIsNone(packet["decision"])
            self.assertTrue(all(ref not in json.dumps(packet) for ref in refs))
        return refs

    def choose(self, game, actor, refs, *, allowed=True):
        prior = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, {"action_id": "choose", "cards": refs})
        self.assertEqual(allowed, result.ok, result.summary)
        if not allowed:
            self.assertEqual(prior, authoritative_state_hash(game.state))

    def test_actual_main_phase_original_discards_four_but_the_uncast_copy_uses_two(self):
        game = self.session(60130001)
        spell = self.add(game.engine, "Haunting Hymn", zone="hand")
        double = self.add(game.engine, "Twincast", seat="C", zone="hand")
        for card in (spell, double):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "B": 2})
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        initial = len(self.hand(game, "B"))
        self.cast_for(game, "A", spell, ["B"])
        original = game.state.stack[-1]
        self.cast_for(game, "C", double, [original.ref])
        choose_targets = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (choose_targets.kind, choose_targets.actors))
        result = game.act("pilot:C", {"action_id": "choose"})
        self.assertTrue(result.ok, result.summary)
        copied = self.private_choice(game, 2)
        self.choose(game, "B", copied)
        self.assertEqual(initial - 2, len(self.hand(game, "B")))
        refs = self.private_choice(game, 4)
        self.choose(game, "A", refs, allowed=False)
        self.choose(game, "B", refs[:3], allowed=False)
        game = self.replay(game, load=True)
        self.choose(game, "B", refs)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(initial - 6, len(self.hand(game, "B")))
        self.replay(game, load=True)

    def test_actual_opponent_main_phase_uses_two_and_preserves_the_affected_player(self):
        game = self.session(60130002)
        spell = self.add(game.engine, "Haunting Hymn", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, active="B", mana={"C": 4, "B": 2})
        self.checkpoint(game)
        prior = len(self.hand(game, "B"))
        self.cast_for(game, "A", spell, ["B"])
        self.choose(game, "B", self.private_choice(game, 2))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(prior - 2, len(self.hand(game, "B")))
        self.replay(game, load=True)

    def test_actual_short_hand_discards_only_all_three_available_cards(self):
        game = self.playing(60130003)
        spell = self.add(game.engine, "Haunting Hymn", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 4, "B": 2})
        self.checkpoint(game)
        original_hand = self.hand(game, "B")
        self.assertEqual(3, len(original_hand))
        self.cast_for(game, "A", spell, ["B"])
        self.choose(game, "B", self.private_choice(game, 3))
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.hand(game, "B"))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in original_hand))
        self.replay(game, load=True)
