"""Source tap and per-opponent current poison thresholds through native play."""
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.life_state import LifeChange
from quorune.oracle_ir import compile_oracle_card
from quorune.per_player_query_life_model import (
    OPPONENT_POISON_THRESHOLD_LIFE_VERSION,
    PerPlayerQueryLifeLossSpec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.per_player_query_life import POISON_THRESHOLD_CAPABILITY
from quorune.semantic_runtime.per_player_query_life import resolve_per_player_query_life
from scripts.build_test_database import build_fixture_database


FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Phyrexian Atlas"
PROFILE = "commander_review"


class SourceTappedPoisonLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-tapped-poison.sqlite3"
        build_fixture_database([
            FIXTURES / "bound-effect-program-cards.json",
            FIXTURES / "counter-prohibition-cards.json",
            FIXTURES / "source-tapped-poison-life-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Current opponent poison thresholds", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def compiled_source(self):
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        return record, compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

    def test_exact_atlas_binds_a_separate_untargeted_tap_trigger_and_rejects_scope_drift(self):
        record, compiled = self.compiled_source()
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        (trigger,) = (node for node in compiled.faces[0].nodes if node.event == "permanent.tap")
        self.assertIsNone(trigger.target_schema)
        self.assertIn(POISON_THRESHOLD_CAPABILITY, trigger.capability_dependencies)
        (effect,) = trigger.effects
        self.assertEqual("lose_life_each_opponent", effect["op"])
        raw = effect["amount"]
        spec = PerPlayerQueryLifeLossSpec.from_dict(raw)
        self.assertEqual(OPPONENT_POISON_THRESHOLD_LIFE_VERSION, spec.schema_version)
        self.assertEqual((3, 1), (spec.amount.minimum_counters, spec.amount.life_loss))
        self.assertEqual([0, 0, 1, 1], [spec.life_loss_for_quantity(count) for count in (0, 2, 3, 5)])
        self.assertEqual(raw, spec.to_dict())
        for altered in (
            record.oracle_text.replace("this artifact becomes tapped", "this artifact becomes untapped"),
            record.oracle_text.replace("this artifact becomes tapped", "an artifact becomes tapped"),
            record.oracle_text.replace("this artifact becomes tapped", "this artifact enters"),
            record.oracle_text.replace("each opponent", "each player"),
            record.oracle_text.replace("three or more poison counters", "three poison counters"),
            record.oracle_text.replace("three or more poison counters", "zero or more poison counters"),
        ):
            with self.subTest(altered=altered):
                result = compile_oracle_card(replace(record, oracle_text=altered),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)

        game = self.session(605126056)
        atlas = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, atlas)
        (program,) = (candidate for candidate in game.engine.semantics.programs_for_oracle(atlas.oracle_id)
                      if candidate.event == "permanent.tap")
        kept = tuple(capability for capability in program.capability_dependencies
                     if capability != POISON_THRESHOLD_CAPABILITY)
        missing = replace(program, capability_dependencies=kept,
                          capability_closure=self.registry.closure(kept, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + POISON_THRESHOLD_CAPABILITY, result["blockers"])

    def test_resolution_uses_independent_positive_thresholds_and_kills_the_unqualified_mutant(self):
        _, compiled = self.compiled_source()
        (trigger,) = (node for node in compiled.faces[0].nodes if node.event == "permanent.tap")
        (effect,) = trigger.effects
        host = SimpleNamespace(
            active_seats=tuple("ABCD"),
            apnap_order=lambda: tuple("DBCA"),
            state=SimpleNamespace(players={seat: SimpleNamespace(poison=count)
                                           for seat, count in zip("ABCD", (4, 2, 3, 5))}),
        )
        item = SimpleNamespace(controller="A")
        expected = [{"player": "D", "amount": -1}, {"player": "C", "amount": -1}]
        self.assertEqual(expected, resolve_per_player_query_life(host, effect["amount"], item)["changes"])
        with patch.object(PerPlayerQueryLifeLossSpec, "life_loss_for_quantity", return_value=1):
            self.assertNotEqual(expected, resolve_per_player_query_life(host, effect["amount"], item)["changes"])
        for changed in (
            {**effect["amount"], "relation": "all_active_players"},
            {**effect["amount"], "amount": {"minimum_counters": True, "life_loss": 1}},
            {**effect["amount"], "amount": {"minimum_counters": 3, "life_loss": 0}},
            {**effect["amount"], "amount": {"minimum_counters": -1, "life_loss": 1}},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                PerPlayerQueryLifeLossSpec.from_dict(changed)

    def test_actual_mana_tap_queues_before_any_opponent_qualifies_and_reads_the_response(self):
        from quorune.effect_runtime import life_effects

        game = self.session(605126057)
        atlas = self.add(game.engine, ORIGINAL)
        prologue = self.add(game.engine, "Prologue to Phyresis", seat="D", zone="hand")
        self.add(game.engine, "Island", seat="D", ref="atlas-response-blue")
        self.add(game.engine, "Generic Bound Plains", seat="D", ref="atlas-response-generic")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="atlas-private")
        for seat, count in zip("ABCD", (4, 2, 2, 0)):
            game.state.players[seat].poison = count
        self.assert_closed(game, atlas)
        self.assert_closed(game, prologue)
        self.prepare(game)
        self.checkpoint(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        (offered,) = (row for row in actions if row.get("source") == atlas.ref and row.get("mana_ability"))
        command = {"action_id": offered["id"], "mana_output": {"G": 1}}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        activated = game.act("pilot:A", command)
        self.assertTrue(activated.ok, activated.summary)

        self.assertTrue(game.state.cards[atlas.object_id].tapped)
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        (trigger,) = game.state.stack
        self.assertEqual(("triggered_ability", "A", atlas.object_id),
                         (trigger.kind, trigger.controller, trigger.source_object_id))
        self.assertEqual([], trigger.targets)
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        game = self.replay(game, load=True)

        self.cast(game, "D", prologue)
        self.resolve_top(game)
        self.assertEqual([5, 3, 3, 0], [game.state.players[seat].poison for seat in "ABCD"])
        self.assertEqual([trigger.ref], [item.ref for item in game.state.stack])
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        original = life_effects._commit
        observed = []

        def observe(host, changes, **kwargs):
            batch = tuple(changes)
            observed.append(batch)
            self.assertEqual([40] * 4, [host.state.players[seat].life for seat in "ABCD"])
            return original(host, batch, **kwargs)

        with patch.object(life_effects, "_commit", side_effect=observe):
            self.assertIsNone(self.resolve(game))
        self.assertEqual([(LifeChange("B", -1), LifeChange("C", -1))], observed)
        self.assertEqual([40, 39, 39, 40], [game.state.players[seat].life for seat in "ABCD"])
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)
