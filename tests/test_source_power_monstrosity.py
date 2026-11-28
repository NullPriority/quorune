"""Exercise one exact targeted source-power trigger and a live Monstrosity amount."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.counter_placement_sets import SOURCE_POWER_COMBAT_TARGET_COUNTER_CAPABILITY
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.controlled_counter_monstrosity import CONTROLLED_COUNTER_MONSTROSITY_CAPABILITY
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
SOURCE = "Generic Counter Steward"
TRIGGER = ("At the beginning of combat on your turn, put a number of +1/+1 counters "
           "equal to this creature's power on another target creature you control.")
ACTIVATE = "{3}{G}{G}: Monstrosity X, where X is the number of counters among creatures you control."


class SourcePowerMonstrosityCompilerTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text):
        from test_bound_effect_programs import record

        return compile_oracle_card(
            record(text, "Creature — Human"),
            capability_registry=self.registry, capability_profile=PROFILE,
        )

    def test_exact_shapes_reject_unrelated_quantities_prices_and_targets(self):
        text = TRIGGER + "\n" + ACTIVATE
        compiled = self.compile(text)
        self.assertEqual("exact", compiled.status, compiled.material_residuals)
        for original, changed in (
            ("this creature's power", "an opponent's life total"),
            ("another target creature you control", "target creature your opponent controls"),
            ("the number of counters among creatures you control", "the number of counters among all creatures"),
            ("{3}{G}{G}", "{G}"),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", self.compile(text.replace(original, changed)).status)

    def test_omitting_either_scoped_lowering_is_detected(self):
        text = TRIGGER + "\n" + ACTIVATE
        for name in (
            "quorune.counter_placement_sets.source_power_combat_target_counter_node_capabilities",
            "quorune.compiler.self_counter_keyword_actions.controlled_counter_monstrosity_activation",
        ):
            with self.subTest(owner=name), patch(name, return_value=()):
                self.assertNotEqual("exact", self.compile(text).status)


class SourcePowerMonstrosityNativeTests(InheritedNativeFixture, TestCase):
    source_name = SOURCE
    body_name = "Generic Bound Body"
    spark_name = "Generic Counter Spark"
    bounce_name = "Generic Counter Rebuff"
    artifact_name = "Generic Counter Lantern"
    green_land_name = "Generic Counter Forest"
    blue_land_name = "Generic Counter Island"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "source-power-monstrosity.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "source-power-monstrosity-witness-cards.json",
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Counter quantity witnesses", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def begin(self, game, source, *, mana=None):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def submit(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def pick_combat_target(self, game, source, target):
        self.until(game, lambda g: g.state.pending_decision.kind == "semantic.target")
        self.assertEqual("beginning_combat", game.state.step)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual([target.ref], decision["ctx"]["target_schema"]["legal_refs"])
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        command = {"action_id": "choose", "targets": [target.ref]}
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "targets": [source.ref]})
        self.submit(game, "A", command)

    def activate(self, game, source, *, check_cost=False):
        self.priority(game, "A")
        live = game.state.cards[source.object_id]
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (a for a in rows if a["id"].startswith("activate:" + live.ref + ":"))
        command = {"action_id": action["id"], "pay": "manual", "payment": {"C": 3, "G": 2}}
        if check_cost:
            self.reject(game, "C", command)
            self.reject(game, "A", {**command, "payment": {"C": 3, "G": 1}})
        self.submit(game, "A", command)

    def test_combat_targets_another_controlled_creature_and_uses_live_or_departed_power(self):
        for mode in ("live", "departed", "stale_recipient", "zero"):
            with self.subTest(mode=mode):
                game = self.session(81013100 + len(mode))
                source = self.add(game.engine, self.source_name)
                recipient = self.add(game.engine, self.body_name)
                self.add(game.engine, self.body_name, seat="B", ref="counter-witness-opponent")
                spark = self.add(game.engine, self.spark_name, zone="hand")
                bounce = self.add(game.engine, self.bounce_name, seat="B", zone="hand")
                self.add(game.engine, self.green_land_name)
                self.add(game.engine, self.blue_land_name, seat="B")
                source.counters.update({"-1/-1": 1} if mode == "zero" else {"+1/+1": 1})
                self.begin(game, source)
                self.pick_combat_target(game, source, recipient)
                game = self.replay(game, load=True)
                if mode != "zero":
                    self.cast(game, "A", spark, [source.ref])
                    self.resolve_top(game)
                if mode in ("departed", "stale_recipient"):
                    bounced = source if mode == "departed" else recipient
                    self.cast(game, "B", bounce, [bounced.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[bounced.object_id].zone)
                self.assertIsNone(self.resolve(game))
                amount = game.state.cards[recipient.object_id].counters.get("+1/+1", 0)
                self.assertEqual(3 if mode in ("live", "departed") else 0, amount)
                self.replay(game, load=True)

    def test_monstrosity_reads_live_counter_total_and_cannot_designate_twice(self):
        game = self.session(81013111)
        source = self.add(game.engine, self.source_name)
        ally = self.add(game.engine, self.body_name)
        self.add(game.engine, self.body_name, seat="B", ref="other-monstrosity-controller").counters["charge"] = 20
        self.add(game.engine, self.artifact_name).counters["charge"] = 20
        spark = self.add(game.engine, self.spark_name, zone="hand")
        source.counters.update({"+1/+1": 2, "charge": 1})
        ally.counters["oil"] = 3
        self.begin(game, source, mana={"C": 6, "G": 5})
        self.activate(game, source, check_cost=True)
        mana = game.state.players["A"].mana_pool
        self.assertEqual((3, 3, 6), (mana["C"], mana["G"], sum(mana.values())))
        self.cast(game, "A", spark, [ally.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[source.object_id]
        self.assertEqual((7, 9), (live.monstrous_value, live.counters["+1/+1"]))
        self.activate(game, live)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((7, 9), (live.monstrous_value, live.counters["+1/+1"]))
        self.replay(game, load=True)

    def test_zero_monstrosity_and_stale_source_use_the_existing_designation_owner(self):
        for bounce_before_resolution in (False, True):
            with self.subTest(bounce=bounce_before_resolution):
                game = self.session(81013121 + bounce_before_resolution)
                source = self.add(game.engine, self.source_name)
                bounce = self.add(game.engine, self.bounce_name, seat="B", zone="hand")
                game.state.players["B"].mana_pool["U"] = 1
                self.begin(game, source, mana={"C": 3, "G": 2})
                self.activate(game, source)
                if bounce_before_resolution:
                    self.cast(game, "B", bounce, [source.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                live = game.state.cards[source.object_id]
                self.assertEqual(None if bounce_before_resolution else 0, live.monstrous_value)
                self.assertEqual(0, live.counters.get("+1/+1", 0))
                self.replay(game, load=True)

    def test_both_runtime_envelopes_independently_require_their_authority(self):
        game = self.session(81013131)
        source = self.add(game.engine, self.source_name)
        self.assert_closed(game, source)
        programs = game.engine.semantics.programs_for_oracle(source.oracle_id)
        for capability in (SOURCE_POWER_COMBAT_TARGET_COUNTER_CAPABILITY, CONTROLLED_COUNTER_MONSTROSITY_CAPABILITY):
            with self.subTest(capability=capability):
                program, = (p for p in programs if capability in p.capability_dependencies)
                remaining = [name for name in program.capability_dependencies if name != capability]
                altered = replace(program, capability_dependencies=remaining,
                                  capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"])
                self.assertIn("capability:undeclared_runtime_dependency:" + capability, binding["blockers"])
