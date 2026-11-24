"""Native fixed sacrifice and conjunctive direct control target evidence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Ritual of the Machine"


class NonartifactNonblackCreatureTargetsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "nonartifact-nonblack-creature-targets.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/nonartifact-nonblack-creature-targets-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Fixed sacrifice and qualified control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        original = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(original.oracle_id)

    def program(self, name=ORIGINAL, *, event="resolve"):
        program, = (program for program in self.programs(name) if program.event == event)
        return program

    def assert_qualified_schema(self, schema):
        spec = DirectPermanentTargetSpec.from_target_schema(schema)
        self.assertIn("creature", (*spec.types_all, *spec.types_any))
        self.assertEqual(("artifact",), spec.types_none)
        self.assertEqual(("B",), spec.colors_none)
        return spec

    def test_complete_originals_retain_both_target_exclusions_and_their_siblings(self):
        program = self.program()
        spec = self.assert_qualified_schema(program.target_schema)
        self.assertIsNotNone(program.cost_schema)
        effect, = program.effects
        self.assertEqual(("gain_control", "$target.0", "zone_object"),
                         (effect["op"], effect["card"], effect["duration"]))
        for schema in (
            None,
            {key: value for key, value in program.target_schema.items() if key != "types_none"},
            {key: value for key, value in program.target_schema.items() if key != "colors_none"},
        ):
            binding = bind_semantic_program_runtime(replace(program, target_schema=schema),
                capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)
        for name in ("Terror", "Expunge", "Nekrataal", "Bone Shredder", "Shriekmaw"):
            event = "resolve" if name in ("Terror", "Expunge") else "permanent.enter.self"
            destruction = self.program(name, event=event)
            self.assertEqual(spec, self.assert_qualified_schema(destruction.target_schema))
            effect, = destruction.effects
            self.assertEqual("destroy", effect["op"])
            self.assertEqual(name in ("Terror", "Expunge", "Nekrataal"), effect.get("regeneration_prohibited", False))
        self.assertIn("activate", {program.event for program in self.programs("Expunge")})
        self.assertIn("step.begin", {program.event for program in self.programs("Bone Shredder")})
        self.assertIn("cast.cost", {program.event for program in self.programs("Shriekmaw")})
        self.program("Feast or Famine")

    def scene(self, seed, *, response=False):
        game = self.session(seed)
        spell = self.add(game.engine, ORIGINAL, zone="hand")
        paid = self.add(game.engine, "Grizzly Bears", ref="ritual-sacrifice")
        ordinary = self.add(game.engine, "Grizzly Bears", seat="B")
        artifact = self.add(game.engine, "Ornithopter", seat="B")
        black = self.add(game.engine, "Vampire Hexmage", seat="B")
        coating = self.add(game.engine, "Liquimetal Coating", seat="B") if response else None
        for card in (spell, paid, ordinary, artifact, black, coating):
            if card is not None:
                self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 2})
        self.checkpoint(game)
        return game, spell, paid, ordinary, artifact, black, coating

    def cast_proposal(self, game, spell, target, paid):
        offer = self.action(game, "A", spell)
        return {"action_id": offer["id"], "targets": [target.ref], "sacrifice_cards": [paid.ref], "pay": "auto"}

    def test_actual_offer_and_command_exclude_each_quality_before_any_cost_is_paid(self):
        game, spell, paid, ordinary, artifact, black, _ = self.scene(60120811)
        offer = self.action(game, "A", spell)
        legal = offer["target_schema"]["legal_refs"]
        self.assertIn(ordinary.ref, legal)
        for invalid in (artifact, black):
            self.assertNotIn(invalid.ref, legal)
            before = authoritative_state_hash(game.state)
            rejected = game.act("pilot:A", self.cast_proposal(game, spell, invalid, paid))
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        cast = game.act("pilot:A", self.cast_proposal(game, spell, ordinary, paid))
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        current = game.state.cards[ordinary.object_id]
        self.assertEqual(("B", "A", "battlefield"), (current.owner, current.controller, current.zone))
        self.until(game, lambda state: state.state.active_player != "A")
        self.assertEqual("A", game.state.cards[ordinary.object_id].controller)
        self.replay(game, load=True)

    def test_actual_response_or_target_sacrifice_leaves_no_legal_target_after_payment(self):
        for method in ("artifact", "same_creature"):
            with self.subTest(method=method):
                game, spell, paid, ordinary, _, _, coating = self.scene(60120821 + len(method), response=method == "artifact")
                target = ordinary if coating is not None else paid
                cast = game.act("pilot:A", self.cast_proposal(game, spell, target, paid))
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
                if coating is not None:
                    self.priority(game, "B")
                    rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                    action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{coating.ref}:")]
                    applied = game.act("pilot:B", {"action_id": action, "targets": [target.ref], "pay": "auto"})
                    self.assertTrue(applied.ok, applied.summary)
                    self.resolve_top(game)
                    self.assertIn("artifact", game.engine._effective_card_data(game.state.cards[target.object_id])[
                        "type_line"].casefold())
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("B", game.state.cards[ordinary.object_id].controller)
                self.assertEqual("graveyard", game.state.cards[paid.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.replay(game, load=True)


    def test_actual_removal_spells_use_the_same_target_scope_and_prohibit_regeneration(self):
        for name, cost in (("Terror", 1), ("Expunge", 2)):
            with self.subTest(name=name):
                game = self.session(70170051 + cost)
                spell = self.add(game.engine, name, zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                artifact = self.add(game.engine, "Ornithopter", seat="B")
                black = self.add(game.engine, "Vampire Hexmage", seat="B")
                mending = self.add(game.engine, "Mending Touch", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                for card in (spell, target, artifact, black, mending):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": cost, "B": 1})
                self.checkpoint(game)
                legal = self.action(game, "A", spell)["target_schema"]["legal_refs"]
                self.assertIn(target.ref, legal)
                self.assertNotIn(artifact.ref, legal)
                self.assertNotIn(black.ref, legal)
                self.cast_for(game, "A", spell, [target.ref])
                self.cast_for(game, "B", mending, [target.ref])
                self.resolve_top(game)
                self.assertEqual(1, game.state.cards[target.object_id].regeneration_shields)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.replay(game, load=True)

    def test_actual_expunge_retains_its_printed_cycling(self):
        game = self.session(70229061)
        source = self.add(game.engine, "Expunge", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row["id"] for row in rows if row["id"].startswith(f"activate:{source.ref}:")]
        before = len(game.state.players["A"].zones["hand"])
        applied = game.act("pilot:A", {"action_id": action, "pay": "auto"})
        self.assertTrue(applied.ok, applied.summary)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(before, len(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_entry_triggers_keep_both_exclusions_and_the_evoke_cost(self):
        cases = (
            ("Bone Shredder", {"C": 2, "B": 1}),
            ("Nekrataal", {"C": 2, "B": 2}),
            ("Shriekmaw", {"C": 1, "B": 1}),
        )
        for index, (name, mana) in enumerate(cases):
            with self.subTest(name=name):
                game = self.session(60330071 + index)
                source = self.add(game.engine, name, zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                artifact = self.add(game.engine, "Ornithopter", seat="B")
                black = self.add(game.engine, "Vampire Hexmage", seat="B")
                mending = None
                if name == "Nekrataal":
                    mending = self.add(game.engine, "Mending Touch", seat="B", zone="hand")
                    self.add(game.engine, "Forest", seat="B")
                for card in (source, target, artifact, black, mending):
                    if card is not None:
                        self.assert_closed(game, card)
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                offer = self.action(game, "A", source)
                command = {"action_id": offer["id"], "targets": [], "pay": "auto"}
                if name == "Shriekmaw":
                    command["cost_option"] = "evoke"
                cast = game.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.resolve_top(game)
                if game.state.pending_decision.kind == "trigger.order":
                    rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["triggers"]
                    order = game.act("pilot:A", {"action_id": "order", "triggers": [row["id"] for row in rows]})
                    self.assertTrue(order.ok, order.summary)
                for invalid in (artifact, black):
                    before = authoritative_state_hash(game.state)
                    rejected = game.act("pilot:A", {"action_id": "choose", "targets": [invalid.ref]})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                self.target(game, "A", [target.ref])
                if mending is not None:
                    self.cast_for(game, "B", mending, [target.ref])
                    self.resolve_top(game)
                    self.assertEqual(1, game.state.cards[target.object_id].regeneration_shields)
                game = self.replay(game, load=True)
                while game.state.stack:
                    self.resolve_top(game)
                self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
                self.assertEqual("graveyard" if name == "Shriekmaw" else "battlefield",
                                 game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_actual_feast_or_famine_keeps_both_original_modes(self):
        for mode in ("mode_1", "mode_2"):
            with self.subTest(mode=mode):
                game = self.session(70020071 + int(mode[-1]))
                source = self.add(game.engine, "Feast or Famine", zone="hand")
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                self.assert_closed(game, source)
                self.assert_closed(game, target)
                self.prepare(game, mana={"C": 3, "B": 1})
                self.checkpoint(game)
                offer = self.action(game, "A", source)
                accepted = game.act("pilot:A", {
                    "action_id": offer["id"], "modes": [mode],
                    "targets": [] if mode == "mode_1" else [target.ref], "pay": "auto",
                })
                self.assertTrue(accepted.ok, accepted.summary)
                game = self.replay(game, load=True)
                self.resolve_top(game)
                tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(int(mode == "mode_1"), len(tokens))
                self.assertEqual("battlefield" if mode == "mode_1" else "graveyard",
                                 game.state.cards[target.object_id].zone)
                if tokens:
                    self.assertIn("zombie", game.engine._effective_card_data(tokens[0])["type_line"].casefold())
                self.replay(game, load=True)
