from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.private_opponent_library_cast_model import (
    DAMAGE_LIBRARY_CAST_CAPABILITY, damage_library_cast_selection, has_private_opponent_library_cast_access,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class PrivateDamageLibraryCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "private-damage-library-cast.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "private-damage-library-cast-cards",
            "attached-cast-restriction-cards", "activation-restriction-material-cards",
            "damage-death-exile-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private combat-damage library cast", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def top(self, game, name, index, seat="B"):
        card = self.add(game.engine, name, seat=seat, zone="library", ref=f"thief-window-{seat}-{index}")
        card.known_to = []
        card.revealed_to = []
        return card

    def pending(self, game, source, reply=None):
        self.assert_closed(game, source)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        self.until(game, lambda session: bool(self.trigger_count(session, source)))
        if reply is not None:
            self.cast(game, "B", reply, [source.ref])
            self.resolve_top(game)
        return self.resolve(game)

    def rejected(self, game, principal, refs):
        before = authoritative_state_hash(game.state)
        result = game.act(principal, {"action_id": "choose", "cards": refs})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_combat_private_choice_owner_order_and_paid_foreign_card_replay(self):
        game = self.session(40637011)
        source = self.add(game.engine, "Thief of Sanity")
        unrelated = self.top(game, "Forest", 0, seat="C")
        first = self.top(game, "Control Magic", 1)
        selected = self.top(game, "Shock", 2)
        last = self.top(game, "Disenchant", 3)
        reply = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Wastes")
        choice = self.pending(game, source, reply=reply)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        context = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertEqual([last.ref, selected.ref, first.ref], [row["id"] for row in context["objects"]])
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            rendered = json.dumps(game.packet(principal, full=True))
            self.assertTrue(all(card.ref not in rendered for card in (first, selected, last, unrelated)))
        self.rejected(game, "pilot:B", [selected.ref])
        self.rejected(game, "pilot:A", [unrelated.ref])
        self.rejected(game, "pilot:A", [])
        game = self.replay(game, load=True)

        choose = game.act("pilot:A", {"action_id": "choose", "cards": [selected.ref]})
        self.assertTrue(choose.ok, choose.summary)
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["B"]), (pending.kind, pending.actors))
        view = game.packet("pilot:B", full=True)
        self.assertEqual({first.ref, last.ref}, {row["id"] for row in view["decision"]["ctx"]["objects"]})
        self.assertNotIn(selected.ref, json.dumps(view))
        self.rejected(game, "pilot:A", [last.ref, first.ref])
        self.rejected(game, "pilot:B", [selected.ref, first.ref])
        game = self.replay(game, load=True)
        choose = game.act("pilot:B", {"action_id": "choose", "cards": [last.ref, first.ref]})
        self.assertTrue(choose.ok, choose.summary)

        exiled = game.state.cards[selected.object_id]
        self.assertEqual(("exile", True, ["A"], []), (exiled.zone, exiled.face_down, exiled.known_to, exiled.revealed_to))
        self.assertEqual([last.object_id, first.object_id], game.state.players["B"].zones["graveyard"][-2:])
        self.assertEqual("library", game.state.cards[unrelated.object_id].zone)
        for seat in "BCD":
            view = game.packet(f"pilot:{seat}", full=True)
            hidden, = (row for row in view["state"]["players"]["B"]["ex"] if row["id"] == selected.ref)
            self.assertEqual({"id":selected.ref, "n":"?", "fd":1}, hidden)
            self.assertNotIn(selected.printed_name, json.dumps(view))
            self.assertFalse(has_private_opponent_library_cast_access(game.engine, actor=seat, card=exiled))
        self.assertTrue(has_private_opponent_library_cast_access(game.engine, actor="A", card=exiled))
        self.cast(game, "A", selected, ["C"])
        item, = [item for item in game.state.stack if item.card_object_id == selected.object_id]
        self.assertEqual(("A", ()), (item.controller, tuple(item.mana_colors_spent)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["C"].life)
        self.assertEqual("graveyard", game.state.cards[selected.object_id].zone)
        self.assertFalse(has_private_opponent_library_cast_access(game.engine, actor="A", card=game.state.cards[selected.object_id]))
        self.replay(game, load=True)

    def test_actual_short_library_can_select_a_land_and_grants_no_land_play(self):
        game = self.session(40637012)
        source = self.add(game.engine, "Thief of Sanity")
        for oid in tuple(game.state.players["B"].zones["library"]):
            game.engine.move_card(oid, "graveyard", log=False, semantic_events=False)
        land = self.top(game, "Mountain", 1)
        choice = self.pending(game, source)
        self.assertEqual("semantic.choice", choice.kind)
        result = game.act("pilot:A", {"action_id": "choose", "cards": [land.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("exile", True), (game.state.cards[land.object_id].zone, game.state.cards[land.object_id].face_down))
        self.until(game, lambda session: session.state.phase == "postcombat_main" and session.state.priority_player == "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("play-land:" + land.ref, {row["id"] for row in legal})
        self.replay(game, load=True)

    def test_damage_source_and_private_graveyard_partition_require_the_complete_original(self):
        original = self.db.lookup("Thief of Sanity", fuzzy=False)
        compiled = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        trigger, = (node for face in compiled.faces for node in face.nodes if node.event == "damage.dealt.self")
        self.assertIn(DAMAGE_LIBRARY_CAST_CAPABILITY, trigger.capability_dependencies)
        self.assertEqual((damage_library_cast_selection(),), tuple(trigger.effects))
        self.assertIsNone(trigger.target_schema)
        for old, new in (("top three", "top four"), ("combat damage", "damage"),
                         ("their graveyard", "the bottom of their library"), ("may cast", "may play"),
                         ("mana of any type", "mana of any color")):
            changed = replace(original, oracle_text=original.oracle_text.replace(old, new, 1))
            self.assertNotEqual("exact", compile_oracle_card(changed, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_damage_library_cast_binding_is_killed(self):
        with patch("quorune.compiler.private_opponent_library_cast_templates.private_damage_library_cast_result", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_damage_source_and_private_graveyard_partition_require_the_complete_original()
