from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.artifact_sacrifice_return import ARTIFACT_SACRIFICE_RETURN_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Goblin Welder"
PROFILE = "commander_review"


class GoblinWelderZoneTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "goblin-welder-zones.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/goblin-welder-zones-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Simultaneous artifact moves", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_native_same_player_sacrifice_and_entry_replay_and_stale_pair_rollback(self):
        for index, stale in enumerate((None, "battlefield", "graveyard")):
            with self.subTest(stale=stale):
                game = self.session(70117100 + index)
                host = game.engine
                welder = self.add(host, CARD_NAME)
                welder.acquired_control_turn_count = -1
                self.assert_closed(game, welder)
                borrowed = self.add(host, "Sol Ring", seat="D", zone="hand", ref="welder-borrowed")
                host.move_card(borrowed.object_id, "battlefield", controller="B", log=False)
                unrelated = self.add(host, "Sol Ring", seat="C", ref="welder-other-player")
                returned = self.add(host, "Ichor Wellspring", seat="B", zone="graveyard", ref="welder-yard")
                if stale == "battlefield":
                    response = self.add(host, "Disenchant", seat="D", zone="hand", ref="welder-remove-battlefield")
                elif stale == "graveyard":
                    response = self.add(host, "Tormod's Crypt", seat="D", ref="welder-remove-graveyard")
                self.prepare(game)
                game.state.players["D"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(game)
                matrix = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                offer, = [row for row in matrix if row["id"].startswith(f"activate:{welder.ref}:")]
                command = {"action_id": offer["id"], "targets": [
                    {"group": "battlefield_artifact", "ref": borrowed.ref},
                    {"group": "graveyard_artifact", "ref": returned.ref},
                ]}
                wrong = [{"group": "battlefield_artifact", "ref": unrelated.ref}, command["targets"][1]]
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:A", {**command, "targets": wrong}).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                self.assertTrue(game.state.cards[welder.object_id].tapped)
                program = game.engine.semantics.get(game.state.stack[-1].semantic_key)
                self.assertEqual(1, program.effects[0]["schema_version"])
                self.assertIn(ARTIFACT_SACRIFICE_RETURN_CAPABILITY, program.capability_dependencies)
                if stale == "battlefield":
                    self.cast_for(game, "D", response, [borrowed.ref])
                    self.resolve_top(game)
                elif stale == "graveyard":
                    self.priority(game, "D")
                    matrix = game.packet("pilot:D", full=True)["decision"]["ctx"]["legal"]["actions"]
                    crypt, = [row for row in matrix if row["id"].startswith(f"activate:{response.ref}:")]
                    exiled = game.act("pilot:D", {"action_id": crypt["id"], "targets": ["B"]})
                    self.assertTrue(exiled.ok, exiled.summary)
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                b_hand = len(game.state.players["B"].zones["hand"])
                with patch.object(game.engine, "_move_cards_simultaneously",
                                  wraps=game.engine._move_cards_simultaneously) as moves:
                    self.resolve_top(game)
                first = game.state.cards[borrowed.object_id]
                second = game.state.cards[returned.object_id]
                self.assertEqual("battlefield", game.state.cards[unrelated.object_id].zone)
                if stale is None:
                    self.assertEqual(("graveyard", "D", "battlefield", "B"),
                                     (first.zone, first.owner, second.zone, second.controller))
                    moves.assert_called_once()
                    args, kwargs = moves.call_args
                    self.assertEqual([(first.object_id, "graveyard"), (second.object_id, "battlefield")], args[0])
                    self.assertEqual("sacrifice", kwargs["transition_kinds"][first.object_id].value)
                    self.assertEqual({second.object_id: "B"}, kwargs["putting_players"])
                    self.assertEqual("B", game.state.stack[-1].controller)
                    self.resolve_top(game)
                    self.assertEqual(b_hand + 1, len(game.state.players["B"].zones["hand"]))
                else:
                    moves.assert_not_called()
                    expected = ("graveyard", "graveyard") if stale == "battlefield" else ("battlefield", "exile")
                    self.assertEqual(expected, (first.zone, second.zone))
                    self.assertEqual(b_hand, len(game.state.players["B"].zones["hand"]))
                self.replay(game, load=True)

    def test_paid_artifact_pair_requires_exact_shape_and_separate_capability(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        self.assertEqual("exact", compile_oracle_card(original, **options).status)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = (current for current in semantics.programs_for_oracle(original.oracle_id)
                    if current.event == "activate")
        shape = {key: value for key, value in program.target_schema.items() if key != "same_player_groups"}
        result = bind_semantic_program_runtime(replace(program, target_schema=shape),
            capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        deps = tuple(owner for owner in program.capability_dependencies if owner != ARTIFACT_SACRIFICE_RETURN_CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + ARTIFACT_SACRIFICE_RETURN_CAPABILITY,
                      result["blockers"])
        with patch("quorune.compiler.activation_paid_sacrifice_results.artifact_sacrifice_return_activation",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, **options).status)
