"""The original activated source untaps for a legal land that was nonbasic."""

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
from quorune.rules.nonbasic_land_source_untap import NONBASIC_LAND_SOURCE_UNTAP_CAPABILITY, NonbasicLandSourceUntap
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class NonbasicLandSourceUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "nonbasic-source.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/nonbasic-land-source-untap-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Nonbasic land untap", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Forest", 30),
        ], [commander])

    def ready(self, game, source, *, mana=None):
        source.acquired_control_turn_count = -1
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.prepare(game, mana=mana or {"B": 3})
        self.checkpoint(game)

    def act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + seat, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def card(self, game, card):
        return game.state.cards[card.object_id]

    def command(self, game, source, target, *, seat="A"):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row["id"] for row in actions if row["id"].startswith(f"activate:{source.ref}:"))
        return {"action_id": action, "targets": [target.ref], "pay": "auto"}

    def test_actual_nonbasic_condition_runs_after_protection_redirect_and_basic_subtype_change(self):
        for branch in ("protected", "redirected", "subtype"):
            with self.subTest(branch=branch):
                game = self.session(709061010 + len(branch))
                source = self.add(game.engine, "Helldozer")
                land = (self.add(game.engine, "Darksteel Citadel", seat="B") if branch == "protected"
                        else self.add(game.engine, "Command Tower", seat="B"))
                if branch == "redirected":
                    self.add(game.engine, "Leyline of the Void")
                if branch == "subtype":
                    self.add(game.engine, "Blood Moon", seat="C")
                self.ready(game, source)
                if branch == "subtype":
                    _, types, supertypes = game.engine._type_parts(game.engine._effective_card_data(land)["type_line"])
                    self.assertIn("mountain", types)
                    self.assertNotIn("basic", supertypes)
                self.act(game, "A", self.command(game, source, land))
                self.assertTrue(self.card(game, source).tapped)
                self.assertEqual(0, game.state.players["A"].mana_pool["B"])
                self.assertIsNone(self.resolve(game))
                expected_zone = {"protected": "battlefield", "redirected": "exile", "subtype": "graveyard"}
                self.assertEqual(expected_zone[branch], self.card(game, land).zone)
                self.assertFalse(self.card(game, source).tapped)
                self.replay(game, load=True)

    def test_basic_or_lost_original_target_leaves_the_paid_source_tapped(self):
        for lost in (False, True):
            with self.subTest(lost=lost):
                game = self.session(709061020 + lost)
                source = self.add(game.engine, "Helldozer")
                target = (self.add(game.engine, "Command Tower", seat="B") if lost else
                          self.add(game.engine, "Forest", seat="B", ref="basic-target-forest"))
                creature = self.add(game.engine, "Grizzly Bears", seat="B")
                private = self.add(game.engine, "Forest", seat="D", zone="hand", ref="nonbasic-private")
                if lost:
                    bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
                    game.state.players["B"].mana_pool["U"] = 2
                self.ready(game, source)
                command = self.command(game, source, target)
                self.reject(game, "A", {**command, "targets": [creature.ref]})
                self.reject(game, "B", command)
                self.act(game, "A", command)
                for seat in "ABC":
                    self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
                if lost:
                    self.cast(game, "B", bounce, [target.ref])
                    self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("hand" if lost else "graveyard", self.card(game, target).zone)
                self.assertTrue(self.card(game, source).tapped)
                self.replay(game, load=True)

    def test_recast_source_incarnation_is_not_untapped_by_the_original_ability(self):
        game = self.session(709061030)
        source = self.add(game.engine, "Helldozer")
        target = self.add(game.engine, "Command Tower", seat="C")
        bounce = self.add(game.engine, "Boomerang", seat="B", zone="hand")
        icy = self.add(game.engine, "Icy Manipulator", seat="B")
        self.add(game.engine, "Vedalken Orrery")
        self.assert_closed(game, icy)
        game.state.players["B"].mana_pool.update({"U": 2, "C": 1})
        self.ready(game, source, mana={"B": 6, "C": 3})
        original = source.logical_object_id
        self.act(game, "A", self.command(game, source, target))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", self.card(game, source).zone)
        self.cast(game, "A", self.card(game, source))
        self.resolve_top(game)
        self.assertNotEqual(original, self.card(game, source).logical_object_id)
        self.act(game, "B", self.command(game, icy, self.card(game, source), seat="B"))
        self.resolve_top(game)
        self.assertTrue(self.card(game, source).tapped)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.card(game, target).zone)
        self.assertTrue(self.card(game, source).tapped)
        self.replay(game, load=True)

    def test_complete_original_and_current_source_untap_authority_are_independently_required(self):
        card = self.db.lookup("Helldozer", fuzzy=False)
        for profile in self.registry.profiles:
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
            self.assertEqual((NonbasicLandSourceUntap().effect(),), tuple(node.effects))
            self.assertTrue(node.cost["tap_source"])
            self.assertEqual(3, node.cost["mana"]["B"])
        report, semantics = register_card(self.db, self.registry, "Helldozer")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        cap = NONBASIC_LAND_SOURCE_UNTAP_CAPABILITY
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if cap in p.capability_dependencies)
        deps = tuple(value for value in program.capability_dependencies if value != cap)
        missing = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        altered = replace(program, cost_schema={**program.cost_schema, "tap_source": False})
        self.assertFalse(bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)["strict"])
        for before, after in (("was nonbasic", "was basic"), ("target land", "target artifact"),
                              ("untap this creature.", "untap this creature. Draw a card.")):
            candidate = replace(card, oracle_text=card.oracle_text.replace(before, after))
            self.assertNotEqual("exact", compile_oracle_card(candidate, capability_registry=self.registry).status)
