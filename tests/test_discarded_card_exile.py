from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckDefinition, DeckEntry
from quorune.effect_runtime.linked_zone_movement import apply_move_if_in_zone
from quorune.errors import GameRuleError
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.discarded_card_exile import (
    DISCARD_EVENT, DISCARD_EXILE_CAPABILITY, DISCARD_EXILE_DEPENDENCIES, MADNESS_ARRIVAL,
    controller_discard_condition, discarded_card_exile_effect, discarded_card_exile_movement,
)
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database


SOURCE_NAME = "Generic Discard Exile Fixture"
SOURCE_ID = "3ff5b21a-01d2-4fd2-9c68-3f5ae3899072"
CLAUSE = "Whenever you discard a card, exile that card from your graveyard."
PROFILE = "commander_review"
PAIRING_FIXTURE = ROOT / "tests/fixtures/commander-pairing-cards.json"
MADNESS_FIXTURE = ROOT / "tests/fixtures/madness-cards.json"
REPLACEMENT_FIXTURE = ROOT / "tests/fixtures/committed-milled-creature-tokens-cards.json"
MADNESS_CARD = "Generic Madness Life Fixture"
OUTLET = "Generic Discard Outlet Fixture"


def source_record(text=CLAUSE):
    return CardRecord(
        oracle_id=SOURCE_ID, name=SOURCE_NAME, mana_cost="{1}{B}", mana_value=2,
        type_line="Enchantment", oracle_text=text, power=None, toughness=None,
        loyalty=None, defense=None, colors=("B",), color_identity=("B",), keywords=(),
        produced_mana=(), layout="normal", released_at="2026-01-01",
        legalities={"commander": "legal"}, faces=(), raw={},
    )


class DiscardedCardExileCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text=CLAUSE, *, registry=None):
        return compile_oracle_card(
            source_record(text), capability_registry=registry or self.registry, capability_profile=PROFILE,
        )

    def test_compiles_one_exact_mandatory_nontargeted_discard_occurrence(self):
        ir = self.compile()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(CLAUSE, CLAUSE[node.span.start:node.span.end])
        self.assertEqual(("battlefield", DISCARD_EVENT), (node.active_zone, node.event))
        self.assertEqual(controller_discard_condition(), node.event_condition)
        self.assertEqual((discarded_card_exile_effect(),), node.effects)
        self.assertIsNone(node.target_schema)
        self.assertLessEqual(set(DISCARD_EXILE_DEPENDENCIES), set(node.capability_dependencies))

    def test_compiler_variants_and_dependency_withdrawal_fail_closed(self):
        self.assertEqual("exact", self.compile().status)
        for text in (
            CLAUSE.replace("you discard", "an opponent discards"),
            CLAUSE.replace("exile that card", "you may exile that card"),
            CLAUSE.replace("that card", "target card"),
            CLAUSE.replace("your graveyard", "any graveyard"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertFalse(any(
                    DISCARD_EXILE_CAPABILITY in node.capability_dependencies
                    for node in ir.faces[0].nodes if node.exact
                ))
        base = DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8")
        for capability in DISCARD_EXILE_DEPENDENCIES:
            with self.subTest(capability=capability):
                registry_data = json.loads(base)
                row = next(row for row in registry_data["capabilities"] if row["id"] == capability)
                row["status"] = "blocked"
                row["blockers"] = ["focused discard-exile dependency withdrawal"]
                registry = CapabilityRegistry(registry_data)
                registry.mark_evidence_verified("1" * 64)
                ir = self.compile(registry=registry)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(any(capability in blocker for residual in ir.material_residuals
                                    for blocker in residual.blockers), ir.material_residuals)

    def test_linked_exile_sibling_blocks_the_unlinked_discard_result(self):
        linked = CLAUSE + "\nReturn each card exiled with this enchantment to its owner’s hand."
        ir = self.compile(linked)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any("linked discarded-card exile" in residual.blockers for residual in ir.material_residuals))
        self.assertFalse(any(node.lowerable and node.effects for node in ir.faces[0].nodes if node.text == CLAUSE))

    def test_resolved_descriptor_rejects_changed_graveyard_or_forged_successors(self):
        effect = {
            **discarded_card_exile_effect(), "card": "test-card", "discard_destination": "exile",
            "discard_zone_change_counter": 2, "discard_owner": "A", MADNESS_ARRIVAL: None,
        }
        self.assertIsNone(discarded_card_exile_movement(effect, actor="A"))
        self.assertIsNone(discarded_card_exile_movement({**effect, MADNESS_ARRIVAL: 3}, actor="B"))
        for mutated in (
            {**effect, "from": "hand"},
            {**effect, MADNESS_ARRIVAL: True},
            {**effect, MADNESS_ARRIVAL: 4},
            {**effect, "discard_destination": "library", MADNESS_ARRIVAL: 3},
        ):
            with self.subTest(mutated=mutated), self.assertRaises(ValueError):
                discarded_card_exile_movement(mutated, actor="A")


class DiscardedCardExileNativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name)
        source = path / "discard-source.json"
        card = source_record()
        source.write_text(json.dumps({"schema_version": 1, "cards": [
            {**card.compact(), "cmc": card.mana_value}
        ]}), encoding="utf-8")
        database = path / "discard.sqlite3"
        build_fixture_database((PAIRING_FIXTURE, MADNESS_FIXTURE, REPLACEMENT_FIXTURE, source), database)
        cls.db = CardDatabase(database)
        cls.deck = DeckDefinition(
            name="Discard occurrence and Madness",
            entries=[
                DeckEntry("Thrasios, Triton Hero", board="commander"),
                DeckEntry(SOURCE_NAME),
                DeckEntry(OUTLET, quantity=2),
                DeckEntry(MADNESS_CARD, quantity=5),
                DeckEntry("Rest in Peace"),
                DeckEntry("Generic Discard Cost Spell Fixture", quantity=5),
            ],
            commanders=["Thrasios, Triton Hero"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed):
        game = CommanderSession.create(
            self.db, {seat: self.deck for seat in "ABCD"},
            first_player="A", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        while game.state.pending_decision is not None and game.state.pending_decision.kind == "mulligan.declare":
            for principal in tuple(game.pending_principals()):
                result = game.act(principal, {"action_id": "keep"})
                self.assertTrue(result.ok, result.summary)
        engine = game.engine
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.priority_player = None
        game.state.priority_passes = []
        game.state.active_player = "A"
        game.state.started = True
        game.state.phase, game.state.step = "precombat_main", "main"
        source = self.card(game, SOURCE_NAME)
        engine.move_card(source.object_id, "battlefield", log=False)
        return game, source

    def card(self, game, name, seat="A", *, zone=None):
        card = next(card for card in game.state.cards.values() if card.owner == seat and card.printed_name == name)
        if zone is not None and card.zone != zone:
            game.engine.move_card(card.object_id, zone, log=False)
        return card

    def discard(self, game, card):
        game.engine.apply_effect({"op": "discard", "card": card.ref}, actor=card.owner)

    def start_priority(self, game):
        game.engine._grant_priority("A")
        game.engine.pump()

    def pass_until(self, game, predicate, *, limit=32):
        for _ in range(limit):
            if predicate():
                return
            principals = game.pending_principals()
            self.assertTrue(principals)
            result = game.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Native discard witness did not reach its expected resolution")

    def activate_discard(self, game, paid):
        outlet = self.card(game, OUTLET, zone="battlefield")
        game.state.players["A"].mana_pool["C"] = 1
        self.start_priority(game)
        game.initial_checkpoint = checkpoint_envelope(game.state)
        game.commands.clear()
        game.decisions.clear()
        actions = game.engine._priority_action_hints("A")["actions"]
        action, = [row for row in actions if row.get("source") == outlet.ref]
        result = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [paid.ref]})
        self.assertTrue(result.ok, result.summary)

    def assert_replay(self, game):
        expected = authoritative_state_hash(game.state)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "discard-exile"
            game.save(path)
            replay = replay_record(path, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_occurring_discard_exiles_only_its_card_without_targeting_and_replays(self):
        game, source = self.session(400_700_01)
        card = self.card(game, "Generic Discard Cost Spell Fixture", zone="hand")
        bystander = self.card(game, MADNESS_CARD, zone="graveyard")
        private = self.card(game, "Rest in Peace", seat="C", zone="hand")
        self.activate_discard(game, card)
        self.assertEqual("graveyard", card.zone)
        trigger = game.state.stack[-1]
        self.assertEqual((source.object_id, DISCARD_EVENT, []),
                         (trigger.source_object_id, trigger.context["event"], trigger.targets))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.pass_until(game, lambda: not game.state.stack)
        self.assertEqual("exile", card.zone)
        self.assertEqual("graveyard", bystander.zone)
        self.assert_replay(game)

    def test_other_seats_ordinary_moves_and_failed_discards_create_no_occurrence(self):
        game, _source = self.session(400_700_02)
        ordinary = self.card(game, "Generic Discard Cost Spell Fixture", zone="hand")
        game.engine.move_card(ordinary.object_id, "graveyard", semantic_events=True)
        with self.assertRaises(GameRuleError):
            self.discard(game, ordinary)
        opposing = self.card(game, "Generic Discard Cost Spell Fixture", seat="B", zone="hand")
        self.discard(game, opposing)
        self.assertFalse(game.state.pending_trigger_batches)
        self.assertFalse(game.state.stack)
        self.assertEqual(("graveyard", "graveyard"), (ordinary.zone, opposing.zone))
        forged = {
            **discarded_card_exile_effect(), "card": opposing.ref, "discard_destination": "graveyard",
            "discard_zone_change_counter": opposing.zone_change_counter, "discard_owner": "A", MADNESS_ARRIVAL: None,
        }
        apply_move_if_in_zone(game.engine, forged, actor="A", operation="move_if_in_zone", reason="Ownership witness")
        self.assertEqual("graveyard", opposing.zone)

    def test_two_discards_keep_separate_objects_when_trigger_source_leaves(self):
        game, source = self.session(400_700_03)
        hand = [card for card in game.state.cards.values()
                if card.owner == "A" and card.printed_name == "Generic Discard Cost Spell Fixture"][:2]
        for card in hand:
            game.engine.move_card(card.object_id, "hand", log=False)
            self.discard(game, card)
        game.engine.move_card(source.object_id, "graveyard", semantic_events=True)
        self.start_priority(game)
        decision = game.state.pending_decision
        self.assertEqual("trigger.order", decision.kind)
        items = game.state.pending_trigger_batches[0].items
        self.assertEqual({card.ref for card in hand}, {item.event_facts["card"] for item in items})
        result = game.act("pilot:A", {"action_id": "order", "triggers": [item.ref for item in items]})
        self.assertTrue(result.ok, result.summary)
        self.pass_until(game, lambda: not game.state.stack)
        self.assertEqual(["exile", "exile"], [card.zone for card in hand])
        self.assertEqual("graveyard", source.zone)

    def stale_destination(self, seed):
        game, _source = self.session(seed)
        card = self.card(game, "Generic Discard Cost Spell Fixture", zone="hand")
        self.discard(game, card)
        self.assertEqual("graveyard", card.zone)
        game.engine.move_card(card.object_id, "hand", semantic_events=True)
        game.engine.move_card(card.object_id, "graveyard", semantic_events=True)
        self.start_priority(game)
        self.pass_until(game, lambda: not game.state.stack)
        return card.zone

    def test_incarnation_check_and_runtime_omission_mutant(self):
        self.assertEqual("graveyard", self.stale_destination(400_700_04))

        def omit_incarnation(effect, *, actor):
            movement = discarded_card_exile_movement(effect, actor=actor)
            return {key: value for key, value in movement.items() if key != "expected_zone_change_counter"}

        with patch("quorune.rules.discarded_card_exile.discarded_card_exile_movement", side_effect=omit_incarnation):
            self.assertEqual("exile", self.stale_destination(400_700_05))

    def test_replacement_redirects_the_discard_but_does_not_create_a_graveyard_link(self):
        game, _source = self.session(400_700_06)
        card = self.card(game, "Generic Discard Cost Spell Fixture", zone="hand")
        replacement = self.card(game, "Rest in Peace", seat="B", zone="battlefield")
        self.discard(game, card)
        self.assertEqual("exile", card.zone)
        locked = card.zone_change_counter
        game.engine.move_card(replacement.object_id, "hand", semantic_events=True)
        game.engine.move_card(card.object_id, "graveyard", semantic_events=True)
        self.assertEqual(("graveyard", locked + 1), (card.zone, card.zone_change_counter))
        self.start_priority(game)
        self.assertEqual(DISCARD_EVENT, game.state.stack[-1].context["event"])
        self.pass_until(game, lambda: not game.state.stack)
        self.assertEqual(("graveyard", locked + 1), (card.zone, card.zone_change_counter))

    def test_madness_order_controls_exact_graveyard_successor_with_principal_rollback_and_replay(self):
        for madness_first, expected in ((True, "exile"), (False, "graveyard")):
            with self.subTest(madness_first=madness_first):
                game, source = self.session(400_700_07 + int(madness_first))
                card = self.card(game, MADNESS_CARD, zone="hand")
                self.activate_discard(game, card)
                self.assertEqual("exile", card.zone)
                self.assertEqual("trigger.order", game.state.pending_decision.kind)
                items = game.state.pending_trigger_batches[0].items
                discard, = [item.ref for item in items if item.event_facts.get(MADNESS_ARRIVAL, False) is None]
                madness, = [item.ref for item in items if item.ref != discard]
                refs = [discard, madness] if madness_first else [madness, discard]
                command = {"action_id": "order", "triggers": refs}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.pass_until(game, lambda: game.state.pending_decision.kind == "semantic.choice")
                decision = {"action_id": "choose", "choice": "decline"}
                before = authoritative_state_hash(game.state)
                self.assertFalse(game.act("pilot:C", decision).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                result = game.act("pilot:A", decision)
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("graveyard", card.zone)
                if madness_first:
                    old_trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
                    self.assertEqual(card.zone_change_counter, old_trigger.context[MADNESS_ARRIVAL])
                self.pass_until(game, lambda: not game.state.stack)
                self.assertEqual(expected, card.zone)
                self.assert_replay(game)

    def test_madness_cast_cannot_supply_a_discard_successor(self):
        game, source = self.session(400_700_09)
        card = self.card(game, MADNESS_CARD, zone="hand")
        game.state.players["A"].mana_pool["B"] = 1
        self.activate_discard(game, card)
        items = game.state.pending_trigger_batches[0].items
        discard, = [item.ref for item in items if item.event_facts.get(MADNESS_ARRIVAL, False) is None]
        madness, = [item.ref for item in items if item.ref != discard]
        ordered = game.act("pilot:A", {"action_id": "order", "triggers": [discard, madness]})
        self.assertTrue(ordered.ok, ordered.summary)
        self.pass_until(game, lambda: game.state.pending_decision.kind == "semantic.choice")
        cast = game.act("pilot:A", {"action_id": "choose", "choice": "cast"})
        self.assertTrue(cast.ok, cast.summary)
        old_trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertIsNone(old_trigger.context[MADNESS_ARRIVAL])
        self.pass_until(game, lambda: not game.state.stack)
        self.assertEqual("graveyard", card.zone)
