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
                FIXTURES / "library-search-rules-cards.json",
                FIXTURES / "opening-hand-battlefield-cards.json",
                FIXTURES / "targeted-spell-control-cards.json",
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
        selected_opponent = actor != game.state.stack[-1].controller and stage in {"divide", "choose_pile", "choose_card"}
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

    def intuition_search(self, game, actor, count):
        self.assertEqual("semantic.search", self.resolve(game).kind)
        self.assertEqual([actor], list(game.state.pending_decision.actors))
        data = game.packet("pilot:" + actor, full=True)["decision"]
        schema = data["legal_actions"][0]["choice_schema"]
        self.assertEqual((count, count, False), (
            schema["minimum"], schema["maximum"], schema["rules_may_fail_to_find"],
        ))
        return schema

    def start_intuition(self, game, spell, *, target="C"):
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        self.reject(game, "A", {"action_id": "cast:" + spell.ref, "targets": ["A"], "pay": "auto"})
        self.cast(game, "A", spell, [target])
        self.assertEqual([target], list(game.state.stack[-1].targets))

    def _gifts_scene(self, seed, names=("Plains", "Island", "Forest", "Grizzly Bears", "Mindlock Orb")):
        game, spell, _, _, private = self.scene(seed, count=0, spell_name="Gifts Ungiven")
        cards = [
            self.add(game.engine, name, zone="library", ref=f"gifts-card-{index}")
            for index, name in reversed(tuple(enumerate(names)))
        ][::-1]
        for card in cards:
            card.known_to = []
            card.revealed_to = []
        return game, spell, cards, private

    def _start_gifts(self, game, spell, target):
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "U": 1})
        self.checkpoint(game)
        self.reject(game, "A", {"action_id": "cast:" + spell.ref, "targets": ["A"], "pay": "auto"})
        self.cast(game, "A", spell, [target])
        self.assertEqual([target], list(game.state.stack[-1].targets))

    def _gifts_search(self, game, maximum=4):
        self.assertEqual("semantic.search", self.resolve(game).kind)
        self.assertEqual(["A"], list(game.state.pending_decision.actors))
        schema = game.packet("pilot:A", full=True)["decision"]["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, maximum), (schema["minimum"], schema["maximum"]))
        self.assertIs(schema["different_names"], True)
        return schema

    def _gifts_order_arrivals(self, game, chosen):
        schema = self.stage(game, "A", "order_graveyard", "graveyard_order")
        self.assertEqual({card.ref for card in chosen}, set(schema["legal_refs"]))
        self.submit(game, "A", graveyard_order=[card.ref for card in reversed(chosen)])
        self.settle(game)

    def test_actual_gifts_private_distinct_search_and_targeted_two_card_choice_replay(self):
        names = ("Plains", "Island", "Forest", "Grizzly Bears", "Mindlock Orb", "Plains")
        for count in (3, 4):
            target = "C" if count == 3 else "D"
            with self.subTest(count=count, target=target):
                game, spell, cards, private = self._gifts_scene(7012390 + count, names)
                initial_shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
                self._start_gifts(game, spell, target)
                schema = self._gifts_search(game)
                self.assertEqual({card.ref for card in cards}, set(schema["legal_refs"]))
                for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                    packet = game.packet(viewer, full=True)
                    self.assertIsNone(packet["decision"])
                    for card in cards:
                        self.assertNotIn(card.ref, json.dumps(packet))
                for actor, subjects in (
                    ("A", cards[:5]), ("A", [cards[0], cards[5]]),
                    ("A", [cards[1], cards[1]]), (target, cards[:count]),
                ):
                    self.reject(game, actor, {"action_id": "choose", "search_cards": [card.ref for card in subjects]})
                selected = cards[:count]
                game = self.replay(game, load=True)
                self.submit(game, "A", search_cards=[card.ref for card in selected])
                decision = self.stage(game, target, "choose_card", "cards")
                self.assertEqual((2, 2), (decision["minimum"], decision["maximum"]))
                self.assertEqual({card.ref for card in selected}, set(decision["legal_refs"]))
                event, = (event for event in game.state.events if event.code == "library.search.reveal")
                self.assertEqual({card.ref for card in selected}, set(event.details["objects"]))
                for viewer in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
                    packet = game.packet(viewer, full=True, cursor_key="gifts-reveal-audit:" + viewer)
                    self.assertEqual(viewer == "pilot:" + target, packet["decision"] is not None)
                    rendered = json.dumps(packet)
                    self.assertTrue(all(card.ref not in rendered for card in cards[count:]))
                    if viewer != "pilot:D":
                        self.assertNotIn(private.ref, rendered)
                    self.assertTrue(any(row["c"] == "library.search.reveal" for row in packet.get("events", ())))
                chosen = [selected[0], selected[-1]]
                self.reject(game, "B", {"action_id": "choose", "cards": [card.ref for card in chosen]})
                for subjects in (selected[:1], selected[:3], [selected[0], cards[-1]], [selected[0], selected[0]]):
                    self.reject(game, target, {"action_id": "choose", "cards": [card.ref for card in subjects]})
                game = self.replay(game, load=True)
                self.submit(game, target, cards=[card.ref for card in chosen])
                self.assertEqual(initial_shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
                self._gifts_order_arrivals(game, chosen)
                self.assert_destinations(game, selected, [card.object_id for card in selected[1:-1]],
                                         [card.object_id for card in chosen])
                self.assertEqual(initial_shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual({card.object_id for card in cards[count:]},
                                 set(game.state.players["A"].zones["library"]))
                self.replay(game, load=True)

    def test_actual_gifts_zero_one_and_two_results_bypass_the_opponent_choice(self):
        for count in range(3):
            with self.subTest(count=count):
                game, spell, cards, _ = self._gifts_scene(7012400 + count)
                self._start_gifts(game, spell, "C")
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                self._gifts_search(game)
                selected = cards[:count]
                self.submit(game, "A", search_cards=[card.ref for card in selected])
                if count == 2:
                    self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
                    game = self.replay(game, load=True)
                    self._gifts_order_arrivals(game, selected)
                else:
                    self.settle(game)
                self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in selected))
                self.assertEqual({card.object_id for card in cards[count:]},
                                 set(game.state.players["A"].zones["library"]))
                self.assertEqual(before + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.replay(game, load=True)

    def test_actual_gifts_constrained_and_denied_searches_keep_the_public_result_bound(self):
        for prohibited in (False, True):
            with self.subTest(prohibited=prohibited):
                game, spell, cards, _ = self._gifts_scene(7012410 + prohibited)
                restriction = self.add(game.engine, "Mindlock Orb" if prohibited else "Aven Mindcensor", seat="D")
                self.assert_closed(game, restriction)
                self._start_gifts(game, spell, "B")
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                if prohibited:
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual({card.object_id for card in cards}, set(game.state.players["A"].zones["library"]))
                    self.assertTrue(any(event.code == "library.search.prohibited" for event in game.state.events))
                    self.assertFalse(any(event.code == "library.search.reveal" for event in game.state.events))
                else:
                    schema = self._gifts_search(game)
                    self.assertEqual({card.ref for card in cards[:4]}, set(schema["legal_refs"]))
                    self.reject(game, "A", {"action_id": "choose", "search_cards": [cards[-1].ref]})
                    self.submit(game, "A", search_cards=[card.ref for card in cards[:4]])
                    self.stage(game, "B", "choose_card", "cards")
                    self.submit(game, "B", cards=[card.ref for card in cards[:2]])
                    self._gifts_order_arrivals(game, cards[:2])
                    self.assert_destinations(game, cards[:4], [card.object_id for card in cards[2:4]],
                                             [card.object_id for card in cards[:2]])
                    self.assertEqual([cards[-1].object_id], game.state.players["A"].zones["library"])
                self.assertEqual(before + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.replay(game, load=True)

    def test_actual_gifts_target_departure_respects_the_resolution_boundary(self):
        for during in (False, True):
            with self.subTest(during=during):
                game, spell, cards, _ = self._gifts_scene(7012420 + during)
                self._start_gifts(game, spell, "B")
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                if during:
                    self._gifts_search(game)
                    self.submit(game, "A", search_cards=[card.ref for card in cards[:3]])
                    self.stage(game, "B", "choose_card", "cards")
                else:
                    self.priority(game, "B")
                accepted = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
                self.assertTrue(accepted.ok, accepted.summary)
                if during:
                    choice = self.stage(game, "A", "choose_opponent", "opponent")
                    self.assertEqual({"C", "D"}, set(choice["legal_values"]))
                    self.reject(game, "A", {"action_id": "choose", "opponent": "B"})
                    game = self.replay(game, load=True)
                    self.submit(game, "A", opponent="D")
                    schema = self.stage(game, "D", "choose_card", "cards")
                    self.assertEqual((2, 2), (schema["minimum"], schema["maximum"]))
                    self.submit(game, "D", cards=[card.ref for card in cards[1:3]])
                    self._gifts_order_arrivals(game, cards[1:3])
                    self.assert_destinations(game, cards[:3], [cards[0].object_id],
                                             [card.object_id for card in cards[1:3]])
                else:
                    self.assertIsNone(self.resolve(game))
                    self.assertTrue(all(game.state.cards[card.object_id].zone == "library" for card in cards))
                    self.assertFalse(any(event.code == "library.search.reveal" for event in game.state.events))
                self.settle(game)
                self.assertEqual(before + int(during), game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_actual_intuition_private_search_and_targeted_public_choice_replay(self):
        game, spell, cards, hidden, private = self.scene(7012301, neighbor=True, spell_name="Intuition")
        initial_shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
        self.start_intuition(game, spell)
        schema = self.intuition_search(game, "A", 3)
        self.assertEqual({card.ref for card in (*cards, hidden)}, set(schema["legal_refs"]))
        selected = (cards[0], cards[3], cards[4])
        selected_refs = [card.ref for card in selected]
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            serialized = json.dumps(game.packet(viewer, full=True))
            for card in (*cards, hidden):
                self.assertNotIn(card.ref, serialized)
        self.reject(game, "A", {"action_id": "choose", "search_cards": selected_refs[:2]})
        self.reject(game, "A", {"action_id": "choose", "search_cards": [selected_refs[0]] * 3})
        self.reject(game, "C", {"action_id": "choose", "search_cards": selected_refs})
        game = self.replay(game, load=True)
        self.submit(game, "A", search_cards=selected_refs)

        chosen = self.stage(game, "C", "choose_card", "cards")
        self.assertEqual(set(selected_refs), set(chosen["legal_refs"]))
        for viewer in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = game.packet(viewer, full=True, cursor_key="intuition-reveal-audit:" + viewer)
            serialized = json.dumps(packet)
            self.assertNotIn(hidden.ref, serialized)
            if viewer != "pilot:D":
                self.assertNotIn(private.ref, serialized)
            for card in cards[1:3]:
                self.assertNotIn(card.ref, serialized)
            reveals = [row for row in packet.get("events", []) if row["c"] == "library.search.reveal"]
            self.assertEqual(1, len(reveals))
            for card in selected:
                self.assertIn(card.printed_name, reveals[0]["s"])
        event, = (event for event in game.state.events if event.code == "library.search.reveal")
        self.assertEqual(set(selected_refs), set(event.details["objects"]))
        self.reject(game, "B", {"action_id": "choose", "cards": [selected_refs[0]]})
        self.reject(game, "C", {"action_id": "choose", "cards": [hidden.ref]})
        self.reject(game, "C", {"action_id": "choose", "cards": selected_refs[:2]})
        self.assertEqual(initial_shuffles, game.state.players["A"].stats.get("shuffle_count", 0))

        game = self.replay(game, load=True)
        self.submit(game, "C", cards=[selected_refs[1]])
        order = self.stage(game, "A", "order_graveyard", "graveyard_order")
        self.assertEqual({selected_refs[2], selected_refs[0]}, set(order["legal_refs"]))
        self.assertEqual(initial_shuffles, game.state.players["A"].stats.get("shuffle_count", 0))
        self.submit(game, "A", graveyard_order=[selected_refs[2], selected_refs[0]])
        self.settle(game)
        self.assertEqual("hand", game.state.cards[selected[1].object_id].zone)
        graveyard = game.state.players["A"].zones["graveyard"]
        self.assertGreater(graveyard.index(selected[2].object_id), graveyard.index(selected[0].object_id))
        self.assertEqual(initial_shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
        self.assertEqual({hidden.object_id, cards[1].object_id, cards[2].object_id},
                         set(game.state.players["A"].zones["library"]))
        self.replay(game, load=True)

    def test_actual_intuition_short_and_empty_library_still_shuffles_once(self):
        for count in range(3):
            with self.subTest(count=count):
                game, spell, cards, _, _ = self.scene(7012310 + count, count=count, spell_name="Intuition")
                self.start_intuition(game, spell)
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                schema = self.intuition_search(game, "A", count)
                self.assertEqual({card.ref for card in cards}, set(schema["legal_refs"]))
                self.submit(game, "A", search_cards=[card.ref for card in cards])
                if cards:
                    self.stage(game, "C", "choose_card", "cards")
                    self.submit(game, "C", cards=[cards[-1].ref])
                    self.assertEqual("hand", game.state.cards[cards[-1].object_id].zone)
                    self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in cards[:-1]))
                self.settle(game)
                self.assertEqual(before + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertFalse(game.state.players["A"].zones["library"])
                self.replay(game, load=True)

    def test_actual_intuition_respects_search_prohibition_and_top_four_replacement(self):
        for prohibited in (False, True):
            with self.subTest(prohibited=prohibited):
                game, spell, cards, hidden, _ = self.scene(7012320 + prohibited, neighbor=True, spell_name="Intuition")
                self.add(game.engine, "Mindlock Orb" if prohibited else "Aven Mindcensor", seat="D")
                self.start_intuition(game, spell, target="B")
                initial_shuffles = game.state.players["A"].stats.get("shuffle_count", 0)
                if prohibited:
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual({card.object_id for card in (*cards, hidden)},
                                     set(game.state.players["A"].zones["library"]))
                    self.assertTrue(any(event.code == "library.search.prohibited" for event in game.state.events))
                    self.assertFalse(any(event.code == "library.search" for event in game.state.events))
                else:
                    schema = self.intuition_search(game, "A", 3)
                    self.assertEqual({card.ref for card in cards[:4]}, set(schema["legal_refs"]))
                    self.reject(game, "A", {"action_id": "choose", "search_cards": [cards[0].ref, cards[1].ref, hidden.ref]})
                    self.submit(game, "A", search_cards=[card.ref for card in cards[:3]])
                    self.stage(game, "B", "choose_card", "cards")
                    self.submit(game, "B", cards=[cards[0].ref])
                    self.stage(game, "A", "order_graveyard", "graveyard_order")
                    self.submit(game, "A", graveyard_order=[cards[1].ref, cards[2].ref])
                    self.settle(game)
                    self.assertEqual({cards[3].object_id, cards[4].object_id, hidden.object_id},
                                     set(game.state.players["A"].zones["library"]))
                self.assertEqual(initial_shuffles + 1, game.state.players["A"].stats.get("shuffle_count", 0))
                self.replay(game, load=True)

    def test_actual_intuition_target_departure_before_resolution_and_during_choice(self):
        for during in (False, True):
            with self.subTest(during=during):
                game, spell, cards, _, _ = self.scene(7012330 + during, count=1, spell_name="Intuition")
                self.start_intuition(game, spell, target="B")
                before = game.state.players["A"].stats.get("shuffle_count", 0)
                if during:
                    self.intuition_search(game, "A", 1)
                    self.submit(game, "A", search_cards=[cards[0].ref])
                    self.stage(game, "B", "choose_card", "cards")
                else:
                    self.priority(game, "B")
                accepted = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
                self.assertTrue(accepted.ok, accepted.summary)
                if during:
                    choice = self.stage(game, "A", "choose_opponent", "opponent")
                    self.assertEqual({"C", "D"}, set(choice["legal_values"]))
                    self.reject(game, "A", {"action_id": "choose", "opponent": "B"})
                    game = self.replay(game, load=True)
                    self.submit(game, "A", opponent="D")
                    self.stage(game, "D", "choose_card", "cards")
                    self.submit(game, "D", cards=[cards[0].ref])
                else:
                    self.assertIsNone(self.resolve(game))
                self.settle(game)
                self.assertEqual("hand" if during else "library", game.state.cards[cards[0].object_id].zone)
                self.assertEqual(before + int(during), game.state.players["A"].stats.get("shuffle_count", 0))
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)

    def test_actual_intuition_uses_current_spell_controller_library_after_control_change(self):
        game, spell, old_cards, _, _ = self.scene(7012340, count=3, spell_name="Intuition")
        takeover = self.add(game.engine, "Aethersnatch", seat="D", zone="hand")
        selected = [self.add(game.engine, "Generic Bound Body", seat="D", zone="library",
                             ref=f"new-controller-library-card-{i}") for i in range(3)]
        for card in selected:
            card.known_to = []
            card.revealed_to = []
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["D"].mana_pool.update({"C": 4, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", spell, ["C"])
        original = game.state.stack[-1].ref
        self.cast(game, "D", takeover, [original])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.submit(game, "D")
        original_item, = game.state.stack
        self.assertEqual(("D", ["C"]), (original_item.controller, original_item.targets))
        schema = self.intuition_search(game, "D", 3)
        self.assertTrue({card.ref for card in selected} <= set(schema["legal_refs"]))
        self.assertTrue({card.ref for card in old_cards}.isdisjoint(schema["legal_refs"]))
        self.submit(game, "D", search_cards=[card.ref for card in selected])
        self.stage(game, "C", "choose_card", "cards")
        self.submit(game, "C", cards=[selected[0].ref])
        self.stage(game, "D", "order_graveyard", "graveyard_order")
        self.submit(game, "D", graveyard_order=[selected[1].ref, selected[2].ref])
        self.settle(game)
        self.assertIn(selected[0].object_id, game.state.players["D"].zones["hand"])
        for card in selected[1:]:
            self.assertIn(card.object_id, game.state.players["D"].zones["graveyard"])
        self.assertEqual({card.object_id for card in old_cards}, set(game.state.players["A"].zones["library"]))
        self.assertIn(spell.object_id, game.state.players["A"].zones["graveyard"])
        self.replay(game, load=True)

    def test_actual_intuition_has_no_cast_offer_with_no_legal_opponent(self):
        game, spell, _, _, _ = self.scene(7012341, count=1, spell_name="Intuition")
        for seat in "BCD":
            self.add(game.engine, "Leyline of Sanctity", seat=seat, ref=f"intuition-protected-{seat}")
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + spell.ref, {action["id"] for action in actions})
        self.reject(game, "A", {"action_id": "cast:" + spell.ref, "targets": ["B"], "pay": "auto"})

    def _karn_activate(self, game, card, ability):
        action_id = f"activate:{card.ref}:{ability}"
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {row["id"] for row in offered})
        result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack[-1].targets)

    def test_karn_scion_of_urza_public_opponent_and_a_different_source_can_return_the_marked_card(self):
        game, karn, cards, hidden, _ = self.scene(20721901, count=2, neighbor=True,
                                                  spell_name="Karn, Scion of Urza")
        next_karn = self.add(game.engine, "Karn, Scion of Urza", zone="hand", ref="another-karn")
        bounce = self.add(game.engine, "Boomerang", zone="hand")
        foreign = self.add(game.engine, "Generic Bound Body", seat="B", zone="exile", ref="foreign-silver-card")
        foreign.counters["silver"] = 1
        unmarked = self.add(game.engine, "Generic Bound Body", zone="exile", ref="owned-unmarked-card")
        self.assert_closed(game, karn)
        self.assert_closed(game, bounce)
        self.prepare(game, mana={"C": 8, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", karn)
        self.assertIsNone(self.resolve(game))
        self._karn_activate(game, karn, "ab1")
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        self.reject(game, "B", {"action_id": "choose", "opponent": "C"})
        self.submit(game, "A", opponent="C")
        self.assertEqual(["C"], game.state.pending_decision.actors)
        packet = game.packet("pilot:C", full=True)
        self.assertEqual({card.ref for card in cards}, {row["id"] for row in packet["decision"]["ctx"]["objects"]})
        self.assertNotIn(hidden.ref, json.dumps(packet))
        game = self.replay(game, load=True)
        self.reject(game, "B", {"action_id": "choose", "objects": [cards[1].ref]})
        self.reject(game, "C", {"action_id": "choose", "objects": [hidden.ref]})
        self.submit(game, "C", objects=[cards[1].ref])
        marked = game.state.cards[cards[0].object_id]
        self.assertEqual(("exile", 1), (marked.zone, marked.counters.get("silver")))
        self.assertEqual("hand", game.state.cards[cards[1].object_id].zone)
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)

        self.cast(game, "A", bounce, [karn.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[karn.object_id].zone)
        self.cast(game, "A", next_karn)
        self.assertIsNone(self.resolve(game))
        self._karn_activate(game, next_karn, "ab2")
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        self.assertEqual([marked.ref], [row["id"] for row in
            game.packet("pilot:A", full=True)["decision"]["ctx"]["objects"]])
        game = self.replay(game, load=True)
        self.reject(game, "A", {"action_id": "choose", "objects": [foreign.ref]})
        self.reject(game, "A", {"action_id": "choose", "objects": [unmarked.ref]})
        self.submit(game, "A", objects=[marked.ref])
        returned = game.state.cards[marked.object_id]
        self.assertEqual("hand", returned.zone)
        self.assertEqual(0, returned.counters.get("silver", 0))
        self.replay(game, load=True)

    def test_karn_scion_of_urza_short_libraries_use_no_fabricated_opponent_or_exile_choice(self):
        for count in (0, 1):
            with self.subTest(count=count):
                game, karn, cards, _, _ = self.scene(20721941 + count, count=count, seats="AB",
                                                     spell_name="Karn, Scion of Urza")
                self.assert_closed(game, karn)
                self.prepare(game, mana={"C": 4})
                self.checkpoint(game)
                self.cast(game, "A", karn)
                self.assertIsNone(self.resolve(game))
                self._karn_activate(game, karn, "ab1")
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.stack)
                self.assertEqual(6, game.state.cards[karn.object_id].counters["loyalty"])
                self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in cards))
                self.assertFalse(any(card.zone == "exile" and card.counters.get("silver", 0)
                                     for card in game.state.cards.values()))
                self.replay(game, load=True)

    def test_karn_scion_of_urza_loyalty_scope_and_exile_counter_are_closed(self):
        from dataclasses import replace
        from unittest.mock import patch
        from quorune.replacement import AffectedObject, ReplaceableEvent
        from quorune.replacement.entry_counter_types import EXILE_ENTRY_COUNTER_CHILD, retain_committed_zone_counters
        from quorune.rules.silver_exile_loyalty import silver_loyalty_runtime_dependencies
        from test_controller_creature_keyword_absence import register_card

        card = self.db.lookup("Karn, Scion of Urza", fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        self.assertEqual("exact", compile_oracle_card(card, **options).status)
        report, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = [program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.cost_schema and program.cost_schema.get("loyalty_delta") in (1, -1)]
        self.assertEqual(2, len(programs))
        for program in programs:
            self.assertTrue(silver_loyalty_runtime_dependencies(program))
            with self.assertRaises(ValueError):
                silver_loyalty_runtime_dependencies(replace(program, active_zone="graveyard"))
        with patch("quorune.compiler.activation_paid_sacrifice_results.silver_loyalty_activation_result", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(card, **options).status)
        subject = AffectedObject("card", "A", None)
        counter = ReplaceableEvent(event_id="silver", kind="counter.place", affected_player=None,
                                   affected_object=subject, payload={EXILE_ENTRY_COUNTER_CHILD: True})
        outgoing = ReplaceableEvent(event_id="move", kind="zone.change", affected_player=None,
                                    affected_object=subject, payload={"destination": "exile"}, children=(counter,))
        self.assertEqual((counter,), retain_committed_zone_counters(outgoing).children)
        self.assertEqual((), retain_committed_zone_counters(replace(outgoing, payload={"destination": "hand"})).children)

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
