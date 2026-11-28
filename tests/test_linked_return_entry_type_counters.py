"""The delayed return adds counters from the original cards' prospective entry types."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import (
    LINKED_TYPE_ENTRY_COUNTERS_CAPABILITY,
    LINKED_TYPE_ENTRY_COUNTERS_VERSION,
    LinkedExileReturnSpec,
    linked_type_entry_counter_target_schema,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class LinkedReturnEntryTypeCounterTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-return-entry-type-counters.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/linked-return-entry-type-counters-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked return counters by entry type", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_semesters_end_keeps_its_two_bound_phases_and_exact_target_union(self):
        report, semantics = register_card(self.db, self.registry, "Semester's End")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        record = self.db.lookup("Semester's End", fuzzy=False)
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual(linked_type_entry_counter_target_schema(), program.target_schema)
        first, second = program.effects
        self.assertEqual(("exile", "return"), (first["phase"], second["phase"]))
        self.assertEqual(first["binding_id"], second["binding_id"])
        self.assertEqual(first["spec"], second["spec"])
        spec = LinkedExileReturnSpec.from_dict(first["spec"])
        self.assertEqual(LINKED_TYPE_ENTRY_COUNTERS_VERSION, spec.schema_version)
        self.assertEqual(spec, LinkedExileReturnSpec.from_dict(spec.to_dict()))
        for invalid in ({"tapped": True}, {"timing": "immediate"}, {"keywords": ["Haste"]}, {"entry_counters": [["+1/+1", 1]]}):
            with self.assertRaises(ValueError):
                LinkedExileReturnSpec.from_dict({**first["spec"], **invalid})
        deps = [cap for cap in program.capability_dependencies if cap != LINKED_TYPE_ENTRY_COUNTERS_CAPABILITY]
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertIn(f"capability:undeclared_runtime_dependency:{LINKED_TYPE_ENTRY_COUNTERS_CAPABILITY}",
                      result["blockers"])
        widened = replace(program, target_schema={**program.target_schema, "types_any": ["creature", "land"]})
        self.assertFalse(bind_semantic_program_runtime(
            widened, capability_registry=self.registry, profile=PROFILE,
        )["strict"])
        with patch("quorune.compiler.linked_exile_return_templates._TYPE_ENTRY_COUNTER_RETURN", ""):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
        old, _ = register_card(self.db, self.registry, "Otherworldly Journey")
        self.assertTrue(old["strict_capability_ready"], old["blockers"])

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def next_end(self, game):
        self.until(game, lambda value: value.state.step == "end_step" and bool(value.state.stack))
        return self.resolve(game)

    def test_actual_delayed_return_counts_current_types_and_preserves_intrinsic_loyalty(self):
        game = self.playing(61126501)
        spell = self.add(game.engine, "Semester's End", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears")
        walker = self.add(game.engine, "Jace Beleren")
        loamspeaker = self.add(game.engine, "Llanowar Loamspeaker")
        land = self.add(game.engine, "Forest")
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        for card in (spell, creature, walker, loamspeaker, land, foreign):
            self.assert_closed(game, card)
        walker.counters["loyalty"] = 6
        loamspeaker.acquired_control_turn_count = -1
        self.prepare(game, actor="A", active="A", mana={"C": 3, "W": 1})
        self.checkpoint(game)
        action, = (ability for ability in game.engine._activated_abilities(loamspeaker) if not ability.mana_ability)
        activation = f"activate:{loamspeaker.ref}:{action.ability_id}"
        self.priority(game, "A")
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(activation, {offer["id"] for offer in legal})
        animated = game.act("pilot:A", {"action_id": activation, "targets": [land.ref], "pay": "auto"})
        self.assertTrue(animated.ok, animated.summary)
        self.assertIsNone(self.resolve(game))
        self.assertIn("creature", game.engine._effective_type_parts(self.current(game, land))[0])
        self.priority(game, "A")
        offered = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offered["id"], "targets": [foreign.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [creature.ref, walker.ref, land.ref])
        self.assertIsNone(self.resolve(game))
        for card in (creature, walker, land):
            self.assertEqual("exile", self.current(game, card).zone)
        self.assertEqual("battlefield", self.current(game, foreign).zone)
        game = self.replay(game, load=True)
        pending = self.next_end(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        for card in (creature, walker, land):
            self.assertEqual("exile", self.current(game, card).zone)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        for option in options:
            with self.subTest(replacement=option["id"]):
                resumed = self.replay(game, load=True)
                command = {"action_id": "choose", "replacement": option["id"]}
                before = authoritative_state_hash(resumed.state)
                self.assertFalse(resumed.act("pilot:B", command).ok)
                self.assertEqual(before, authoritative_state_hash(resumed.state))
                chosen = resumed.act("pilot:A", command)
                self.assertTrue(chosen.ok, chosen.summary)
                self.assertIsNone(self.resolve(resumed))
                self.assertEqual(1, self.current(resumed, creature).counters.get("+1/+1", 0))
                self.assertEqual(4, self.current(resumed, walker).counters.get("loyalty", 0))
                self.assertEqual(0, self.current(resumed, walker).counters.get("+1/+1", 0))
                self.assertEqual({}, self.current(resumed, land).counters)
                self.assertEqual({"land"}, resumed.engine._effective_type_parts(self.current(resumed, land))[0])
                self.replay(resumed, load=True)

    def test_actual_delayed_return_uses_the_owner_and_normal_entry_counter_replacements(self):
        game = self.playing(61126502)
        spell = self.add(game.engine, "Semester's End", zone="hand")
        borrow = self.add(game.engine, "Control Magic", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", seat="B")
        scales = self.add(game.engine, "Hardened Scales", seat="B")
        for card in (spell, borrow, creature, scales):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 7, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", borrow, [creature.ref], reserve={"U": 3, "W": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", self.current(game, creature).controller)
        self.cast_for(game, "A", spell, [creature.ref])
        self.assertIsNone(self.resolve(game))
        self.assertIsNone(self.next_end(game))
        self.assertEqual("B", self.current(game, creature).controller)
        self.assertEqual(2, self.current(game, creature).counters.get("+1/+1", 0))
        self.replay(game, load=True)
