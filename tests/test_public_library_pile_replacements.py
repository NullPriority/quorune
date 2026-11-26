"""CR 404.3 and 614.6. Order applies to actual, replacement-aware arrivals."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_public_library_piles as sealed
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


REPLACEMENT = "Anafenza, the Foremost"
FIXTURES = ROOT / "tests" / "fixtures"


class PublicLibraryPileReplacementTests(InheritedNativeFixture, unittest.TestCase):
    scene = sealed.PublicLibraryPilesNativeTests.scene
    announce = sealed.PublicLibraryPilesNativeTests.announce
    stage = sealed.PublicLibraryPilesNativeTests.stage
    assert_visible = sealed.PublicLibraryPilesNativeTests.assert_visible
    assert_public_reveal = sealed.PublicLibraryPilesNativeTests.assert_public_reveal
    submit = sealed.PublicLibraryPilesNativeTests.submit
    reject = sealed.PublicLibraryPilesNativeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        work = Path(cls.temporary.name)
        reference = json.loads((FIXTURES / "graveyard-exile-replacement-cards.json").read_bytes())
        row, = [card for card in reference["cards"] if card["name"] == REPLACEMENT]
        extra = work / "creature-graveyard-exile.json"
        extra.write_text(json.dumps({
            "schema_version": 1,
            "source": "Single printed card from the sealed graveyard-exile replacement fixture",
            "cards": [row],
            "rulings": [
                ruling for ruling in reference.get("rulings", []) if ruling["oracle_id"] == row["oracle_id"]
            ],
        }), encoding="utf-8")
        path = work / "replacement-piles.sqlite3"
        build_fixture_database(
            [FIXTURES / "bound-effect-program-cards.json", FIXTURES / "public-library-piles-cards.json", extra],
            path,
        )
        cls.db = CardDatabase(path)
        cls.addClassCleanup(cls.db.close)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Replacement-aware public piles",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    def prepare_scene(self, *, spell_name, seed):
        game, spell, cards, hidden, private = self.scene(seed, seats="AB", neighbor=True, spell_name=spell_name)
        replacement = self.add(game.engine, REPLACEMENT, seat="B", ref="opponent-creature-exile-replacement")
        self.assert_closed(game, replacement)
        anchor = self.add(game.engine, "Generic Bound Plains", zone="graveyard", ref="old-graveyard-anchor")
        mana = {"C": 2, "U": 1, "R": 1} if spell_name == sealed.MIRROR else {"C": 3, "U": 1}
        self.announce(game, spell, cards, active="B", mana=mana)
        self.assertIsNotNone(self.resolve(game))
        self.assert_public_reveal(game, cards)
        return game, spell, cards, hidden, private, anchor

    def verify_after_pile_selection(self, game, spell, cards, anchor, hidden, private, hand, actual_gy, exile):
        desired_order = tuple(card.ref for card in actual_gy) if len(actual_gy) >= 2 else ()
        offered_order = ()
        decision = game.state.pending_decision
        if decision is not None and decision.kind == "semantic.choice":
            schema = self.stage(game, "A", "order_graveyard", "graveyard_order")
            offered_order = tuple(schema["legal_refs"])
            self.assert_visible(game, cards, actor="A", seats="AB", hidden=hidden, private_hand=private)
            self.assertEqual(
                [hidden.object_id, *(card.object_id for card in reversed(cards))],
                game.state.players["A"].zones["library"],
            )
            self.reject(game, "B", {"action_id": "choose", "graveyard_order": list(offered_order)})
            self.reject(game, "A", {"action_id": "choose", "graveyard_order": [hidden.ref, *offered_order[1:]]})
            if exile[0].ref not in offered_order:
                self.reject(game, "A", {"action_id": "choose", "graveyard_order": [*desired_order, exile[0].ref]})
            game = self.replay(game, load=True)
            reopened = self.stage(game, "A", "order_graveyard", "graveyard_order")
            self.assertEqual(offered_order, tuple(reopened["legal_refs"]))
            # The sealed version may still issue a printed-pile order. Submit exactly
            # its offered references so actual destinations and replay remain witnessed.
            chosen = [card.ref for card in actual_gy if card.ref in offered_order]
            chosen.extend(ref for ref in offered_order if ref not in chosen)
            self.submit(game, "A", graveyard_order=chosen)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([hidden.object_id], game.state.players["A"].zones["library"])
        self.assertEqual([spell.object_id, *(card.object_id for card in actual_gy), anchor.object_id],
                         list(reversed(game.state.players["A"].zones["graveyard"])))
        for expected, zone in ((hand, "hand"), (actual_gy, "graveyard"), (exile, "exile")):
            for card in expected:
                self.assertEqual(zone, game.state.cards[card.object_id].zone)
        self.assertEqual({card.object_id for card in exile}, set(game.state.players["A"].zones["exile"]))
        opponent = game.packet("pilot:B", full=True)["state"]["players"]["A"]
        self.assertNotIn("hand", opponent)
        self.assertEqual({card.ref for card in hand}, {row["id"] for row in opponent.get("known_hand", [])})
        resumed = self.replay(game, load=True)
        for zone in ("hand", "graveyard", "exile", "library"):
            self.assertEqual(game.state.players["A"].zones[zone], resumed.state.players["A"].zones[zone])
        print(json.dumps({
            "fixture": spell.printed_name,
            "public_reveal_and_role_authority": True,
            "destinations": {"hand": len(hand), "graveyard": len(actual_gy), "exile": len(exile)},
            "offered_graveyard_order_refs": len(offered_order),
            "expected_graveyard_order_refs": len(desired_order),
            "exact_replay": True,
        }, sort_keys=True), flush=True)
        self.assertEqual(set(desired_order), set(offered_order),
                         "The graveyard order offer must use actual simultaneous arrivals")

    def test_fact_or_fiction_omits_order_after_two_of_three_arrivals_are_exiled(self):
        game, spell, cards, hidden, private, anchor = self.prepare_scene(spell_name=sealed.CARD, seed=40430431)
        self.stage(game, "B", "divide", "piles")
        self.assert_visible(game, cards, actor="B", seats="AB", hidden=hidden, private_hand=private)
        game = self.replay(game, load=True)
        hands, printed_gy = cards[3:], cards[:3]
        self.submit(game, "B", piles={"first": [card.ref for card in hands],
                                       "second": [card.ref for card in printed_gy]})
        self.stage(game, "A", "choose_pile", "pile")
        self.reject(game, "B", {"action_id": "choose", "pile": "first"})
        self.submit(game, "A", pile="first")
        self.verify_after_pile_selection(game, spell, cards, anchor, hidden, private,
                                         hands, [cards[1]], [cards[0], cards[2]])

    def test_steam_augury_only_orders_the_two_actual_graveyard_arrivals(self):
        game, spell, cards, hidden, private, anchor = self.prepare_scene(spell_name=sealed.MIRROR, seed=40430432)
        self.stage(game, "A", "divide", "piles")
        self.assert_visible(game, cards, actor="A", seats="AB", hidden=hidden, private_hand=private)
        hands = [cards[2], cards[4]]
        # Printed pile order differs from the controller's requested physical order.
        printed_gy = [cards[1], cards[0], cards[3]]
        self.submit(game, "A", piles={"first": [card.ref for card in hands],
                                       "second": [card.ref for card in printed_gy]})
        self.stage(game, "B", "choose_pile", "pile")
        self.reject(game, "A", {"action_id": "choose", "pile": "first"})
        game = self.replay(game, load=True)
        self.submit(game, "B", pile="first")
        self.verify_after_pile_selection(game, spell, cards, anchor, hidden, private,
                                         hands, [cards[3], cards[1]], [cards[0]])


if __name__ == "__main__":
    unittest.main()
