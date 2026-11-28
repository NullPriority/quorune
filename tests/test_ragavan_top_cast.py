from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.combat_damage_library_exile import COMBAT_DAMAGE_CAST_EXILE_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Ragavan, Nimble Pilferer"
PROFILE = "commander_review"


class RagavanTopCastTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ragavan.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/ragavan-top-cast-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Combat damage cast access", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_native_dash_combat_treasure_and_exact_current_turn_cast_replay(self):
        for index, card_name in enumerate(("Lightning Bolt", "Plains")):
            with self.subTest(top=card_name):
                game = self.session(7028200 + index)
                host = game.engine
                for object_id in tuple(game.state.players["A"].zones["hand"])[5:]:
                    host.move_card(object_id, "library", log=False)
                source = self.add(host, CARD_NAME, zone="hand")
                self.assert_closed(game, source)
                top = self.add(host, card_name, seat="B", zone="library", ref="ragavan-hidden-top")
                top.known_to.clear()
                top.revealed_to.clear()
                top_identity = top.logical_object_id
                untouched = self.add(host, "Plains", seat="C", zone="library", ref="ragavan-other-top")
                unrelated = self.add(host, "Plains", seat="D", zone="exile", ref="ragavan-unrelated")
                for land_index in range(2):
                    self.add(host, "Mountain", ref=f"ragavan-dash-mountain-{land_index}")
                self.prepare(game)
                self.checkpoint(game)
                for seat in "ACD":
                    self.assertNotIn(top.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                offer = self.action(game, "A", source)
                self.assertIn("dash", {option["id"] for option in offer["cost_options"]})
                dash = game.act("pilot:A", {"action_id": offer["id"], "cost_option": "dash", "pay": "auto"})
                self.assertTrue(dash.ok, dash.summary)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                attacker = game.state.cards[source.object_id]
                self.attack(game, [(attacker, "B")])
                self.until(game, lambda current: self.trigger_count(current, attacker) == 1)
                self.assertEqual(38, game.state.players["B"].life)
                self.assertEqual("B", game.state.stack[-1].context["target"])
                self.assertEqual([], game.state.stack[-1].targets)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                top = game.state.cards[top.object_id]
                treasury, = [card for card in game.state.cards.values()
                             if card.zone == "battlefield" and card.controller == "A"
                             and card.printed_name in {"Treasure", "Treasure Token"}]
                self.assertEqual(("exile", "B", False), (top.zone, top.owner, top.face_down))
                self.assertNotEqual(top_identity, top.logical_object_id)
                self.assertEqual("library", game.state.cards[untouched.object_id].zone)
                self.assertNotIn("temporary_play_permission", game.state.cards[unrelated.object_id].annotations)
                grant = top.annotations["temporary_play_permission"]
                self.assertEqual(("A", False, True, False, game.state.turn_sequence),
                                 (grant["player"], grant["allow_land"], grant["allow_spell"],
                                  grant["without_mana_cost"], grant["turn_sequence"]))
                self.until(game, lambda current: current.state.phase == "postcombat_main"
                           and current.state.priority_player == "A")
                actions = {row["id"]: row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
                self.assertNotIn("play-land:" + top.ref, actions)
                cast_id = "cast:" + top.ref
                if card_name == "Lightning Bolt":
                    options = actions[cast_id]["cost_options"]
                    self.assertTrue(all(option["requirements"]["R"] == 1 for option in options))
                    before = authoritative_state_hash(game.state)
                    rejected = game.act("pilot:B", {"action_id": cast_id, "targets": ["C"], "pay": "auto"})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    paid = game.act("pilot:A", {"action_id": cast_id, "targets": ["C"], "pay": "auto"})
                    self.assertTrue(paid.ok, paid.summary)
                    self.assertNotEqual("battlefield", game.state.cards[treasury.object_id].zone)
                    self.resolve_top(game)
                    self.assertEqual(37, game.state.players["C"].life)
                else:
                    self.assertNotIn(cast_id, actions)

                turn = game.state.turn_sequence
                self.until(game, lambda current: current.state.turn_sequence > turn)
                self.assertEqual("hand", game.state.cards[source.object_id].zone)
                self.assertNotIn("temporary_play_permission", game.state.cards[top.object_id].annotations)
                self.replay(game, load=True)

    def test_damage_recipient_and_payment_descriptor_remain_closed(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(original, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = tuple(item for item in semantics.programs_for_oracle(original.oracle_id)
                         if COMBAT_DAMAGE_CAST_EXILE_CAPABILITY in item.capability_dependencies)
        for altered in (
            replace(program, effects=tuple(reversed(program.effects))),
            replace(program, effects=(program.effects[0], {**program.effects[1], "count": 2})),
            replace(program, event_condition=None),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        remaining = tuple(owner for owner in program.capability_dependencies if owner != COMBAT_DAMAGE_CAST_EXILE_CAPABILITY)
        stripped = replace(program, capability_dependencies=remaining,
                           capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + COMBAT_DAMAGE_CAST_EXILE_CAPABILITY, result["blockers"])
        with patch("quorune.combat_damage_library_exile.combat_damage_cast_exile_effect", return_value={}):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
