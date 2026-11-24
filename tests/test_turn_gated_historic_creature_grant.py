"""Bayek's current turn grant uses the exact historic creature union."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_predicate import ObjectQueryError, ObjectQuerySpec
from quorune.object_query import ObjectQueryResult, object_matches_query
from quorune.object_query_union import RECEIVER_UNION_CAPABILITY, historic_creature_receiver_query
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Bayek of Siwa"
PROFILE = "commander_review"


class TurnGatedHistoricCreatureGrantTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "historic-creature-receiver.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/turn-gated-historic-creature-grant-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current historic creatures", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        card = self.db.lookup(NAME, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, "Bayek of Siwa")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        grant, = [item for item in programs if item.event == "characteristics.evaluate"]
        self.assertTrue(any(item.event == "disguise.action" for item in programs))
        return card, grant

    def test_complete_original_keeps_disguise_source_strike_and_the_closed_turn_union(self):
        card, grant = self.program()
        self.assertIn(RECEIVER_UNION_CAPABILITY, grant.capability_dependencies)
        descriptor, = grant.handlers
        self.assertEqual("controller_turn", descriptor["source_condition"]["kind"])
        target = descriptor["target"]
        self.assertEqual("source_controller", target["target_controller"])
        self.assertTrue(target["exclude_source"])
        self.assertEqual(historic_creature_receiver_query(), ObjectQuerySpec.from_dict(target["predicate"]))
        self.assertEqual(["Double Strike"], descriptor["modifier"]["add_abilities"])
        ir = compile_oracle_card(card, capability_registry=self.registry)
        self.assertTrue(any(node.template_id == "printed-keyword-list-v1" for face in ir.faces for node in face.nodes))
        altered = replace(card, oracle_text=card.oracle_text.replace("other historic creatures", "other historic cards"))
        denied = compile_oracle_card(altered, capability_registry=self.registry)
        self.assertNotEqual("exact", denied.status)

    def test_exact_union_keeps_the_saga_branch_and_refuses_arbitrary_legendary_sets(self):
        query = historic_creature_receiver_query()
        saved = ObjectQuerySpec.from_dict(query.to_dict())
        self.assertEqual(query, saved)
        saga = ObjectQueryResult(object_id="saga-creature", ref="saga-creature", printed_name="Saga predicate",
                                owner="A", controller="A", zone="battlefield",
                                types=("creature", "enchantment"), subtypes=("saga",))
        self.assertTrue(object_matches_query(saga, query))
        self.assertFalse(object_matches_query(replace(saga, types=("enchantment",)), query))
        self.assertFalse(object_matches_query(replace(saga, subtypes=()), query))
        artifact, legendary, saga_branch = query.any_of
        with self.assertRaises(ObjectQueryError):
            replace(query, any_of=(artifact, replace(legendary, types_all=("land",)), saga_branch))

        _, grant = self.program()
        caps = [value for value in grant.capability_dependencies if value != RECEIVER_UNION_CAPABILITY]
        altered = replace(grant, capability_dependencies=caps,
                          capability_closure=self.registry.closure(caps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + RECEIVER_UNION_CAPABILITY, bound["blockers"])

    def has_strike(self, game, card):
        return "double strike" in game.engine._combat_keywords(game.state.cards[card.object_id])

    def turn_up(self):
        game = self.session(7006041)
        engine = game.engine
        bayek = self.add(engine, NAME, zone="hand")
        artifact = self.add(engine, "Ornithopter", ref="historic-own-artifact")
        legendary = self.add(engine, "Yargle, Glutton of Urborg")
        ordinary = self.add(engine, "Grizzly Bears", ref="historic-ordinary-creature")
        opponent = self.add(engine, "Ornithopter", seat="B", ref="historic-foreign-artifact")
        land = self.add(engine, "Darksteel Citadel")
        for card in (artifact, legendary, ordinary, opponent):
            card.acquired_control_turn_count = -1
        self.assert_closed(game, bayek)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1})
        self.checkpoint(game)

        cast_id = "cast-disguise:" + bayek.ref
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(cast_id, {row["id"] for row in rows})
        accepted = game.act("pilot:A", {"action_id": cast_id, "pay": "auto",
                                      "reserve": {"C": 1, "R": 1, "W": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        current = game.state.cards[bayek.object_id]
        self.assertTrue(current.face_down)
        incarnation = current.logical_object_id
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(NAME, json.dumps(game.packet(principal, full=True)))
        self.assertFalse(self.has_strike(game, artifact))
        self.assertFalse(self.has_strike(game, legendary))

        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        turn_up = f"turn-face-up:{current.ref}"
        self.assertIn(turn_up, {row["id"] for row in rows})
        command = {"action_id": turn_up, "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        current = game.state.cards[bayek.object_id]
        self.assertEqual((False, incarnation), (current.face_down, current.logical_object_id))
        self.assertTrue(self.has_strike(game, bayek))
        self.assertTrue(self.has_strike(game, artifact))
        self.assertTrue(self.has_strike(game, legendary))
        self.assertFalse(self.has_strike(game, ordinary), "Ordinary creatures stay outside the historic set")
        self.assertFalse(self.has_strike(game, opponent))
        self.assertFalse(self.has_strike(game, land))
        return game, bayek, artifact, legendary, ordinary

    def test_actual_disguise_grants_strike_and_the_existing_two_damage_steps_then_expires_by_turn(self):
        game, bayek, artifact, legendary, ordinary = self.turn_up()
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.attackers")
        attackers = {legendary.ref: "B", ordinary.ref: "B"}
        attack = game.act("pilot:A", {"action_id": "attack", "attackers": attackers})
        self.assertTrue(attack.ok, attack.summary)
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        block = game.act("pilot:B", {"action_id": "block", "blocks": {}})
        self.assertTrue(block.ok, block.summary)
        for index, life in ((0, 31), (1, 20)):
            self.until(game, lambda current: current.state.step == "combat_damage"
                       and current.state.combat.damage_step_index == index
                       and current.state.pending_decision.kind == "priority")
            self.assertEqual(life, game.state.players["B"].life)
        self.until(game, lambda current: current.state.active_player == "B"
                   and current.state.pending_decision.kind == "priority")
        self.assertFalse(self.has_strike(game, artifact))
        self.assertFalse(self.has_strike(game, legendary))
        self.assertTrue(self.has_strike(game, bayek))
        self.replay(game, load=True)

    def test_actual_face_up_witness_kills_substituting_all_controlled_creatures(self):
        with patch("quorune.compiler.receiver_union_queries.historic_creature_receiver_query",
                   return_value=ObjectQuerySpec(zones=("battlefield",), types_all=("creature",))):
            with self.assertRaisesRegex(AssertionError, "Ordinary creatures stay outside the historic set"):
                self.turn_up()
