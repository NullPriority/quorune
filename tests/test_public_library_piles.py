"""CR 404.3 and 700.3. Native public piles preserve object identity and owner order."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


CARD = "Fact or Fiction"
MIRROR = "Steam Augury"
ARTIFACT = "Generic Bound Mentor"
FIXTURES = ROOT / "tests" / "fixtures"
TOP_NAMES = (
    "Generic Bound Body",
    "Generic Bound Plains",
    ARTIFACT,
    "Generic Bound Counter Adder",
    "Generic Bound Draw Loss",
)


class PublicLibraryPilesNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        path = Path(cls.temporary.name) / "public-library-piles.sqlite3"
        build_fixture_database(
            [
                FIXTURES / "bound-effect-program-cards.json",
                FIXTURES / "public-library-piles-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Public pile choices",
            [
                DeckEntry("Generic Bound Commander", 1, "commander"),
                DeckEntry("Generic Bound Plains", 30),
            ],
            ["Generic Bound Commander"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def scene(self, seed, *, seats="ABCD", count=5, neighbor=False, spell_name=CARD):
        game = CommanderSession.create(
            self.db,
            {seat: deepcopy(self.deck) for seat in seats},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        keep_all(game)
        game.state.config.semantic_policy = "trusted_only"
        for oid in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(oid, "outside", log=False, semantic_events=False)
        hidden = None
        if neighbor:
            hidden = self.add(
                game.engine, "Generic Bound Growth", zone="library", ref="hidden-pile-neighbor"
            )
            hidden.known_to = []
            hidden.revealed_to = []
        cards = []
        for index in reversed(range(count)):
            card = self.add(
                game.engine, TOP_NAMES[index], zone="library", ref=f"public-pile-card-{index}"
            )
            card.known_to = []
            card.revealed_to = []
            cards.insert(0, card)
        private_hand = game.state.cards[game.state.players[seats[-1]].zones["hand"][0]]
        spell = self.add(game.engine, spell_name, zone="hand", ref="public-pile-source")
        return game, spell, cards, hidden, private_hand

    def announce(self, game, spell, cards, *, active="A", mana=None):
        record = self.db.lookup(spell.printed_name, fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active=active, mana=mana or {"C": 3, "U": 1})
        action = self.action(game, "A", spell)
        self.assertFalse(action.get("target_schema"))
        self.checkpoint(game)
        command = {"action_id": action["id"], "pay": "auto"}
        self.reject(game, "A", {**command, "targets": ["B"]})
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual((), tuple(game.state.stack[-1].targets))
        observer = game.packet("pilot:B", full=True)
        self.assertNotIn("known_top", observer["state"]["players"]["A"])
        for card in cards:
            self.assertNotIn(card.ref, json.dumps(observer))

    def stage(self, game, actor, stage, field):
        decision = game.state.pending_decision
        self.assertIsNotNone(decision, f"Expected {stage}")
        self.assertEqual("semantic.choice", decision.kind)
        self.assertEqual((actor,), tuple(decision.actors))
        self.assertEqual(["pilot:" + actor], game.pending_principals())
        packet = game.packet("pilot:" + actor, full=True)
        projected_decision = packet["decision"]
        ctx = projected_decision["ctx"]
        self.assertEqual("public_library_piles", ctx["operation"])
        self.assertEqual(stage, ctx["stage"])
        actions = ctx["legal_actions"]
        selected_opponent = actor != game.state.stack[-1].controller and stage in {"divide", "choose_pile"}
        expected_ids = ["choose", "concede"] if selected_opponent else ["choose"]
        self.assertEqual(expected_ids, [action["id"] for action in actions])
        schema = actions[0]["choice_schema"]
        self.assertEqual(field, schema["field"])
        self.assertEqual(expected_ids, [action["id"] for action in projected_decision["legal_actions"]])
        projected_action = projected_decision["legal_actions"][0]
        self.assertEqual("choose", projected_action["id"])
        control, = projected_action["form"]["fields"]
        self.assertEqual(field, control["name"])
        if stage == "divide":
            self.assertEqual("ordered_partition", control["control"])
            self.assertEqual(set(schema["legal_refs"]), {option["value"] for option in control["options"]})
        return schema

    def assert_visible(self, game, cards, *, actor, seats, hidden=None, private_hand=None):
        expected = [card.ref for card in cards]
        for seat in seats:
            packet = game.packet("pilot:" + seat, full=True)
            known = packet["state"]["players"]["A"].get("known_top", [])
            self.assertEqual(expected, [card["id"] for card in known])
            if seat != actor:
                self.assertIsNone(packet["decision"])
            rendered = json.dumps(packet)
            if hidden is not None:
                self.assertNotIn(hidden.ref, rendered)
            if private_hand is not None and seat != private_hand.owner:
                self.assertNotIn(private_hand.ref, rendered)
        spectator = game.packet("spectator", full=True)
        self.assertIsNone(spectator["decision"])
        if hidden is not None:
            self.assertNotIn(hidden.ref, json.dumps(spectator))
        if private_hand is not None:
            self.assertNotIn(private_hand.ref, json.dumps(spectator))

    def assert_public_reveal(self, game, cards):
        packet = game.packet("spectator", full=True, cursor_key="public-pile-reveal-audit")
        reveal, = [row for row in packet.get("events", []) if row["c"] == "library.piles.reveal"]
        for card in cards:
            self.assertIn(card.printed_name, reveal["s"])

    def submit(self, game, actor, **fields):
        result = game.act("pilot:" + actor, {"action_id": "choose", **fields})
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, actor, response):
        before = authoritative_state_hash(game.state)
        commands = len(game.commands)
        result = game.act("pilot:" + actor, response)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(commands, len(game.commands))

    def assert_destinations(self, game, cards, expected_hand, expected_graveyard):
        self.assertEqual(
            set(expected_hand) | set(expected_graveyard), {card.object_id for card in cards}
        )
        for oid in expected_hand:
            self.assertEqual("hand", game.state.cards[oid].zone)
            self.assertEqual("A", game.state.cards[oid].owner)
        for oid in expected_graveyard:
            self.assertEqual("graveyard", game.state.cards[oid].zone)
            self.assertEqual("A", game.state.cards[oid].owner)

    def test_four_player_cast_reveal_divide_select_and_owner_order_replay(self):
        game, spell, cards, hidden, private = self.scene(4043001, neighbor=True)
        self.announce(game, spell, cards, active="B")
        self.assertIsNotNone(self.resolve(game))
        self.assertEqual("B", game.state.active_player)
        opponent = self.stage(game, "A", "choose_opponent", "opponent")
        self.assertEqual({"B", "C", "D"}, set(opponent["legal_values"]))
        self.assert_public_reveal(game, cards)
        self.assert_visible(game, cards, actor="A", seats="ABCD", hidden=hidden, private_hand=private)
        self.reject(game, "B", {"action_id": "choose", "opponent": "C"})
        self.reject(game, "A", {"action_id": "choose", "opponent": "A"})
        self.reject(game, "A", {"action_id": "choose", "opponent": "Z"})
        game = self.replay(game, load=True)
        self.submit(game, "A", opponent="C")
        divide = self.stage(game, "C", "divide", "piles")
        self.assertEqual("ordered_partition", divide["shape"])
        self.assertEqual({"first", "second"}, set(divide["partitions"]))
        self.assertEqual({card.ref for card in cards}, set(divide["legal_refs"]))
        self.assertTrue(divide["complete"])
        self.assertTrue(divide["distinct"])
        self.assert_visible(game, cards, actor="C", seats="ABCD", hidden=hidden, private_hand=private)
        game = self.replay(game, load=True)
        first = [cards[1].ref, cards[3].ref]
        second = [cards[4].ref, cards[0].ref, cards[2].ref]
        for actor, piles in (
            ("A", {"first": first, "second": second}),
            ("D", {"first": first, "second": second}),
            ("C", {"first": [*first, first[0]], "second": second}),
            ("C", {"first": [first[0], first[0]], "second": second}),
            ("C", {"first": first, "second": [first[0], *second]}),
            ("C", {"first": first, "second": second[:-1]}),
            ("C", {"first": [hidden.ref, first[1]], "second": second}),
            ("C", {"first": first, "second": second, "third": []}),
        ):
            with self.subTest(actor=actor, shape=tuple(piles)):
                self.reject(game, actor, {"action_id": "choose", "piles": piles})
        partition = {"first": first, "second": second}
        self.submit(game, "C", piles=partition)
        self.reject(game, "C", {"action_id": "choose", "piles": partition})
        choice = self.stage(game, "A", "choose_pile", "pile")
        self.assertEqual({"first", "second"}, set(choice["legal_values"]))
        self.assert_visible(game, cards, actor="A", seats="ABCD", hidden=hidden, private_hand=private)
        self.reject(game, "C", {"action_id": "choose", "pile": "first"})
        self.reject(game, "A", {"action_id": "choose", "pile": "third"})
        game = self.replay(game, load=True)
        self.submit(game, "A", pile="first")
        order = self.stage(game, "A", "order_graveyard", "graveyard_order")
        self.assertEqual(set(second), set(order["legal_refs"]))
        self.assertEqual(
            [hidden.object_id, *(card.object_id for card in reversed(cards))],
            game.state.players["A"].zones["library"],
        )
        self.reject(game, "C", {"action_id": "choose", "graveyard_order": second})
        self.reject(game, "A", {"action_id": "choose", "graveyard_order": [second[0], second[0], second[2]]})
        self.reject(game, "A", {"action_id": "choose", "graveyard_order": [second[0], hidden.ref, second[2]]})
        game = self.replay(game, load=True)
        order_top = [cards[0], cards[4], cards[2]]
        self.submit(game, "A", graveyard_order=[card.ref for card in order_top])
        self.assertIsNone(self.resolve(game))
        self.assert_destinations_and_replay(game, spell, cards, [cards[1], cards[3]], order_top)
        self.assertEqual([hidden.object_id], game.state.players["A"].zones["library"])
        again = {"action_id": "choose", "graveyard_order": [card.ref for card in order_top]}
        self.reject(game, "A", again)
        self.reject(game, "A", again)

    def assert_destinations_and_replay(self, game, spell, cards, hand, graveyard_top):
        self.assert_destinations(game, cards, [card.object_id for card in hand], [
            card.object_id for card in graveyard_top
        ])
        self.assertEqual(
            [spell.object_id, *(card.object_id for card in graveyard_top)],
            list(reversed(game.state.players["A"].zones["graveyard"])),
        )
        self.replay(game, load=True)

    def test_duel_short_library_can_route_all_cards_to_either_destination(self):
        for chosen in ("first", "second"):
            with self.subTest(chosen=chosen):
                game, spell, cards, _, private = self.scene(
                    4043010 + int(chosen == "second"), seats="AB", count=2
                )
                self.announce(game, spell, cards)
                self.assertIsNotNone(self.resolve(game))
                split = self.stage(game, "B", "divide", "piles")
                self.assertEqual({card.ref for card in cards}, set(split["legal_refs"]))
                self.assert_visible(game, cards, actor="B", seats="AB", private_hand=private)
                self.submit(game, "B", piles={"first": [], "second": [card.ref for card in cards]})
                choose = self.stage(game, "A", "choose_pile", "pile")
                self.assertEqual({"first", "second"}, set(choose["legal_values"]))
                self.submit(game, "A", pile=chosen)
                if chosen == "first":
                    self.stage(game, "A", "order_graveyard", "graveyard_order")
                    self.submit(game, "A", graveyard_order=[card.ref for card in reversed(cards)])
                    self.assertIsNone(self.resolve(game))
                    self.assert_destinations_and_replay(game, spell, cards, [], list(reversed(cards)))
                else:
                    self.assertIsNone(self.resolve(game))
                    self.assert_destinations_and_replay(game, spell, cards, cards, [])
                self.assertEqual([], game.state.players["A"].zones["library"])

    def test_empty_library_resolves_without_seat_choice_or_failed_draw(self):
        game, spell, cards, _, _ = self.scene(4043020, count=0)
        self.announce(game, spell, cards)
        before_hand = len(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before_hand, len(game.state.players["A"].zones["hand"]))
        self.assertTrue(game.state.players["A"].in_game)
        self.assert_destinations_and_replay(game, spell, cards, [], [])

    def test_mirror_controller_divides_before_opponent_selects(self):
        game, spell, cards, hidden, private = self.scene(4043030, neighbor=True, spell_name=MIRROR)
        self.announce(game, spell, cards, active="B", mana={"C": 2, "U": 1, "R": 1})
        self.assertIsNotNone(self.resolve(game))
        divide = self.stage(game, "A", "divide", "piles")
        self.assertEqual({card.ref for card in cards}, set(divide["legal_refs"]))
        self.assert_public_reveal(game, cards)
        self.assert_visible(game, cards, actor="A", seats="ABCD", hidden=hidden, private_hand=private)
        first, second = cards[:2], cards[2:]
        partition = {"first": [card.ref for card in first], "second": [card.ref for card in second]}
        self.reject(game, "B", {"action_id": "choose", "piles": partition})
        self.reject(game, "A", {"action_id": "choose", "opponent": "C"})
        self.submit(game, "A", piles=partition)
        opponent = self.stage(game, "A", "choose_opponent", "opponent")
        self.assertEqual({"B", "C", "D"}, set(opponent["legal_values"]))
        game = self.replay(game, load=True)
        self.submit(game, "A", opponent="C")
        choose = self.stage(game, "C", "choose_pile", "pile")
        self.assertEqual({"first", "second"}, set(choose["legal_values"]))
        self.reject(game, "A", {"action_id": "choose", "pile": "second"})
        self.submit(game, "C", pile="second")
        order = self.stage(game, "A", "order_graveyard", "graveyard_order")
        self.assertEqual({card.ref for card in first}, set(order["legal_refs"]))
        self.reject(game, "C", {"action_id": "choose", "graveyard_order": [card.ref for card in first]})
        graveyard_top = list(reversed(first))
        self.submit(game, "A", graveyard_order=[card.ref for card in graveyard_top])
        self.assertIsNone(self.resolve(game))
        self.assert_destinations_and_replay(game, spell, cards, second, graveyard_top)

    def test_wrong_final_chooser_mutation_breaks_the_ownership_witness(self):
        game, spell, cards, _, _ = self.scene(4043040, seats="AB", count=2)
        self.announce(game, spell, cards)
        self.assertIsNotNone(self.resolve(game))
        self.stage(game, "B", "divide", "piles")
        mutated = self.replay(game, load=True)
        piles = {"first": [cards[0].ref], "second": [cards[1].ref]}
        self.submit(game, "B", piles=piles)
        self.stage(game, "A", "choose_pile", "pile")

        from quorune.public_library_piles_model import PublicLibraryPileState

        original_actor = PublicLibraryPileState.actor

        def use_splitter_for_final_choice(state, spec):
            if state.stage == "choose_pile":
                return state.opponent
            return original_actor(state, spec)

        with patch.object(PublicLibraryPileState, "actor", use_splitter_for_final_choice):
            self.submit(mutated, "B", piles=piles)
            self.stage(mutated, "B", "choose_pile", "pile")
            with self.assertRaises(AssertionError):
                self.stage(mutated, "A", "choose_pile", "pile")


if __name__ == "__main__":
    unittest.main()
