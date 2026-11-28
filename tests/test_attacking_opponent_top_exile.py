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
from quorune.opponent_library_exile import INCARNATION_PLAY_FIELD, current_opponent_library_exile_play_permission
from quorune.opponent_library_exile_model import ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class AttackingOpponentTopExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db = Path(cls.temporary.name) / "attacking-opponent-top-exile.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "attacking-opponent-top-exile-cards",
            "attached-cast-restriction-cards", "activation-restriction-material-cards",
            "event-player-modifier-cards", "battlefield-target-reduction-cards",
        )
        build_fixture_database([ROOT / f"tests/fixtures/{name}.json" for name in fixtures], db)
        cls.db = CardDatabase(db)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attacking opponent top exile", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def top(self, game, name, reference):
        top = self.add(game.engine, name, seat="A", zone="library", ref=reference)
        top.known_to = []
        top.revealed_to = []
        return top

    def scene(self, seed, number):
        game = self.session(seed)
        source = self.add(game.engine, "Cunning Rhetoric", seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        attackers = [self.add(game.engine, "Grizzly Bears", ref=f"rhetoric-attacker-{index}") for index in range(number)]
        for card in (source, walker, *attackers):
            self.assert_closed(game, card)
        return game, source, walker, attackers

    def test_actual_mixed_recipients_exile_one_changed_top_publicly_with_paid_color_and_replay(self):
        game, source, walker, attackers = self.scene(50853201, 4)
        hidden = self.top(game, "Control Magic", "rhetoric-hidden")
        after_draw = self.top(game, "Shock", "rhetoric-actual")
        before_draw = self.top(game, "Disenchant", "rhetoric-initial")
        draw = self.add(game.engine, "Think Twice", zone="hand")
        for index, name in enumerate(("Island", "Island", "Plains", "Plains")):
            self.add(game.engine, name, ref=f"rhetoric-mana-a-{index}")
        self.add(game.engine, "Island", seat="B", ref="rhetoric-mana-b")
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, list(zip(attackers, ("B", walker.ref, walker.ref, "C"), strict=True)))
        self.assertEqual(1, self.trigger_count(game, source))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(before_draw.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        self.cast(game, "A", draw)
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[before_draw.object_id].zone)
        self.cast(game, "A", before_draw, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))

        exiled = game.state.cards[after_draw.object_id]
        self.assertEqual(("exile", "A", False), (exiled.zone, exiled.owner, exiled.face_down))
        self.assertEqual(("B", exiled.logical_object_id), tuple(exiled.annotations[INCARNATION_PLAY_FIELD]))
        self.assertEqual("library", game.state.cards[hidden.object_id].zone)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D", "spectator"):
            view = game.packet(principal, full=True)
            visible, = (row for row in view["state"]["players"]["A"]["ex"] if row["id"] == exiled.ref)
            self.assertEqual("Shock", visible["n"])
            self.assertNotIn(hidden.ref, json.dumps(view))
        for seat in "ACD":
            self.assertIsNone(current_opponent_library_exile_play_permission(game.state, seat, exiled))

        self.priority(game, "B")
        offer, = [action for action in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if action["id"] == "cast:" + exiled.ref]
        outcome = game.act("pilot:B", {"action_id": offer["id"], "pay": "auto", "targets": ["A"]})
        self.assertTrue(outcome.ok, outcome.summary)
        item, = [item for item in game.state.stack if item.card_object_id == exiled.object_id]
        self.assertEqual("B", item.controller)
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual("graveyard", game.state.cards[exiled.object_id].zone)
        self.assertIsNone(current_opponent_library_exile_play_permission(game.state, "B", game.state.cards[exiled.object_id]))
        self.replay(game, load=True)

    def test_walker_only_is_one_trigger_other_defenders_are_ignored_and_empty_library_is_safe(self):
        for index, mode in enumerate(("walker", "other", "empty"), 2):
            with self.subTest(mode=mode):
                game, source, walker, attackers = self.scene(50853200 + index, 2)
                land = None
                if mode == "empty":
                    for oid in tuple(game.state.players["A"].zones["library"]):
                        game.engine.move_card(oid, "graveyard", log=False, semantic_events=False)
                else:
                    land = self.top(game, "Mountain", "rhetoric-mountain")
                self.prepare(game)
                self.checkpoint(game)
                defender = "D" if mode == "other" else walker.ref
                self.attack(game, [(card, defender) for card in attackers])
                self.assertEqual(int(mode != "other"), self.trigger_count(game, source))
                self.assertIsNone(self.resolve(game))
                if mode == "walker":
                    exiled = game.state.cards[land.object_id]
                    self.assertEqual("exile", exiled.zone)
                    self.assertTrue(current_opponent_library_exile_play_permission(game.state, "B", exiled)["allow_land"])
                    self.priority(game, "B")
                    legal = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    self.assertFalse(any(row["id"] == "play-land:" + land.ref for row in legal))
                elif mode == "other":
                    self.assertEqual("library", game.state.cards[land.object_id].zone)
                else:
                    self.assertEqual([], game.state.players["A"].zones["exile"])
                self.replay(game, load=True)

    def test_original_requires_grouped_player_or_planeswalker_and_the_original_any_color_play(self):
        original = self.db.lookup("Cunning Rhetoric", fuzzy=False)
        result = compile_oracle_card(original, capability_registry=self.registry, capability_profile="commander_review")
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertTrue(any(ATTACKING_OPPONENT_TOP_EXILE_CAPABILITY in node.capability_dependencies
                            for face in result.faces for node in face.nodes))
        for before, after in (("and/or", "and"), ("planeswalkers", "battles"), ("that player's", "your"),
                              ("any color", "any type"), ("for as long as it remains exiled", "until end of turn")):
            variant = replace(original, oracle_text=original.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(variant, capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_original_grouped_attack_binding_is_killed(self):
        with patch("quorune.compiler.fixed_public_action_event_bindings._player_or_walker_attacked_spec", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_original_requires_grouped_player_or_planeswalker_and_the_original_any_color_play()
