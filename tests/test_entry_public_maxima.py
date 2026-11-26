import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.drawing.restrictions import drawn_this_turn
from quorune.entry_counter_model import DynamicEntryCounterValueSource
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class EntryPublicMaximaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry-public-maxima.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "inherited-rule-composition-cards.json",
            fixtures / "entry-public-maxima-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry maxima", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def pay_cast(self, game, seat, card, payment, targets=()):
        self.priority(game, seat)
        current = game.state.cards[card.object_id]
        offer = self.action(game, seat, current)
        result = game.act("pilot:" + seat, {
            "action_id": offer["id"], "targets": list(targets),
            "pay": "manual", "payment": payment,
        })
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_journal_uses_the_greatest_live_creature_count_including_tokens(self):
        game = self.session(61412350)
        journal = self.add(game.engine, "Investigator's Journal", zone="hand")
        alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand")
        for index, seat in enumerate("ABCC"):
            self.add(game.engine, "Grizzly Bears", seat=seat, ref=f"entry-maximum-bear-{index}")
        for card in (journal, alarm):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.pay_cast(game, "B", alarm, {"C": 1, "W": 1})
        self.assertEqual(2, sum(card.is_token and card.zone == "battlefield" and card.controller == "B"
                                for card in game.state.cards.values()))
        self.pay_cast(game, "A", journal, {"C": 2})
        current = game.state.cards[journal.object_id]
        self.assertEqual(("battlefield", 3), (current.zone, current.counters.get("suspect", 0)))
        self.replay(game, load=True)

    def test_actual_sponge_excludes_own_draws_and_uses_current_opponent_maximum(self):
        game = self.session(61412351)
        sponge = self.add(game.engine, "Thought Sponge", zone="hand", ref="private-entry-sponge")
        draws = [self.add(game.engine, "Inspiration", zone="hand", ref=f"entry-maximum-draw-{i}")
                 for i in range(4)]
        removal = self.add(game.engine, "Murder", zone="hand")
        for card in (sponge, *draws, removal):
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 16, "U": 5, "B": 2})
        self.checkpoint(game)
        for card, recipient in zip(draws, "AABC", strict=True):
            self.pay_cast(game, "A", card, {"C": 3, "U": 1}, [recipient])
        self.assertEqual([4, 2, 2, 0], [drawn_this_turn(game.engine, seat) for seat in "ABCD"])
        for seat in "BCD":
            self.assertNotIn(sponge.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        game = self.replay(game, load=True)
        self.pay_cast(game, "A", sponge, {"C": 3, "U": 1})
        self.assertEqual(2, game.state.cards[sponge.object_id].counters.get("+1/+1", 0))
        power = game.engine._numeric_stat(sponge.object_id, "power")
        self.pay_cast(game, "A", removal, {"C": 1, "B": 2}, [sponge.ref])
        if game.state.stack:
            self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[sponge.object_id].zone)
        self.assertEqual(4 + power, drawn_this_turn(game.engine, "A"))
        self.replay(game, load=True)

    def test_dropping_the_current_creature_maximum_fails_the_native_entry_witness(self):
        from quorune.semantic_runtime import self_entry_counters as owner

        original = owner.dynamic_entry_counter_amount

        def omit(host, **kwargs):
            if kwargs["amount_spec"].value_source is DynamicEntryCounterValueSource.MAX_PLAYER_CREATURES:
                return 0
            return original(host, **kwargs)

        result = unittest.TestResult()
        witness = type(self)("test_actual_journal_uses_the_greatest_live_creature_count_including_tokens")
        with patch.object(owner, "dynamic_entry_counter_amount", omit):
            witness.run(result)
        self.assertEqual([], result.errors)
        self.assertEqual(1, len(result.failures), result.failures)
        self.assertIn("('battlefield', 3)", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
