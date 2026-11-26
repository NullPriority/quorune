"""Resolve the printed counterless and named-counter-absence battlefield sets."""

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
from quorune.object_predicate import (
    COUNTER_ABSENCE_QUERY_CAPABILITY,
    CounterAbsenceSpec,
    ObjectQueryError,
    ObjectQuerySpec,
    PermanentStatePredicateSpec,
    permanent_state_predicate_matches,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class PublicCreatureCounterAbsenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-creature-counter-absence.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "nonartifact-nonblack-mass-destruction-cards.json",
            fixtures / "public-creature-counter-absence-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public counter absence", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_exact_counter_absence_programs_require_the_typed_capability(self):
        cases = (
            ("Damning Verdict", None, "destroy_all", "no counters", "no flying counters"),
            ("Wave Goodbye", "+1/+1", "move_public_zone_set", "+1/+1", "charge"),
        )
        for name, counter, operation, printed, unsupported in cases:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                binding, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                self.assertIsNone(program.target_schema)
                effect, = program.effects
                self.assertEqual(operation, effect["op"])
                query = ObjectQuerySpec.from_dict(effect["set"]["query"])
                self.assertEqual(("creature",), query.types_all)
                self.assertEqual(CounterAbsenceSpec(counter), query.state_predicate.counter_absence)
                self.assertIn(COUNTER_ABSENCE_QUERY_CAPABILITY, program.capability_dependencies)
                dependencies = tuple(c for c in program.capability_dependencies if c != COUNTER_ABSENCE_QUERY_CAPABILITY)
                weakened = replace(
                    program, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict(),
                )
                result = bind_semantic_program_runtime(
                    weakened, capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(result["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + COUNTER_ABSENCE_QUERY_CAPABILITY,
                              result["blockers"])
                other = replace(card, oracle_text=card.oracle_text.replace(printed, unsupported))
                self.assertNotEqual("exact", compile_oracle_card(
                    other, capability_registry=self.registry, capability_profile="commander_review",
                ).status)

    def test_counter_absence_codec_distinguishes_named_from_all_and_preserves_older_shape(self):
        counterless = PermanentStatePredicateSpec(counter_absence=CounterAbsenceSpec())
        named = PermanentStatePredicateSpec(counter_absence=CounterAbsenceSpec("+1/+1"))
        historical = {"entered_this_turn": False, "tapped": None, "counter_name": "+1/+1",
                      "minimum_counter_count": 1}
        self.assertEqual(historical, PermanentStatePredicateSpec.from_dict(historical).to_dict())
        for spec in (counterless, named):
            self.assertEqual(spec, PermanentStatePredicateSpec.from_dict(spec.to_dict()))
        for data in (
            {"counter_absence": {"counter_name": 0}},
            {"counter_absence": {"counter_name": ""}},
            {"counter_absence": {"counter_name": None, "minimum": 0}},
            {"counter_absence": {"counter_name": None}, "face_down": True},
        ):
            with self.subTest(data=data), self.assertRaises(ObjectQueryError):
                PermanentStatePredicateSpec.from_dict(data)

        def witness():
            for counters, expected in (({}, (True, True)),
                                       ({"charge": 0}, (True, True)),
                                       ({"charge": 1}, (False, True)),
                                       ({"+1/+1": 1}, (False, False))):
                actual = tuple(permanent_state_predicate_matches(
                    spec, counters=counters, entered_this_turn=False, tapped=False,
                ) for spec in (counterless, named))
                self.assertEqual(expected, actual)

        witness()
        with patch("quorune.object_predicate.permanent_counter_total", return_value=0):
            with self.assertRaises(AssertionError):
                witness()

    def scene(self, name, seed, mana, *, response=False):
        game = self.session(seed)
        spell = self.add(game.engine, name, zone="hand")
        bare = self.add(game.engine, "Grizzly Bears", seat="B", zone="hand", ref="counter-absence-bare")
        game.engine.move_card(bare.object_id, "battlefield", controller="A", log=False)
        unrelated = self.add(game.engine, "Grizzly Bears", seat="B", ref="counter-absence-charge")
        unrelated.counters["charge"] = 1
        protected = self.add(game.engine, "Grizzly Bears", seat="C", ref="counter-absence-plus-one")
        protected.counters["+1/+1"] = 1
        other_type = self.add(game.engine, "Liquimetal Coating", seat="D", ref="counter-absence-artifact")
        hexmage = self.add(game.engine, "Vampire Hexmage", seat="B") if response else None
        self.assert_closed(game, spell)
        self.prepare(game, mana=mana)
        self.checkpoint(game)
        action_id = "cast:" + spell.ref
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:D", {"action_id": action_id, "pay": "auto"})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell)
        return game, bare, unrelated, protected, other_type, hexmage

    def test_actual_counterless_destruction_uses_the_current_public_set_and_replays(self):
        for response in (False, True):
            with self.subTest(response=response):
                game, bare, unrelated, protected, other_type, hexmage = self.scene(
                    "Damning Verdict", 74590010 + response, {"C": 3, "W": 2}, response=response,
                )
                if hexmage is not None:
                    self.priority(game, "B")
                    actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    activation, = (action for action in actions if action["id"].startswith("activate:" + hexmage.ref + ":"))
                    assert_valid = game.act("pilot:B", {"action_id": activation["id"],
                                                          "targets": [unrelated.ref], "pay": "auto"})
                    self.assertTrue(assert_valid.ok, assert_valid.summary)
                    self.resolve_top(game)
                    self.assertFalse(game.state.cards[unrelated.object_id].counters)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                graveyard = game.state.players["B"].zones["graveyard"]
                self.assertIn(bare.object_id, graveyard)
                self.assertEqual(response, unrelated.object_id in graveyard)
                for survivor in (protected, other_type):
                    self.assertEqual("battlefield", game.state.cards[survivor.object_id].zone)
                self.replay(game, load=True)

    def test_actual_named_counter_bounce_keeps_plus_one_and_returns_to_the_private_owners(self):
        game, bare, unrelated, protected, other_type, _ = self.scene(
            "Wave Goodbye", 74590020, {"C": 2, "U": 2},
        )
        game = self.replay(game, load=True)
        self.resolve_top(game)
        owner_hand = game.state.players["B"].zones["hand"]
        self.assertTrue({bare.object_id, unrelated.object_id} <= set(owner_hand))
        for survivor in (protected, other_type):
            self.assertEqual("battlefield", game.state.cards[survivor.object_id].zone)
        owner = game.packet("pilot:B", full=True)["state"]["players"]["B"]
        observer = game.packet("pilot:C", full=True)["state"]["players"]["B"]
        self.assertIn("hand", owner)
        self.assertNotIn("hand", observer)
        self.replay(game, load=True)
