"""Original Mosswort Bridge through native private choices and exact replay."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.hideaway_model import HIDEAWAY_PLAY_OPERATION, HIDEAWAY_SELECTION_VERSION, hideaway_selection_effect
from quorune.library_battlefield_model import RANDOM_REVEALED_CREATURE_ENTRY_VERSION, random_revealed_creature_entry_effect
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.land_play_permissions import land_play_permission_options
from quorune.rules.library_selection_capability_shapes import fixed_library_selection_node_capabilities
from quorune.zone_object_state import face_down_exile_viewers
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database


NAME = "Mosswort Bridge"


class HideawayLinkedPlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "hideaway.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/chosen-type-event-cards.json",
            ROOT / "tests/fixtures/equipped-color-upkeep-counter-cards.json",
            ROOT / "tests/fixtures/fixed-qualified-stack-copy-cards.json",
            ROOT / "tests/fixtures/next-personal-turn-protection-cards.json",
            ROOT / "tests/fixtures/resolution-mana-choice-cards.json",
            ROOT / "tests/fixtures/hideaway-linked-play-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Native Hideaway", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def command(self, game, actor, command):
        result = game.act(f"pilot:{actor}", command)
        self.assertTrue(result.ok, result.summary)

    def rejected(self, game, actor, command):
        previous = authoritative_state_hash(game.state)
        self.assertFalse(game.act(f"pilot:{actor}", command).ok)
        self.assertEqual(previous, authoritative_state_hash(game.state))

    def actions(self, game, actor):
        return game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]["legal"]["actions"]

    def exile_row(self, game, actor, card):
        packet = game.packet(f"pilot:{actor}", full=True)
        rows = packet["state"]["players"][card.owner]["ex"]
        return next(row for row in rows if row["id"] == card.ref)

    def assert_hidden_exile(self, game, actor, card):
        row = self.exile_row(game, actor, card)
        self.assertEqual("?", row["n"])
        self.assertNotIn("cid", row)

    def turn(self, game, actor):
        original = game.state.players[actor].turns_begun
        commands = {
            "priority": {"action_id": "pass"},
            "combat.attackers": {"action_id": "attack", "attackers": {}},
            "combat.blockers": {"action_id": "block", "blocks": {}},
        }
        for _ in range(240):
            if (game.state.active_player == actor and game.state.priority_player == actor
                    and game.state.phase == "precombat_main" and game.state.players[actor].turns_begun > original):
                return
            pending = game.state.pending_decision
            self.assertIn(pending.kind, commands)
            result = game.act(game.pending_principals()[0], commands[pending.kind])
            self.assertTrue(result.ok, result.summary)
        self.fail("The next ordinary main phase did not arrive")

    def setup(self, seed, *, selected_name="Grizzly Bears", power=10):
        game = self.session(seed)
        transitions = ZoneTransitionOwner(game.engine)
        for player in game.state.players.values():
            for object_id in player.zones["hand"][:2]:
                transitions.move_card(object_id, "library", reason="Seed fixture library", semantic_events=False)
        bridge = self.add(game.engine, NAME, zone="hand")
        self.add(game.engine, "Forest", ref="hideaway-green-mana")
        creature = self.add(game.engine, "Grizzly Bears")
        creature.counters["+1/+1"] = power - 2
        selected = self.add(game.engine, selected_name, zone="library", ref="hideaway-selected")
        for name in ("Plains", "Island", "Wastes"):
            self.add(game.engine, name, zone="library", ref="hideaway-top-" + name.casefold())
        return game, bridge, selected, creature

    def enter_and_choose(self, game, bridge, selected, *, intervene=None):
        self.prepare(game)
        self.checkpoint(game)
        play, = [row for row in self.actions(game, "A") if row.get("kind") == "play_land" and row.get("card") == bridge.ref]
        self.command(game, "A", {"action_id": play["id"]})
        self.assertTrue(game.state.cards[bridge.object_id].tapped)
        if intervene is not None:
            intervene()
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual("fixed_library_selection", context["operation"])
        self.assertEqual(4, len(context["objects"]))
        remainder_refs = {row["id"] for row in context["objects"]} - {selected.ref}
        foreign = json.dumps(game.packet("pilot:C", full=True))
        self.assertNotIn(selected.ref, foreign)
        self.rejected(game, "C", {"action_id": "choose", "cards": [selected.ref]})
        self.rejected(game, "A", {"action_id": "choose", "cards": []})
        game = self.replay(game, load=True)
        self.command(game, "A", {"action_id": "choose", "cards": [selected.ref]})
        self.assertIsNone(self.resolve(game))
        exiled = game.state.cards[selected.object_id]
        self.assertTrue(exiled.face_down)
        self.assertEqual("exile", exiled.zone)
        self.assertEqual({"A"}, face_down_exile_viewers(game.state, exiled))
        remainder = game.state.players["A"].zones["library"][:3]
        self.assertEqual(remainder_refs, {game.state.cards[object_id].ref for object_id in remainder})
        self.assert_hidden_exile(game, "C", selected)
        return game

    def activate(self, game, bridge, actor="A"):
        self.priority(game, actor)
        action = f"activate:{bridge.ref}:ab4"
        self.assertIn(action, [row["id"] for row in self.actions(game, actor)])
        self.command(game, actor, {"action_id": action, "pay": "auto"})
        self.assertTrue(game.state.cards[bridge.object_id].tapped)

    def play_link(self, game, selected, *, actor="A", kind=None):
        pending = self.resolve(game)
        self.assertEqual("semantic.choice", pending.kind)
        context = game.packet(f"pilot:{actor}", full=True)["decision"]["ctx"]
        self.assertEqual(HIDEAWAY_PLAY_OPERATION, context["operation"])
        option, = [row for row in context["cast_options"]
                   if row["card"] == selected.ref and (kind is None or row.get("kind") == kind)]
        command = {"action_id": "choose", "choice": "play", "cast": {"cost_option": option["id"]}}
        self.rejected(game, "C", command)
        self.rejected(game, actor, {**command, "cast": {**command["cast"], "card": "outside-the-hideaway-link"}})
        game = self.replay(game, load=True)
        self.command(game, actor, command)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[selected.object_id].zone)
        self.assertFalse(game.state.cards[selected.object_id].face_down)
        self.replay(game, load=True)
        return game

    def test_mosswort_strict_shape_and_unrepresented_neighbors(self):
        record = self.db.lookup(NAME)
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertIn(HIDEAWAY_SELECTION_VERSION, [effect.get("schema_version")
                                                 for node in ir.faces[0].nodes for effect in node.effects])
        self.assertIn(HIDEAWAY_PLAY_OPERATION, [effect["op"] for node in ir.faces[0].nodes for effect in node.effects])
        for before, after in (("Hideaway 4", "Hideaway 5"), ("10 or greater", "9 or greater")):
            modified = replace(record, oracle_text=record.oracle_text.replace(before, after))
            other = compile_oracle_card(modified, capability_registry=self.registry, capability_profile="commander_review")
            self.assertTrue(other.material_residuals)
        from quorune.compiler import hideaway_nodes

        for producer in ("hideaway_keyword_node", "hideaway_play_template"):
            with patch.object(hideaway_nodes, producer, return_value=None):
                other = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
                self.assertTrue(other.material_residuals, producer)
        game = self.session(7027500)
        self.assert_closed(game, self.add(game.engine, NAME))

    def test_mosswort_fixed_selection_keeps_revealed_creature_version_separate(self):
        def dependencies(effect):
            return fixed_library_selection_node_capabilities(
                effects=(effect,), target_schema=None, mechanic_ids=("fixed-library-selection",),
            )

        hideaway = hideaway_selection_effect()
        creature = random_revealed_creature_entry_effect(4)
        self.assertEqual((29, 28), (hideaway["schema_version"], creature["schema_version"]))
        self.assertTrue(dependencies(hideaway))
        self.assertTrue(dependencies(creature))
        self.assertFalse(dependencies({**hideaway, "schema_version": RANDOM_REVEALED_CREATURE_ENTRY_VERSION}))
        self.assertFalse(dependencies({**creature, "schema_version": HIDEAWAY_SELECTION_VERSION}))

    def test_mosswort_private_random_partition_and_free_spell(self):
        game, bridge, selected, _ = self.setup(7027510)
        game = self.enter_and_choose(game, bridge, selected)
        self.turn(game, "A")
        self.activate(game, bridge)
        self.play_link(game, selected)

    def test_mosswort_play_land_uses_current_land_quota(self):
        game, bridge, selected, _ = self.setup(7027520, selected_name="Island")
        game = self.enter_and_choose(game, bridge, selected)
        self.turn(game, "A")
        self.activate(game, bridge)
        game = self.play_link(game, selected, kind="land")
        self.assertFalse(land_play_permission_options(game.engine, "A"))

    def test_mosswort_below_threshold_leaves_exile_private(self):
        game, bridge, selected, _ = self.setup(7027530, power=9)
        game = self.enter_and_choose(game, bridge, selected)
        self.turn(game, "A")
        self.activate(game, bridge)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[selected.object_id].face_down)
        self.replay(game, load=True)

    def test_mosswort_new_controller_can_see_and_cast_foreign_exile(self):
        game, bridge, selected, _ = self.setup(7027540)
        theft = self.add(game.engine, "Confiscate", seat="B", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="hideaway-second-controller-creature")
        creature.counters["+1/+1"] = 8
        self.add(game.engine, "Forest", seat="B", ref="hideaway-second-controller-mana")
        for index in range(6):
            self.add(game.engine, "Island", seat="B", ref=f"hideaway-confiscate-mana-{index}")
        game = self.enter_and_choose(game, bridge, selected)
        self.assert_hidden_exile(game, "B", selected)
        self.turn(game, "B")
        self.cast_for(game, "B", theft, (bridge.ref,))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[bridge.object_id].controller)
        self.assertEqual({"A", "B"}, face_down_exile_viewers(game.state, game.state.cards[selected.object_id]))
        self.assertEqual(selected.printed_name, self.exile_row(game, "B", selected)["n"])
        self.assert_hidden_exile(game, "C", selected)
        game = self.replay(game, load=True)
        self.turn(game, "B")
        self.activate(game, bridge, actor="B")
        game = self.play_link(game, selected, actor="B")
        self.assertEqual(("A", "B"), (
            game.state.cards[selected.object_id].owner, game.state.cards[selected.object_id].controller,
        ))

    def test_mosswort_copied_trigger_keeps_both_exact_linked_results(self):
        game, bridge, first, _ = self.setup(7027550)
        second = self.add(game.engine, "Island", zone="library", ref="hideaway-second-link")
        library = game.state.players["A"].zones["library"]
        library.remove(second.object_id)
        library.insert(len(library) - 4, second.object_id)
        copier = self.add(game.engine, "Strionic Resonator")
        self.add(game.engine, "Wastes", ref="hideaway-copy-mana")
        self.prepare(game)
        self.checkpoint(game)
        land, = [row for row in self.actions(game, "A") if row.get("kind") == "play_land" and row.get("card") == bridge.ref]
        self.command(game, "A", {"action_id": land["id"]})
        self.priority(game, "A")
        trigger = game.state.stack[-1]
        activate, = [row["id"] for row in self.actions(game, "A") if row["id"].startswith(f"activate:{copier.ref}:")]
        self.command(game, "A", {"action_id": activate, "targets": [trigger.ref], "pay": "auto"})
        for selected in (first, second):
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            self.command(game, "A", {"action_id": "choose", "cards": [selected.ref]})
        self.assertIsNone(self.resolve(game))
        self.turn(game, "A")
        self.activate(game, bridge)
        for selected, expected in ((first, {first.ref, second.ref}), (second, {second.ref})):
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            options = game.packet("pilot:A", full=True)["decision"]["ctx"]["cast_options"]
            self.assertEqual(expected, {row["card"] for row in options})
            chosen, = [row for row in options if row["card"] == selected.ref]
            self.command(game, "A", {"action_id": "choose", "choice": "play", "cost_option": chosen["id"]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("battlefield", "battlefield"),
                         tuple(game.state.cards[card.object_id].zone for card in (first, second)))
        self.replay(game, load=True)

    def test_mosswort_phased_source_grants_look_when_control_returns(self):
        game, bridge, selected, _ = self.setup(7027560)
        theft = self.add(game.engine, "Confiscate", seat="B", zone="hand")
        protection = self.add(game.engine, "Teferi's Protection", seat="B", zone="hand")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        for index, name in enumerate(("Island",) * 6 + ("Plains",) * 3):
            self.add(game.engine, name, seat="B", ref=f"hideaway-phase-mana-{index}")

        def intervene():
            self.cast_for(game, "B", theft, (bridge.ref,))
            self.resolve_top(game)
            self.assertEqual("B", game.state.cards[bridge.object_id].controller)
            self.cast_for(game, "B", protection)
            self.resolve_top(game)
            self.assertTrue(game.state.cards[bridge.object_id].phased_out)

        game = self.enter_and_choose(game, bridge, selected, intervene=intervene)
        self.assert_hidden_exile(game, "B", selected)
        self.turn(game, "B")
        self.assertFalse(game.state.cards[bridge.object_id].phased_out)
        self.assertEqual("B", game.state.cards[bridge.object_id].controller)
        self.assertEqual({"A", "B"}, face_down_exile_viewers(game.state, game.state.cards[selected.object_id]))
        self.assertEqual(selected.printed_name, self.exile_row(game, "B", selected)["n"])
        self.assert_hidden_exile(game, "C", selected)
        self.replay(game, load=True)
