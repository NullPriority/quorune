from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class LandEntryPuttingPlayerTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "land-entry-putting-player.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/land-entry-putting-player-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition(
            "Land entry player", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander],
        )

    def play_land(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["action"] == "play_land" and row.get("card") == card.ref]
        result = game.act(f"pilot:{seat}", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)

    def entry_context(self, trace, card):
        context, = [
            call.args[1] for call in trace.call_args_list
            if call.args[0] == "land.enter" and call.args[1].get("card") == card.ref
        ]
        return context

    def test_actual_swamp_play_records_its_actor_and_legacy_replay_keeps_the_old_event(self):
        for version in (1, None):
            with self.subTest(version=version):
                game = self.session(6020020 + (version is None))
                game.state.battlefield_entry_player_version = version
                swamp = self.add(game.engine, "Swamp", seat="B", zone="hand")
                self.prepare(game, actor="B", active="B")
                self.checkpoint(game)
                with patch.object(game.engine, "_dispatch_semantic_event",
                                  wraps=game.engine._dispatch_semantic_event) as trace:
                    self.play_land(game, "B", swamp)
                context = self.entry_context(trace, swamp)
                self.assertEqual(("B", "B"), (context["owner"], context["controller"]))
                if version is None:
                    self.assertNotIn("putting_player", context)
                else:
                    self.assertEqual("B", context["putting_player"])
                resumed = self.replay(game, load=True)
                self.assertEqual(version, resumed.state.battlefield_entry_player_version)

    def test_actual_flicker_of_fate_binds_the_caster_when_the_land_returns_to_its_owner(self):
        game = self.session(6020022)
        urborg = self.add(game.engine, "Urborg, Tomb of Yawgmoth")
        arbor = self.add(game.engine, "Dryad Arbor", seat="B")
        flicker = self.add(game.engine, "Flicker of Fate", zone="hand")
        for card in (urborg, arbor, flicker):
            self.assert_closed(game, card)
        previous_identity = arbor.logical_object_id
        self.prepare(game, mana={"W": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", flicker, [arbor.ref])
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            self.resolve_top(game)
        arrived = game.state.cards[arbor.object_id]
        self.assertEqual(("battlefield", "B"), (arrived.zone, arrived.controller))
        self.assertNotEqual(previous_identity, arrived.logical_object_id)
        context = self.entry_context(trace, arrived)
        self.assertEqual(("A", "B", "B"), (context["putting_player"], context["controller"], context["owner"]))
        self.assertIn("swamp", context["subtypes"])
        self.assertEqual(arrived.logical_object_id, context["card_object_identity"])
        self.replay(game, load=True)

    def test_actual_library_search_binds_the_player_who_puts_the_chosen_land(self):
        game = self.session(6020023)
        growth = self.add(game.engine, "Rampant Growth", seat="C", zone="hand")
        swamp = self.add(game.engine, "Swamp", seat="C", zone="library", ref="searched-swamp")
        self.assert_closed(game, growth)
        self.prepare(game, actor="C", active="C", mana={"G": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "C", growth)
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["C"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            result = game.act("pilot:C", {"action_id": "choose", "search_cards": [swamp.ref]})
        self.assertTrue(result.ok, result.summary)
        context = self.entry_context(trace, swamp)
        self.assertEqual(("C", "C", True), (context["putting_player"], context["controller"], context["tapped"]))
        self.replay(game, load=True)

    def test_actual_burgeoning_places_a_land_for_its_controller_during_an_opponents_turn(self):
        game = self.session(6020024)
        burgeoning = self.add(game.engine, "Burgeoning", seat="B")
        played = self.add(game.engine, "Swamp", zone="hand", ref="played-swamp")
        placed = self.add(game.engine, "Swamp", seat="B", zone="hand", ref="placed-swamp")
        self.assert_closed(game, burgeoning)
        self.prepare(game)
        self.checkpoint(game)
        self.play_land(game, "A", played)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            result = game.act("pilot:B", {"action_id": "choose", "card": placed.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("A", game.state.active_player)
        context = self.entry_context(trace, placed)
        self.assertEqual(("B", "B"), (context["putting_player"], context["controller"]))
        self.replay(game, load=True)

    def test_actual_reclamation_records_the_player_for_each_simultaneous_land(self):
        game = self.session(6020025)
        spell = self.add(game.engine, "Splendid Reclamation", zone="hand")
        lands = [
            self.add(game.engine, "Swamp", zone="graveyard", ref=f"reclaimed-swamp-{i}")
            for i in range(2)
        ]
        other = self.add(game.engine, "Swamp", seat="B", zone="graveyard", ref="other-graveyard-swamp")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"G": 1, "C": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            self.resolve_top(game)
        for land in lands:
            context = self.entry_context(trace, land)
            self.assertEqual(("A", "A", True), (context["putting_player"], context["controller"], context["tapped"]))
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_actual_awaken_the_woods_records_the_land_token_creator(self):
        game = self.session(6020026)
        urborg = self.add(game.engine, "Urborg, Tomb of Yawgmoth")
        awaken = self.add(game.engine, "Awaken the Woods", seat="B", zone="hand")
        for card in (urborg, awaken):
            self.assert_closed(game, card)
        self.prepare(game, actor="B", active="B", mana={"G": 2, "C": 2})
        self.checkpoint(game)
        self.priority(game, "B")
        offer = self.action(game, "B", awaken)
        result = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto", "x": 2})
        self.assertTrue(result.ok, result.summary)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            self.resolve_top(game)
        events = [
            call.args[1] for call in trace.call_args_list
            if call.args[0] == "land.enter" and call.args[1]["token"]
        ]
        self.assertEqual(2, len(events))
        self.assertEqual(2, len({event["card_object_identity"] for event in events}))
        for event in events:
            self.assertEqual(("B", "B"), (event["putting_player"], event["controller"]))
            self.assertIn("swamp", event["subtypes"])
        self.replay(game, load=True)

    def test_actual_creature_spell_records_its_resolving_controller_as_the_putting_player(self):
        game = self.session(6020027)
        bears = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand")
        self.assert_closed(game, bears)
        self.prepare(game, actor="D", active="D", mana={"G": 1, "C": 1})
        self.checkpoint(game)
        self.cast_for(game, "D", bears)
        spell_identity = game.state.cards[bears.object_id].logical_object_id
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            self.resolve_top(game)
        entries = [
            call.args[1] for call in trace.call_args_list
            if call.args[0] == "permanent.enter" and call.args[1].get("card") == bears.ref
        ]
        entry, = entries
        self.assertEqual(("D", "D"), (entry["putting_player"], entry["controller"]))
        self.assertEqual(spell_identity, entry["card_object_identity"])
        self.replay(game, load=True)

    def test_actual_pilgrimage_pins_the_actor_of_the_one_land_placed_from_the_search(self):
        game = self.session(6020028)
        spell = self.add(game.engine, "Nissa's Pilgrimage", seat="C", zone="hand")
        first = self.add(game.engine, "Forest", seat="C", zone="library", ref="first-forest")
        second = self.add(game.engine, "Forest", seat="C", zone="library", ref="second-forest")
        self.assert_closed(game, spell)
        self.prepare(game, actor="C", active="C", mana={"G": 1, "C": 2})
        self.checkpoint(game)
        self.cast_for(game, "C", spell)
        pending = self.resolve(game)
        self.assertEqual(("semantic.search", ["C"]), (pending.kind, pending.actors))
        game = self.replay(game, load=True)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            result = game.act("pilot:C", {"action_id": "choose", "search_cards": [first.ref, second.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", game.state.cards[second.object_id].zone)
        context = self.entry_context(trace, first)
        self.assertEqual(("C", "C", True), (context.get("putting_player"), context["controller"], context["tapped"]))
        self.replay(game, load=True)

    def test_actual_rejuvenator_pins_the_actor_for_a_land_selected_from_the_top_of_the_library(self):
        game = self.session(6020029)
        source = self.add(game.engine, "Elvish Rejuvenator", seat="D", zone="hand")
        swamp = self.add(game.engine, "Swamp", seat="D", zone="library", ref="top-swamp")
        self.assert_closed(game, source)
        self.prepare(game, actor="D", active="D", mana={"G": 1, "C": 2})
        self.checkpoint(game)
        self.cast_for(game, "D", source)
        pending = self.resolve(game)
        self.assertEqual(["D"], pending.actors)
        game = self.replay(game, load=True)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            result = game.act("pilot:D", {"action_id": "choose", "cards": [swamp.ref]})
        self.assertTrue(result.ok, result.summary)
        context = self.entry_context(trace, swamp)
        self.assertEqual(("D", "D", True), (context.get("putting_player"), context["controller"], context["tapped"]))
        self.replay(game, load=True)

    def test_actual_drownyard_temple_retains_its_activating_player_when_it_reenters(self):
        game = self.session(6020036)
        temple = self.add(game.engine, "Drownyard Temple", seat="C", zone="graveyard")
        urborg = self.add(game.engine, "Urborg, Tomb of Yawgmoth", seat="D")
        for card in (temple, urborg):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"C": 3})
        self.checkpoint(game)
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer, = [row for row in actions if row["id"].startswith(f"activate:{temple.ref}:")]
        result = game.act("pilot:C", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            self.resolve_top(game)
        arrived = game.state.cards[temple.object_id]
        context = self.entry_context(trace, arrived)
        self.assertEqual(("C", "C", True), (context.get("putting_player"), context["controller"], context["tapped"]))
        self.assertIn("swamp", context["subtypes"])
        self.replay(game, load=True)

    def test_actual_yore_tiller_retains_the_player_who_returns_a_land_attacking(self):
        game = self.session(6020037)
        source = self.add(game.engine, "Yore-Tiller Nephilim")
        source.acquired_control_turn_count = -1
        arbor = self.add(game.engine, "Dryad Arbor", zone="graveyard")
        for card in (source, arbor):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.until(game, lambda state: state.state.pending_decision.kind == "combat.attackers")
        result = game.act("pilot:A", {"action_id": "attack", "attackers": {source.ref: "B"}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [arbor.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        with patch.object(game.engine, "_dispatch_semantic_event",
                          wraps=game.engine._dispatch_semantic_event) as trace:
            result = game.act("pilot:A", {"action_id": "choose", "choice": "B"})
        self.assertTrue(result.ok, result.summary)
        arrived = game.state.cards[arbor.object_id]
        self.assertEqual("B", game.state.combat.attackers[arrived.object_id])
        context = self.entry_context(trace, arrived)
        self.assertEqual(("A", "A", True), (context.get("putting_player"), context["controller"], context["tapped"]))
        self.replay(game, load=True)
