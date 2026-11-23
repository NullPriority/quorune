"""The original multi-option discard Ward has deterministic private evidence."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameState
from quorune.record import authoritative_state_hash, replay_record
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_choices.defaults import default_semantic_choice_registry
from scripts.build_test_database import build_fixture_database


class WardDiscardReplayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "discard-ward.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "event-player-modifier-cards.json",
            fixtures / "ward-discard-replay-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        ruler = "Generic Bound Commander"
        cls.deck = DeckDefinition("Discard Ward", [
            DeckEntry(ruler, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [ruler])

    def scene(self, seed, *, legacy=False):
        game = self.session(seed)
        ward = self.add(game.engine, "Westgate Regent", ref="WARD-PERMANENT")
        spell = self.add(game.engine, "Word of Seizing", seat="B", zone="hand", ref="TARGETING-SPELL")
        unpaid = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="z-payment")
        payment = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="a-payment")
        foreign = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="OTHER-SEAT")
        self.assert_closed(game, ward)
        self.assert_closed(game, spell)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="B", mana={"C": 3, "R": 2})
        if legacy:
            game.state.ward_discard_order_version = None
            game.state.cards = dict(sorted(game.state.cards.items()))
        self.checkpoint(game)
        self.cast_for(game, "B", spell, [ward.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        return game, ward, spell, payment, unpaid, foreign

    def options(self, game):
        decision = game.state.pending_decision
        schema = decision.payload_by_actor["B"]["legal_actions"][0]["choice_schema"]
        _, frozen = default_semantic_choice_registry().decode_continuation(decision.continuation)
        refs = schema["legal_refs"]
        self.assertEqual(refs, [card["id"] for card in schema["cards"]])
        self.assertEqual(refs, [card["ref"] for card in frozen.effect["_legal_objects"]])
        self.assertGreater(len(refs), 2)
        return list(refs)

    def test_actual_multi_option_discard_is_private_stable_and_replayable(self):
        game, ward, spell, payment, unpaid, foreign = self.scene(70221046)
        self.assertEqual(1, game.state.ward_discard_order_version)
        self.assertEqual(1, format_state_versions(game.state)["ward_discard_order_version"])
        refs = self.options(game)
        self.assertEqual(sorted(refs), refs)
        self.assertIn(payment.ref, refs)
        self.assertIn(unpaid.ref, refs)
        self.assertNotIn(foreign.ref, refs)
        for seat in "ACD":
            packet = game.packet(f"pilot:{seat}", full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(payment.ref, json.dumps(packet))
        original = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", {"action_id": "choose", "cards": [payment.ref]}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [foreign.ref]}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [payment.ref, unpaid.ref]}).ok)
        self.assertEqual(original, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.assertEqual(refs, self.options(game))
        paid = game.act("pilot:B", {"action_id": "choose", "cards": [payment.ref]})
        self.assertTrue(paid.ok, paid.summary)
        self.assertEqual("graveyard", game.state.cards[payment.object_id].zone)
        self.assertEqual("hand", game.state.cards[unpaid.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[ward.object_id].controller)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_stale_physical_card_cannot_pay_the_current_choice(self):
        game, _, _, payment, _, _ = self.scene(70221047)
        game.engine.move_card(payment.object_id, "graveyard", log=False)
        game.engine.move_card(payment.object_id, "hand", log=False)
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [payment.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_legacy_record_keeps_its_original_option_order_and_replays(self):
        game, _, _, _, _, _ = self.scene(70221048, legacy=True)
        self.assertIsNone(game.state.ward_discard_order_version)
        self.assertNotIn("ward_discard_order_version", format_state_versions(game.state))
        self.assertNotIn("ward_discard_order_version", game.state.to_dict())
        before = self.options(game)
        self.assertNotEqual(sorted(before), before)
        game = self.replay(game, load=True)
        self.assertEqual(before, self.options(game))

    def test_manifest_and_checkpoint_require_the_same_typed_version(self):
        state = self.session(70221049).state
        self.assertEqual(1, GameState.from_dict(state.to_dict()).ward_discard_order_version)
        fmt = format_state_versions(state)
        validate_state_versions({"format": fmt}, state)
        for invalid in (None, False, "1", 2):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_state_versions({"format": {**fmt, "ward_discard_order_version": invalid}}, state)
