"""Explicit Forage mana choices, rollback, and exact replay."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.forage_mana import CAPABILITY, HANDLER_ID
from scripts.build_test_database import build_fixture_database


class ForageManaTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "forage-mana.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/forage-mana-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Forage mana choice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def actions(self, game):
        self.priority(game, "A")
        return game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

    def forage_action(self, game, source):
        action, = (row for row in self.actions(game) if row.get("source") == source.ref
                   and "cost_cards" in row.get("choice_schema", {}) and "mana_output" in row["choice_schema"])
        return action

    def scene(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, "Thornvault Forager")
        source.acquired_control_turn_count = -1
        meal = self.add(game.engine, "Gingerbrute", seat="B", ref="borrowed-food")
        game.engine.change_control(meal.object_id, "A", reason="Seed an owned-elsewhere Food")
        graveyard = [self.add(game.engine, name, zone="graveyard", ref="forage-grave-" + str(index))
                     for index, name in enumerate(("Forest", "Mountain", "Plains"))]
        foreign = self.add(game.engine, "Forest", seat="B", zone="graveyard", ref="foreign-forage")
        self.prepare(game)
        self.assert_closed(game, source)
        return game, source, meal, graveyard, foreign

    def test_forage_mana_exact_shape_requires_its_typed_carrier_and_two_colors(self):
        game, source, _, _, _ = self.scene(70155100)
        card = self.db.by_oracle_id(source.oracle_id)
        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry,
                                       capability_profile="commander_review")
        self.assertEqual("exact", compile_card(card).status)
        alternate = replace(card, oracle_text=card.oracle_text.replace("Add two mana", "Add three mana"))
        self.assertNotEqual("exact", compile_card(alternate).status)
        program, = (row for row in game.engine.semantics.programs_for_oracle(source.oracle_id)
                    if CAPABILITY in row.capability_dependencies)
        stripped = replace(program, handlers=[row for row in program.handlers if row.get("handler_id") != HANDLER_ID])
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(binding["strict"], binding)
        self.assertTrue(any("matching typed fixed-output" in blocker for blocker in binding["blockers"]), binding)

    def test_actual_forage_mana_pays_either_resource_branch_and_replays(self):
        for branch in ("food", "graveyard"):
            with self.subTest(branch=branch):
                game, source, meal, graveyard, foreign = self.scene(70155101 + (branch == "food"))
                game.state.config.semantic_policy = "trusted_only"
                self.checkpoint(game)
                action = self.forage_action(game, source)
                schema = action["choice_schema"]
                self.assertEqual([1, 3], schema["cost_cards"]["allowed_counts"])
                self.assertEqual({meal.ref, *(card.ref for card in graveyard)}, set(schema["cost_cards"]["legal_refs"]))
                combos = [row["value"] for row in schema["mana_output"]["options"]]
                self.assertEqual(15, len(combos))
                self.assertTrue(all(sum(mode.values()) == 2 and set(mode) <= set("WUBRG") for mode in combos))
                refs = [meal.ref] if branch == "food" else [card.ref for card in graveyard]
                output = {"R": 1, "U": 1} if branch == "food" else {"U": 2}
                command = {"action_id": action["id"], "pay": "auto", "cost_cards": refs, "mana_output": output}
                self.reject(game, "B", command)
                self.reject(game, "A", {**command, "cost_cards": [foreign.ref]})
                self.reject(game, "A", {**command, "cost_cards": [meal.ref, graveyard[0].ref, graveyard[1].ref]})
                self.reject(game, "A", {**command, "mana_output": {"C": 2}})
                self.reject(game, "A", {**command, "mana_output": {"R": 3}})
                self.act(game, "A", command)
                self.assertTrue(game.state.cards[source.object_id].tapped)
                self.assertFalse(game.state.stack)
                self.assertEqual(output, {k: v for k, v in game.state.players["A"].mana_pool.items() if v})
                if branch == "food":
                    self.assertIn(meal.object_id, game.state.players["B"].zones["graveyard"])
                    self.assertTrue(all(game.state.cards[c.object_id].zone == "graveyard" for c in graveyard))
                else:
                    self.assertEqual("battlefield", game.state.cards[meal.object_id].zone)
                    self.assertTrue(all(game.state.cards[c.object_id].zone == "exile" for c in graveyard))
                self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)

    def test_actual_forage_is_explicit_and_rejects_expired_resource_after_untap(self):
        game, source, meal, _, _ = self.scene(70155110)
        bolt = self.add(game.engine, "Lightning Bolt", zone="hand")
        untap = self.add(game.engine, "Twiddle", zone="hand")
        self.checkpoint(game)
        modes = [mode for current in game.engine.available_mana_sources("A") if current.ref == source.ref
                 for mode in current.modes]
        self.assertEqual([{"G": 1}], [{key: value for key, value in m.bundle.items() if value} for m in modes])
        self.reject(game, "A", {"a": "cast", "card": bolt.ref, "targets": ["C"], "pay": "auto"})
        first = self.forage_action(game, source)
        self.act(game, "A", {"action_id": first["id"], "cost_cards": [meal.ref],
                            "mana_output": {"R": 1, "U": 1}, "pay": "auto"})
        self.cast(game, "A", bolt, ["C"])
        self.resolve_top(game)
        self.cast(game, "A", untap, [source.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.act(game, "A", {"action_id": "choose", "choice": "untap"})
        self.assertFalse(game.state.cards[source.object_id].tapped)
        fresh = self.forage_action(game, game.state.cards[source.object_id])
        self.assertNotIn(meal.ref, fresh["choice_schema"]["cost_cards"]["legal_refs"])
        self.reject(game, "A", {"action_id": fresh["id"], "cost_cards": [meal.ref],
                                "mana_output": {"R": 2}, "pay": "auto"})
        self.replay(game, load=True)
