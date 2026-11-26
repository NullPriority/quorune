"""Native combat damage, graveyard targeting and committed exile evidence."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.targeted_graveyard_outcomes import (
    DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY,
    DAMAGE_TARGET_BOUND_FIELD,
    bind_damage_graveyard_target_bound,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class DamageLinkedGraveyardOutcomeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "damage-linked-graveyard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/damage-linked-graveyard-outcome-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Damage-linked exile", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_original_closes_only_the_committed_damage_owner_and_outcomes(self):
        card = self.db.lookup("Froghemoth", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = (node for node in ir.faces[0].nodes if node.kind == "triggered_ability")
        effect, = trigger.effects
        schema = trigger.target_schema
        self.assertEqual("$context.target", schema["owner_seat"])
        self.assertEqual(1, schema[DAMAGE_TARGET_BOUND_FIELD])
        self.assertEqual("$context.amount", effect["maximum_targets"])
        self.assertEqual(2, effect["followup"]["schema_version"])
        self.assertIn(DAMAGE_GRAVEYARD_OUTCOME_CAPABILITY, trigger.capability_dependencies)
        facts = {"target_kind": "player", "combat": True, "amount": 9}
        self.assertEqual(9, bind_damage_graveyard_target_bound(schema, facts)["up_to"])
        for patch in ({"amount": 0}, {"amount": True}, {"combat": False}, {"target_kind": "permanent"}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                bind_damage_graveyard_target_bound(schema, {**facts, **patch})

        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = (node for node in semantics.programs_for_oracle(card.oracle_id)
                    if node.event == "damage.dealt.self")
        for mutant in (
            replace(program, event="damage.dealt"),
            replace(program, target_schema={**schema, "owner_seat": "opponent"}),
            replace(program, effects=({**effect, "maximum_targets": "$context.assigned_amount"},)),
            replace(program, effects=({**effect, "followup": {**effect["followup"], "schema_version": 1}},)),
        ):
            with self.subTest(mutant=mutant):
                bind = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bind["strict"], bind)

    def stage_combat(self, seed, *, counters=0, quantity=2, response=False):
        game = self.session(seed)
        frog = self.add(game.engine, "Froghemoth")
        if counters:
            frog.counters["+1/+1"] = counters
        grave = [
            self.add(game.engine, "Grizzly Bears" if n % 2 == 0 else "Mountain",
                     seat="B", zone="graveyard", ref=f"damage-exile-{n}")
            for n in range(quantity)
        ]
        foreign = self.add(game.engine, "Mountain", seat="C", zone="graveyard", ref="other-grave")
        hidden = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="damage-exile-private")
        cremate = self.add(game.engine, "Cremate", seat="C", zone="hand") if response else None
        if cremate is not None:
            self.add(game.engine, "Swamp", seat="C")
            self.assert_closed(game, cremate)
        self.assert_closed(game, frog)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(frog, "B")])
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "semantic.target")
        return game, frog, grave, foreign, hidden, cremate

    def test_actual_dealt_damage_bounds_targets_and_committed_types_replay(self):
        game, frog, grave, foreign, hidden, _ = self.stage_combat(736435001, counters=3, quantity=8)
        self.assertEqual(33, game.state.players["B"].life)
        self.assertEqual(7, game.state.stack[-1].context["amount"])
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertNotIn(foreign.ref, json.dumps(schema))
        self.assertNotIn(hidden.ref, json.dumps(schema))
        before = authoritative_state_hash(game.state)
        for refs in ([card.ref for card in grave], [foreign.ref], [grave[0].ref, grave[0].ref]):
            denied = game.act("pilot:A", {"action_id": "choose", "targets": refs})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.target(game, "A", [card.ref for card in grave[:-1]])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(7, game.state.cards[frog.object_id].counters["+1/+1"])
        self.assertEqual(43, game.state.players["A"].life)
        for card in grave[:-1]:
            self.assertEqual("exile", game.state.cards[card.object_id].zone)
        for card in (grave[-1], foreign):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_actual_zero_and_stale_targets_do_not_add_to_either_count(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                game, frog, grave, _, _, cremate = self.stage_combat(736435010 + stale, response=stale)
                self.target(game, "A", [card.ref for card in grave] if stale else [])
                if stale:
                    self.cast_for(game, "C", cremate, [grave[0].ref])
                    self.resolve_top(game)
                    moved = game.state.cards[grave[0].object_id]
                    self.assertEqual("exile", moved.zone)
                    incarnation = moved.logical_object_id
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertFalse(game.state.cards[frog.object_id].counters)
                self.assertEqual(41 if stale else 40, game.state.players["A"].life)
                self.assertEqual("exile" if stale else "graveyard", game.state.cards[grave[1].object_id].zone)
                if stale:
                    self.assertEqual(incarnation, game.state.cards[grave[0].object_id].logical_object_id)
                self.replay(game, load=True)
