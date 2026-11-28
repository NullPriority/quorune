"""Fertilid's target owns the private search, battlefield result, and shuffle."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, packet_without_decision_capability
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TargetPlayerBasicLandSearchNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "target-player-basic-land-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/external-entry-tapped-cards.json",
            ROOT / "tests/fixtures/library-search-dispatch-cards.json",
            ROOT / "tests/fixtures/library-search-rules-cards.json",
            ROOT / "tests/fixtures/target-player-basic-land-search-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Target player basic land search", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 24),
        ], [commander])

    def source(self, game, *, cast=False, counters=2):
        card = self.add(game.engine, "Fertilid", zone="hand" if cast else "battlefield")
        self.assert_closed(game, card)
        if not cast:
            card.counters["+1/+1"] = counters
        return card

    def prepare_activation(self, game, *, mana=None):
        self.prepare(game, mana={"C": 1, "G": 1} if mana is None else mana)
        self.checkpoint(game)

    def activation_offer(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = (row for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        return offer

    def activate(self, game, source, target):
        offer = self.activation_offer(game, source)
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [target], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        top = game.state.stack[-1]
        self.assertEqual(("A", [target]), (top.controller, top.targets))

    def search_decision(self, game, target):
        self.assertEqual([f"pilot:{target}"], game.pending_principals())
        decision = game.packet(f"pilot:{target}", full=True)["decision"]
        self.assertEqual("semantic.search", decision["kind"])
        self.assertTrue(decision["cap"])
        self.assertEqual("search", decision["ctx"]["operation"])
        for observer in "ABCD":
            if observer != target:
                self.assertIsNone(game.packet(f"pilot:{observer}", full=True)["decision"])
        return decision

    def begin_search(self, game, target):
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.search", [target]), (pending.kind, pending.actors))
        return self.search_decision(game, target)["legal_actions"][0]["choice_schema"]

    def choose(self, game, actor, refs=()):
        result = game.act(f"pilot:{actor}", {"action_id": "choose", "search_cards": list(refs)})
        self.assertTrue(result.ok, result.summary)

    def assert_rejected(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        accepted = len(game.commands)
        result = game.act(f"pilot:{actor}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertFalse(result.state_changed)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertEqual(accepted, len(game.commands))

    def libraries(self, game):
        return {seat: tuple(player.zones["library"]) for seat, player in game.state.players.items()}

    def shuffle_counts(self, game):
        return {seat: player.stats.get("shuffle_count", 0) for seat, player in game.state.players.items()}

    def assert_shuffled_only(self, game, before_counts, prior_libraries, target, *, moved=None):
        expected_counts = {**before_counts, target: before_counts[target] + 1}
        self.assertEqual(expected_counts, self.shuffle_counts(game), "Only the targeted library is shuffled")
        for seat, library in self.libraries(game).items():
            if seat == target:
                self.assertCountEqual([object_id for object_id in prior_libraries[seat] if object_id != moved], library)
            else:
                self.assertEqual(prior_libraries[seat], library)

    def assert_tapped_land(self, game, land, seat):
        current = game.state.cards[land.object_id]
        self.assertEqual((seat, seat, "battlefield", True),
                         (current.owner, current.controller, current.zone, current.tapped))
        self.assertIn(land.object_id, game.state.players[seat].zones["battlefield"])

    def test_fertilid_charges_announced_cost_and_target_searches_basic_into_play_tapped(self):
        game = self.session(701_230_271)
        source = self.source(game, cast=True)
        basic = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-found:basic")
        dual = self.add(game.engine, "Volcanic Island", seat="B", zone="library", ref="fertilid-held:dual")
        other_library = self.add(game.engine, "Wastes", zone="library", ref="fertilid-held:other-owner")
        self.prepare_activation(game, mana={"C": 3, "G": 2})
        self.cast_for(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual(2, sum(game.state.players["A"].mana_pool.values()))

        offer = self.activation_offer(game, source)
        self.assertEqual(set("ABCD"), set(offer["target_schema"]["legal_refs"]))
        shuffles, libraries = self.shuffle_counts(game), self.libraries(game)
        self.activate(game, source, "B")
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(libraries, self.libraries(game))

        schema = self.begin_search(game, "B")
        self.assertEqual((0, 1), (schema["minimum"], schema["maximum"]))
        self.assertTrue(schema["rules_may_fail_to_find"])
        self.assertIn(basic.ref, schema["legal_refs"])
        self.assertNotIn(dual.ref, schema["legal_refs"])
        self.assertNotIn(other_library.ref, schema["legal_refs"])
        self.choose(game, "B", [basic.ref])
        self.assertIsNone(self.resolve(game))
        self.assert_tapped_land(game, basic, "B")
        self.assert_shuffled_only(game, shuffles, libraries, "B", moved=basic.object_id)
        self.assertEqual("library", game.state.cards[dual.object_id].zone)
        self.replay(game)

    def test_fertilid_can_target_self_and_fail_to_find_then_shuffles_only_that_library(self):
        game = self.session(701_230_272)
        source = self.source(game)
        basic = self.add(game.engine, "Wastes", zone="library", ref="fertilid-decline:known-hit")
        self.prepare_activation(game)
        shuffles, libraries = self.shuffle_counts(game), self.libraries(game)
        self.activate(game, source, "A")
        schema = self.begin_search(game, "A")
        self.assertIn(basic.ref, schema["legal_refs"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        self.choose(game, "A")
        self.assertIsNone(self.resolve(game))
        self.assertEqual("library", game.state.cards[basic.object_id].zone)
        self.assert_shuffled_only(game, shuffles, libraries, "A")

    def test_fertilid_keeps_target_search_private_and_replays_pending_choice(self):
        game = self.session(701_230_273)
        source = self.source(game)
        selected = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-tail:found")
        kept = self.add(game.engine, "Generic Bound Plains", seat="B", zone="library", ref="fertilid-tail:unfound")
        self.prepare_activation(game)
        self.activate(game, source, "B")
        self.begin_search(game, "B")
        private = packet_without_decision_capability(game.packet("pilot:B", full=True))["decision"]
        self.assertIn(selected.ref, json.dumps(private))
        self.assertIn(kept.ref, json.dumps(private))
        game = self.replay(game, load=True)
        resumed = packet_without_decision_capability(game.packet("pilot:B", full=True))["decision"]
        self.assertEqual(private, resumed)
        for seat in "ACD":
            packet = packet_without_decision_capability(game.packet(f"pilot:{seat}", full=True))
            self.assertIsNone(packet["decision"])
            self.assertNotIn(selected.ref, json.dumps(packet))
            self.assertNotIn(kept.ref, json.dumps(packet))

        self.choose(game, "B", [selected.ref])
        self.assertIsNone(self.resolve(game))
        self.assert_tapped_land(game, selected, "B")
        restored = self.replay(game, load=True)
        for seat in "ACD":
            packet = packet_without_decision_capability(restored.packet(f"pilot:{seat}", full=True))
            self.assertIn(restored.state.cards[selected.object_id].ref, json.dumps(packet))
            self.assertNotIn(kept.ref, json.dumps(packet))

    def test_fertilid_respects_restricted_search_and_replays_target_owned_result(self):
        game = self.session(701_230_274)
        source = self.source(game)
        mindcensor = self.add(game.engine, "Aven Mindcensor")
        self.assert_closed(game, mindcensor)
        excluded = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-below-top-four")
        selected = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-in-top-four")
        for index in range(3):
            self.add(game.engine, "Volcanic Island", seat="B", zone="library", ref=f"fertilid-top-dual-{index}")
        self.prepare_activation(game)
        shuffles, libraries = self.shuffle_counts(game), self.libraries(game)
        self.activate(game, source, "B")
        schema = self.begin_search(game, "B")
        self.assertEqual([selected.ref], schema["legal_refs"])
        self.assert_rejected(game, "B", {"action_id": "choose", "search_cards": [excluded.ref]})
        game = self.replay(game, load=True)
        self.choose(game, "B", [selected.ref])
        self.assertIsNone(self.resolve(game))
        self.assert_tapped_land(game, selected, "B")
        self.assertEqual("library", game.state.cards[excluded.object_id].zone)
        self.assert_shuffled_only(game, shuffles, libraries, "B", moved=selected.object_id)
        self.replay(game)

    def test_fertilid_replays_private_target_land_entry_replacement(self):
        game = self.session(701_230_277)
        source = self.source(game)
        orb = self.add(game.engine, "Orb of Dreams", ref="fertilid-entry-orb")
        archelos = self.add(game.engine, "Archelos, Lagoon Mystic", seat="C", ref="fertilid-entry-archelos")
        selected = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-entry:private")
        for replacement_source in (orb, archelos):
            self.assert_closed(game, replacement_source)
        self.prepare_activation(game)
        shuffles, libraries = self.shuffle_counts(game), self.libraries(game)
        self.activate(game, source, "B")
        self.begin_search(game, "B")
        self.choose(game, "B", [selected.ref])
        self.assertEqual(("replacement.order", ["B"]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        self.assertEqual(libraries, self.libraries(game))
        self.assertEqual(shuffles, self.shuffle_counts(game))
        for seat in "ACD":
            packet = packet_without_decision_capability(game.packet(f"pilot:{seat}", full=True))
            self.assertIsNone(packet["decision"])
            self.assertNotIn(selected.ref, json.dumps(packet))

        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        orb_first = next(row["id"] for row in options if orb.ref in row["id"])
        game = self.replay(game, load=True)
        self.assertEqual(options, game.packet("pilot:B", full=True)["decision"]["ctx"]["options"])
        command = {"action_id": "choose", "replacement": orb_first}
        self.assert_rejected(game, "A", command)
        result = game.act("pilot:B", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        land = game.state.cards[selected.object_id]
        self.assertEqual(("B", "B", "battlefield", False), (land.owner, land.controller, land.zone, land.tapped))
        self.assert_shuffled_only(game, shuffles, libraries, "B", moved=selected.object_id)
        self.replay(game, load=True)

    def test_fertilid_rejects_unaffordable_activation_and_foreign_or_invalid_search_choices(self):
        game = self.session(701_230_275)
        source = self.source(game, counters=1)
        valid = self.add(game.engine, "Wastes", seat="B", zone="library", ref="fertilid-valid-search")
        dual = self.add(game.engine, "Volcanic Island", seat="B", zone="library", ref="fertilid-dual-search")
        foreign = self.add(game.engine, "Wastes", zone="library", ref="fertilid-foreign-search")
        self.prepare_activation(game, mana={"C": 2, "G": 1})
        offer = self.activation_offer(game, source)
        self.assert_rejected(game, "A", {
            "action_id": offer["id"], "targets": ["B"], "pay": "manual", "payment": {"C": 1},
        })
        self.assert_rejected(game, "A", {
            "action_id": offer["id"], "targets": ["B"], "pay": "manual", "payment": {"C": 2},
        })
        self.assert_rejected(game, "A", {
            "action_id": offer["id"], "targets": [], "pay": "auto",
        })
        self.assertEqual(1, game.state.cards[source.object_id].counters.get("+1/+1"))
        self.assertEqual(3, sum(game.state.players["A"].mana_pool.values()))
        self.activate(game, source, "B")
        self.begin_search(game, "B")
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        stolen = self.search_decision(game, "B")["cap"]
        for seat, refs, extra in (
            ("A", [valid.ref], {"cap": stolen}),
            ("D", [], {}),
            ("B", [dual.ref], {}),
            ("B", [foreign.ref], {}),
            ("B", [valid.ref, valid.ref], {}),
            ("B", valid.ref, {}),
            ("B", None, {}),
            ("B", [], {"searching_player": "A"}),
        ):
            with self.subTest(seat=seat, refs=refs):
                self.assert_rejected(game, seat, {"action_id": "choose", "search_cards": refs, **extra})

        self.choose(game, "B", [valid.ref])
        self.assertIsNone(self.resolve(game))
        self.assert_tapped_land(game, valid, "B")

    def test_omitting_target_library_shuffle_is_detected(self):
        game = self.session(701_230_276)
        source = self.source(game)
        self.prepare_activation(game)
        shuffles, libraries = self.shuffle_counts(game), self.libraries(game)
        self.activate(game, source, "B")
        self.begin_search(game, "B")
        with patch.object(game.engine, "shuffle_library") as omit:
            with self.assertRaisesRegex(AssertionError, "Only the targeted library is shuffled"):
                self.choose(game, "B")
                self.assert_shuffled_only(game, shuffles, libraries, "B")
        omit.assert_called_once()
