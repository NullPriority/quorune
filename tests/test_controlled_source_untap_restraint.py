"""The original entry trigger taps and retains a controller-dependent untap restraint."""

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
from quorune.continuous_effect_model import ContinuousObjectIdentity
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_continuity import SourceContinuitySnapshot
from quorune.source_tap_effect import SOURCE_CONTROL_UNTAP_CAPABILITY, SOURCE_CONTROL_UNTAP_FIELD
from quorune.source_untap_rule_model import ResolvedSourceUntapRestraint
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
CANDIDATES = ("Dungeon Geists", "Icefall Regent", "Tidebinder Mage")


class ControlledSourceUntapRestraintTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-source-untap-restraint.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/controlled-source-untap-restraint-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Controlled-source untap restraint", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_creature_entries_keep_flying_and_the_icefall_cast_tax(self):
        for name in CANDIDATES:
            with self.subTest(card=name):
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                record = self.db.lookup(name, fuzzy=False)
                programs = semantics.programs_for_oracle(record.oracle_id)
                trigger, = (row for row in programs if row.event == "permanent.enter.self")
                self.assertEqual([{"op": "tap", "card": "$target.0", SOURCE_CONTROL_UNTAP_FIELD: 1}], trigger.effects)
                if name != "Tidebinder Mage":
                    self.assertGreater(len(programs), 1)
                if name == "Icefall Regent":
                    self.assertGreaterEqual(len(programs), 3)
                self.assertEqual("opponent", trigger.target_schema["controller_relation"])
                self.assertEqual(("G", "R") if name == "Tidebinder Mage" else (),
                                 tuple(trigger.target_schema.get("colors_any", ())))
                dependencies = [cap for cap in trigger.capability_dependencies if cap != SOURCE_CONTROL_UNTAP_CAPABILITY]
                omitted = replace(trigger, capability_dependencies=dependencies,
                                  capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
                self.assertIn(f"capability:undeclared_runtime_dependency:{SOURCE_CONTROL_UNTAP_CAPABILITY}",
                              binding["blockers"])
                for changed in (
                    replace(trigger, event="permanent.enter"),
                    replace(trigger, target_schema={**trigger.target_schema, "colors_any": ["B"]}),
                    replace(trigger, effects=[{**trigger.effects[0], SOURCE_CONTROL_UNTAP_FIELD: True}]),
                ):
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile=PROFILE,
                    )["strict"])

    def test_missing_compiler_rule_or_opened_untap_scope_cannot_admit_the_pinned_mage(self):
        record = self.db.lookup("Tidebinder Mage", fuzzy=False)

        def status(card):
            return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE).status

        self.assertEqual("exact", status(record))
        with patch("quorune.compiler.effect_template_composition.controlled_source_untap_restraint_template",
                   return_value=None):
            self.assertNotEqual("exact", status(record))
        for oracle in (
            record.oracle_text.replace("red or green", "red or blue"),
            record.oracle_text.replace("you control this creature", "this creature remains untapped"),
            record.oracle_text.replace("target red", "up to one target red"),
            record.oracle_text + " Return it to its owner's hand.",
        ):
            self.assertNotEqual("exact", status(replace(record, oracle_text=oracle)))

    def test_source_untap_journal_retains_the_legacy_shape_and_rejects_irrelevant_v2_fields(self):
        effect = ResolvedSourceUntapRestraint(
            "restraint", 2, ContinuousObjectIdentity("source", "original"),
            SourceContinuitySnapshot(1, 0, 0), (ContinuousObjectIdentity("subject", "target"),),
        )
        legacy = effect.to_dict()
        current = replace(effect, duration_controller="A")
        wire = current.to_dict()
        self.assertEqual(1, legacy["schema_version"])
        self.assertNotIn("duration_controller", legacy)
        self.assertEqual(2, wire["schema_version"])
        self.assertEqual("A", wire["duration_controller"])
        self.assertEqual(effect, ResolvedSourceUntapRestraint.from_dict(legacy))
        self.assertEqual(current, ResolvedSourceUntapRestraint.from_dict(wire))
        for malformed in ({**wire, "duration_controller": None}, {**wire, "schema_version": True},
                          {**legacy, "duration_controller": "A"}, {**wire, "locked_objects": []}):
            with self.assertRaises(ValueError):
                ResolvedSourceUntapRestraint.from_dict(malformed)

    def restraint(self, game, recipient):
        current = self.current(game, recipient)
        return [effect for effect in game.state.continuous_effects
                if isinstance(effect, ResolvedSourceUntapRestraint)
                and ContinuousObjectIdentity(current.object_id, current.logical_object_id) in effect.locked_objects]

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def upkeep(self, game, seat):
        self.until(game, lambda active: active.state.active_player == seat and active.state.step == "upkeep")

    def twiddle(self, game, spell, actor, card, mode):
        self.cast_for(game, actor, spell, [card.ref])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", [actor]), (pending.kind, pending.actors))
        answer = game.act("pilot:" + actor, {"action_id": "choose", "choice": mode})
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_mage_rejects_a_blue_target_and_restrains_an_already_tapped_red_creature(self):
        game = self.playing(61126201)
        source = self.add(game.engine, "Tidebinder Mage", zone="hand")
        red = self.add(game.engine, "Raging Goblin", seat="B")
        blue = self.add(game.engine, "Wind Drake", seat="B")
        own = self.add(game.engine, "Grizzly Bears")
        manual = self.add(game.engine, "Twiddle", zone="hand", ref="untap-recipient")
        retap = self.add(game.engine, "Twiddle", zone="hand", ref="retap-recipient")
        for card in (source, red, blue, own, manual, retap):
            self.assert_closed(game, card)
        red.tapped = True
        blue.tapped = True
        self.prepare(game, actor="A", active="A", mana={"U": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 2})
        self.until(game, lambda value: value.state.pending_decision.kind == "semantic.target")
        for target in (blue, own):
            before = authoritative_state_hash(game.state)
            refused = game.act("pilot:A", {"action_id": "choose", "targets": [target.ref]})
            self.assertFalse(refused.ok, refused.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.target(game, "A", [red.ref])
        self.assertIsNone(self.resolve(game))
        effect, = self.restraint(game, red)
        self.assertEqual("A", effect.duration_controller)
        self.assertFalse(self.current(game, source).tapped)
        self.twiddle(game, manual, "A", red, "untap")
        self.assertFalse(self.current(game, red).tapped)
        self.twiddle(game, retap, "A", red, "tap")
        self.upkeep(game, "B")
        self.assertTrue(self.current(game, red).tapped)
        self.assertFalse(self.current(game, blue).tapped)
        self.replay(game, load=True)

    def test_actual_geists_still_taps_after_control_is_lost_before_resolution(self):
        game = self.playing(61126202)
        source = self.add(game.engine, "Dungeon Geists", zone="hand")
        victim = self.add(game.engine, "Wind Drake", seat="B")
        theft = self.add(game.engine, "Control Magic", seat="B", zone="hand")
        permission = self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (source, victim, theft, permission):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 4})
        game.state.players["B"].mana_pool.update({"U": 4})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.target(game, "A", [victim.ref])
        self.cast_for(game, "B", theft, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", self.current(game, source).controller)
        self.assertTrue(self.current(game, victim).tapped)
        self.assertEqual([], self.restraint(game, victim))
        self.upkeep(game, "B")
        self.assertFalse(self.current(game, victim).tapped)
        self.replay(game, load=True)

    def test_actual_icefall_follows_the_subject_and_does_not_resume_after_source_control_returns(self):
        game = self.playing(61126203)
        source = self.add(game.engine, "Icefall Regent", zone="hand")
        victim = self.add(game.engine, "Wind Drake", seat="B")
        receive = self.add(game.engine, "Control Magic", zone="hand", ref="receive-restrained-creature")
        theft = self.add(game.engine, "Control Magic", seat="B", zone="hand")
        release = self.add(game.engine, "Disenchant", zone="hand", ref="release-stolen-source")
        permission = self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (source, victim, receive, theft, release, permission):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"U": 10, "W": 1})
        game.state.players["B"].mana_pool.update({"U": 6})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 5, "W": 1})
        self.target(game, "A", [victim.ref])
        self.assertIsNone(self.resolve(game))
        self.cast_for(game, "A", receive, [victim.ref], reserve={"U": 1, "W": 1})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", self.current(game, victim).controller)
        self.assertEqual(1, len(self.restraint(game, victim)))

        retained = self.replay(game, load=True)
        for seat in ("B", "C", "D", "A"):
            self.upkeep(retained, seat)
        self.assertTrue(self.current(retained, victim).tapped)
        self.replay(retained, load=True)

        self.cast_for(game, "B", theft, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.players["B"].mana_pool.get("U", 0))
        self.assertEqual("B", self.current(game, source).controller)
        self.cast_for(game, "A", release, [theft.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", self.current(game, source).controller)
        self.assertEqual([], self.restraint(game, victim))
        for seat in ("B", "C", "D", "A"):
            self.upkeep(game, seat)
        self.assertFalse(self.current(game, victim).tapped)
        self.replay(game, load=True)
