"""Mode-locked native exact-X targets and single 2X life events."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from test_qualified_zone_event_queries import query_record
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.announced_x_modal import (
    ANNOUNCED_X_MODAL_CAPABILITY, DEPENDENCIES, DESTROY_MODE, LIFE_MODE, bind_announced_x_modal_targets,
)
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database

CARD = "Heliod's Intervention"
PROFILE = "commander_review"
ORACLE = ("Choose one —\n"
          "• Destroy X target artifacts and/or enchantments.\n"
          "• Target player gains twice X life.")


class AnnouncedXModalTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "announced-x-modal.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / name for name in (
            "bound-effect-program-cards.json", "announced-x-modal-cards.json",
            "cast-target-copy-cards.json", "state-qualified-zone-cards.json",
            "ability-source-counter-cards.json", "counter-prohibition-cards.json",
        )], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Announced X modal", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_compiler_and_binding_require_closed_modes_and_original_x_ownership(self):
        record = replace(query_record(ORACLE), type_line="Instant", mana_cost="{X}{W}{W}")
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE, ORACLE[node.span.start:node.span.end])
        self.assertTrue(set(DEPENDENCIES).issubset(node.capability_dependencies))
        schema = node.target_schema
        zero = bind_announced_x_modal_targets(schema, 0)["modes"]
        concrete = bind_announced_x_modal_targets(schema, 3)["modes"]
        self.assertEqual(0, zero[DESTROY_MODE]["count"])
        self.assertIn("count_parameter", schema["modes"][DESTROY_MODE])
        self.assertNotIn("count", schema["modes"][DESTROY_MODE])
        self.assertEqual((3, 1), (concrete[DESTROY_MODE]["count"], concrete[LIFE_MODE]["count"]))
        self.assertEqual(("artifact", "enchantment"), tuple(concrete[DESTROY_MODE]["types_any"]))
        self.assertEqual(2, concrete[LIFE_MODE]["effects"][0]["delta"]["coefficient"])
        for old, new in (("Choose one", "Choose both"), ("Destroy X", "Destroy up to X"),
                         ("twice X", "three times X"), ("artifacts and/or enchantments", "creatures")):
            with self.subTest(new=new):
                changed = replace(record, oracle_text=ORACLE.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE).status)
        self.assertNotEqual("exact", compile_oracle_card(
            replace(record, mana_cost="{2}{W}{W}"), capability_registry=self.registry,
            capability_profile=PROFILE).status)
        admitted, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        program, = semantics.programs_for_oracle(self.db.lookup(CARD, fuzzy=False).oracle_id)
        deps = [key for key in program.capability_dependencies if key != ANNOUNCED_X_MODAL_CAPABILITY]
        unbound = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        self.assertFalse(bind_semantic_program_runtime(unbound, capability_registry=self.registry,
                                                       profile=PROFILE)["strict"])

    def start(self, game, spell, x, *support):
        for card in (spell, *support):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": x, "W": 2})
        self.checkpoint(game)

    def command(self, game, spell, mode, x, targets):
        self.priority(game, "A")
        action = self.action(game, "A", spell)
        return {"action_id": action["id"], "modes": [mode], "x": x, "targets": targets, "pay": "auto"}

    def accepted(self, game, command):
        receipt = game.act("pilot:A", command)
        self.assertTrue(receipt.ok, receipt.summary)
        item = game.state.stack[-1]
        self.assertEqual(command["modes"], item.modes)
        self.assertEqual(command["x"], item.x_value)
        return item

    def test_destroy_requires_distinct_exact_x_and_rechecks_each_named_incarnation(self):
        game = self.session(107311520)
        spell = self.add(game.engine, CARD, zone="hand")
        dual = self.add(game.engine, "Bident of Thassa", seat="B")
        enchantment = self.add(game.engine, "Solemnity", seat="B")
        invalid = self.add(game.engine, "Grizzly Bears", seat="C")
        blink = self.add(game.engine, "Flicker of Fate", seat="B", zone="hand")
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        original = enchantment.logical_object_id
        self.start(game, spell, 2, blink)
        command = self.command(game, spell, DESTROY_MODE, 2, [dual.ref, enchantment.ref])
        before = authoritative_state_hash(game.state)
        for refs in ([dual.ref], [dual.ref, dual.ref], [dual.ref, invalid.ref]):
            with self.subTest(refs=refs):
                result = game.act("pilot:A", {**command, "targets": refs})
                self.assertFalse(result.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        item = self.accepted(game, command)
        bound = item.context["target_schema_override"]["modes"][DESTROY_MODE]
        self.assertEqual(2, bound["count"])
        self.cast_for(game, "B", blink, [enchantment.ref])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[enchantment.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[dual.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[enchantment.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[invalid.object_id].zone)
        self.replay(game, load=True)

    def test_x_zero_does_not_erase_the_life_mode_player_target(self):
        for mode, targets in ((DESTROY_MODE, []), (LIFE_MODE, ["C"])):
            with self.subTest(mode=mode):
                game = self.session(107311530 + (mode == LIFE_MODE))
                spell = self.add(game.engine, CARD, zone="hand")
                artifact = self.add(game.engine, "Sol Ring", seat="B")
                self.start(game, spell, 0)
                offer = self.action(game, "A", spell)
                public = offer["cost_options"][0]["target_schema"]["mode_schemas"]
                self.assertEqual("x", public[DESTROY_MODE]["groups"][0]["count_from"])
                self.assertNotIn("count_from", public[LIFE_MODE]["groups"][0])
                command = self.command(game, spell, mode, 0, targets)
                before = authoritative_state_hash(game.state)
                wrong = game.act("pilot:A", {**command, "targets": [artifact.ref] if mode == DESTROY_MODE else []})
                self.assertFalse(wrong.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                start = len(game.state.turn_history.events)
                self.accepted(game, command)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
                self.assertEqual(40, game.state.players["C"].life)
                self.assertFalse(any(row.kind == "player_gained_life" for row in game.state.turn_history.events[start:]))
                self.replay(game, load=True)

    def test_copy_keeps_mode_and_x_and_each_player_receives_one_twice_x_event(self):
        game = self.session(707101526)
        spell = self.add(game.engine, CARD, zone="hand")
        copier = self.add(game.engine, "Twincast", seat="D", zone="hand")
        game.state.players["D"].mana_pool.update({"U": 2})
        self.start(game, spell, 3, copier)
        original = self.accepted(game, self.command(game, spell, LIFE_MODE, 3, ["B"]))
        self.cast_for(game, "D", copier, [original.ref])
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["D"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        acknowledge = game.act("pilot:D", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(acknowledge.ok, acknowledge.summary)
        copy = game.state.stack[-1]
        self.assertEqual(("spell_copy", "D", [LIFE_MODE], 3, ["C"]),
                         (copy.kind, copy.controller, copy.modes, copy.x_value, copy.targets))
        game = self.replay(game, load=True)
        start = len(game.state.turn_history.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40, 46, 46, 40], [game.state.players[seat].life for seat in "ABCD"])
        entries = [row for row in game.state.turn_history.events[start:] if row.kind == "player_gained_life"]
        self.assertEqual([("C", 6), ("B", 6)], [(row.target, row.amount) for row in entries])
        self.replay(game, load=True)
