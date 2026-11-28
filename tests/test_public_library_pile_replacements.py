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

    def dihada_scene(self, seed, cards, *, replacement=(), loyalty=5):
        game = self.session(seed)
        source = self.add(game.engine, "Dihada, Binder of Wills", ref="source-dihada")
        source.counters["loyalty"] = loyalty
        for name, seat in replacement:
            self.assert_closed(game, self.add(game.engine, name, seat=seat))
        # Seal a short known top for the full public-choice transcript.
        for object_id in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(object_id, "hand", reason="Set a short library", log=False)
        top = [self.add(game.engine, name, zone="library", ref=ref) for name, ref in cards]
        for card in top:
            card.known_to = []
            card.revealed_to = []
        self.assert_closed(game, source)
        return game, source, top

    def dihada_activate(self, game, source, delta, *, targets=()):
        self.priority(game, "A")
        source = game.state.cards[source.object_id]
        ability, = (ability for ability in game.engine._activated_abilities(source)
                    if ability.loyalty_delta == delta)
        command = {"action_id": f"activate:{source.ref}:{ability.ability_id}", "targets": list(targets)}
        self.reject(game, "B", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def dihada_choice(self, game):
        decision = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        choose = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
                      if row["id"] == "choose")
        self.assertEqual("cards", choose["choice_schema"]["field"])
        return choose["choice_schema"]

    def dihada_treasures(self, game):
        return [card for card in game.state.cards.values() if card.zone == "battlefield"
                and card.controller == "A" and card.is_token and "Treasure" in card.printed_name]

    def test_dihada_actual_graveyard_subset_orders_and_pays_only_committed_arrivals(self):
        game, source, top = self.dihada_scene(60603501, (
            ("Forest", "dihada-land"), ("Grizzly Bears", "dihada-creature"),
            ("Lightning Bolt", "dihada-instant"), ("Isamaru, Hound of Konda", "dihada-legend"),
        ), replacement=((REPLACEMENT, "B"),))
        land, creature, spell, legendary = top
        outside = self.add(game.engine, "Isamaru, Hound of Konda", zone="graveyard", ref="old-legend")
        hidden = self.add(game.engine, "Plains", seat="C", zone="hand", ref="unrevealed-foreign-hand")
        self.prepare(game)
        self.checkpoint(game)
        self.dihada_activate(game, source, -3)
        self.assertEqual(2, game.state.cards[source.object_id].counters["loyalty"])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        first = self.dihada_choice(game)
        self.assertEqual([legendary.ref], first["legal_refs"])
        for actor in ("pilot:B", "spectator"):
            packet = game.packet(actor, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(hidden.ref, json.dumps(packet))
        visible = game.packet("pilot:B", full=True)["state"]["players"]["A"].get("known_top", [])
        self.assertEqual([card.ref for card in reversed(top)], [row["id"] for row in visible])
        for refs in ([creature.ref], [outside.ref], [legendary.ref, legendary.ref]):
            self.reject(game, "A", {"action_id": "choose", "cards": refs})
        game = self.replay(game, load=True)
        self.submit(game, "A", cards=[legendary.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        order = self.dihada_choice(game)
        self.assertEqual({land.ref, spell.ref}, set(order["legal_refs"]))
        self.assertFalse(self.dihada_treasures(game))
        self.reject(game, "A", {"action_id": "choose", "cards": [spell.ref, creature.ref]})
        self.reject(game, "B", {"action_id": "choose", "cards": [spell.ref, land.ref]})
        game = self.replay(game, load=True)
        self.submit(game, "A", cards=[spell.ref, land.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([outside.object_id, spell.object_id, land.object_id], game.state.players["A"].zones["graveyard"])
        self.assertEqual("exile", game.state.cards[creature.object_id].zone)
        self.assertEqual("hand", game.state.cards[legendary.object_id].zone)
        self.assertEqual(2, len(self.dihada_treasures(game)))
        self.replay(game)

    def test_dihada_choose_all_and_empty_under_competing_public_replacements(self):
        for take_all in (True, False):
            with self.subTest(take_all=take_all):
                names = (("Isamaru, Hound of Konda", "legend-one"), ("Isamaru, Hound of Konda", "legend-two"))
                replaced = () if take_all else (("Rest in Peace", "B"), ("Leyline of the Void", "C"))
                game, source, top = self.dihada_scene(60603502 + take_all, names, replacement=replaced)
                self.prepare(game)
                self.checkpoint(game)
                self.dihada_activate(game, source, -3)
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self.assertEqual({card.ref for card in top}, set(self.dihada_choice(game)["legal_refs"]))
                self.submit(game, "A", cards=[card.ref for card in top] if take_all else [])
                pending = self.resolve(game)
                if not take_all:
                    self.assertEqual("replacement.order", pending.kind)
                    game = self.replay(game, load=True)
                    for _ in range(len(top)):
                        self.assertEqual("replacement.order", game.state.pending_decision.kind)
                        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                        self.submit(game, "A", replacement=options[0]["id"])
                        pending = self.resolve(game)
                self.assertIsNone(pending)
                self.assertFalse(self.dihada_treasures(game))
                expected = "hand" if take_all else "exile"
                self.assertTrue(all(game.state.cards[card.object_id].zone == expected for card in top))
                self.assertEqual(2, game.state.cards[source.object_id].counters["loyalty"])
                self.replay(game)

    def test_dihada_existing_loyalty_abilities_and_commander_setup_remain_native(self):
        from quorune.deck import DeckLoader
        from quorune.session import CommanderSession

        name = "Dihada, Binder of Wills"
        deck = DeckDefinition("Dihada Commander fixture", [DeckEntry(name, 1, "commander"), DeckEntry("Plains", 99)], [name])
        self.assertEqual([], DeckLoader(self.db).validate_commander_deck(deck))
        created = CommanderSession.create(self.db, {"A": deck, "B": self.deck}, first_player="A", seed=60603510)
        command, = [created.state.cards[oid] for oid in created.state.players["A"].zones["command"]]
        self.assertEqual((name, "A", True), (command.printed_name, command.owner, command.is_commander))

        game, source, _top = self.dihada_scene(60603511, ())
        legendary = self.add(game.engine, "Isamaru, Hound of Konda", ref="loyalty-legendary-creature")
        self.prepare(game)
        self.checkpoint(game)
        self.dihada_activate(game, source, 2, targets=[legendary.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(7, game.state.cards[source.object_id].counters["loyalty"])
        self.assertTrue({"vigilance", "lifelink", "indestructible"} <= set(game.engine._combat_keywords(game.state.cards[legendary.object_id])))
        self.replay(game)

        game, source, _top = self.dihada_scene(60603512, (), loyalty=12)
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="ultimate-creature")
        ring = self.add(game.engine, "Sol Ring", seat="B", ref="ultimate-artifact")
        land = self.add(game.engine, "Plains", seat="B", ref="ultimate-land")
        for card in (creature, ring, land):
            card.tapped = True
        self.prepare(game)
        self.checkpoint(game)
        self.dihada_activate(game, source, -11)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[source.object_id].counters["loyalty"])
        for card in (creature, ring):
            moved = game.state.cards[card.object_id]
            self.assertEqual(("A", "B", False), (moved.controller, moved.owner, moved.tapped))
        self.assertIn("haste", game.engine._combat_keywords(game.state.cards[creature.object_id]))
        land = game.state.cards[land.object_id]
        self.assertEqual(("B", True), (land.controller, land.tapped))
        self.replay(game)

    def test_dihada_exact_public_legendary_treasure_shape_is_required(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.revealed_legendary_graveyard_treasure import (
            revealed_legendary_treasure_capabilities, revealed_legendary_treasure_effect,
        )

        record = self.db.lookup("Dihada, Binder of Wills", fuzzy=False)
        compiler = lambda candidate: compile_oracle_card(
            candidate, capability_registry=self.registry, capability_profile="commander_review",
        )
        self.assertEqual("exact", compiler(record).status)
        with patch("quorune.compiler.fixed_library_selection_templates.RevealedLegendaryTreasureTemplate.compiled",
                   return_value=(None, (), None, ())):
            self.assertNotEqual("exact", compiler(record).status)
        self.assertNotEqual("exact", compiler(replace(
            record, oracle_text=record.oracle_text.replace("Reveal the top four", "Reveal the top three"),
        )).status)
        with self.assertRaises(ValueError):
            revealed_legendary_treasure_capabilities({**revealed_legendary_treasure_effect(), "public_reveal": False})

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
