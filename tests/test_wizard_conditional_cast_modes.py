"""Native casting, independent targets and replay for Wizard-conditioned modes."""

from copy import deepcopy
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
from quorune.rules.modal_selection import WIZARD_CAST_TWO_MODES_CAPABILITY
from scripts.build_test_database import build_fixture_database


class WizardConditionalCastModesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "wizard-modes.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/wizard-conditional-cast-mode-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Wizard mode condition", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def test_actual_card_keeps_three_closed_targets_and_versioned_wizard_admission(self):
        card = self.db.lookup("Flame of Anor", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        schema = node.target_schema
        self.assertEqual((1, 1, 2), tuple(schema[name] for name in ("mode_count", "min_modes", "max_modes")))
        self.assertEqual({"condition": "controls_wizard", "schema_version": 2}, schema["cast_mode_limit"])
        self.assertIn(WIZARD_CAST_TWO_MODES_CAPABILITY, node.capability_dependencies)
        modes = list(schema["modes"].values())
        self.assertEqual(["draw", "destroy", "damage"], [mode["effects"][0]["op"] for mode in modes])
        self.assertEqual(2, modes[0]["effects"][0]["count"])
        self.assertEqual(5, modes[2]["effects"][0]["amount"])
        self.assertEqual(["mode_1_target_1", "mode_2_target_1", "mode_3_target_1"],
                         [mode["groups"][0]["id"] for mode in modes])
        for old, new in (
            ("a Wizard", "a Shaman"),
            ("as you cast this spell", "as this spell resolves"),
            ("may choose two", "may choose three"),
        ):
            with self.subTest(changed=old):
                self.assertNotEqual("exact", self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)

        game = self.session(736436000)
        spell = self.add(game.engine, card.name, zone="hand")
        self.assert_closed(game, spell)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        for version in (1, True, 3):
            with self.subTest(version=version):
                changed = deepcopy(program.target_schema)
                changed["cast_mode_limit"]["schema_version"] = version
                bind = bind_semantic_program_runtime(replace(program, target_schema=changed),
                                                     capability_registry=self.registry, profile="commander_review")
                self.assertFalse(bind["strict"], bind)
        requirements = tuple(value for value in program.capability_dependencies if value != WIZARD_CAST_TWO_MODES_CAPABILITY)
        missing = replace(program, capability_dependencies=requirements,
                          capability_closure=self.registry.closure(requirements, profile="commander_review").to_dict())
        bind = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile="commander_review")
        self.assertIn("capability:undeclared_runtime_dependency:" + WIZARD_CAST_TWO_MODES_CAPABILITY, bind["blockers"])

    def scene(self, seed, *, phased=False, response=False):
        game = self.session(seed)
        spell = self.add(game.engine, "Flame of Anor", zone="hand")
        your_wizard = self.add(game.engine, "Vodalian Arcanist", ref="flame-your-wizard")
        your_wizard.phased_out = phased
        self.add(game.engine, "Vodalian Arcanist", seat="C", ref="flame-foreign-wizard")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand") if response else None
        if bounce is not None:
            self.add(game.engine, "Island", seat="C")
            self.assert_closed(game, bounce)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "U": 1, "R": 1})
        self.checkpoint(game)
        return game, spell, your_wizard, artifact, creature, bounce

    def offer(self, game, spell):
        packet = game.packet("pilot:A", full=True)["decision"]
        return next(row for row in packet["ctx"]["legal"]["actions"] if row["id"] == f"cast:{spell.ref}")

    def submit(self, game, spell, modes, targets, *, accepted=True):
        offer = self.offer(game, spell)
        before = authoritative_state_hash(game.state)
        response = game.act("pilot:A", {
            "action_id": offer["id"], "modes": modes, "targets": targets, "pay": "auto",
        })
        self.assertEqual(accepted, response.ok, response.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_foreign_and_phased_wizards_leave_a_single_mode(self):
        game, spell, _, artifact, creature, _ = self.scene(736436001, phased=True)
        schema = self.offer(game, spell)["target_schema"]
        self.assertEqual(1, schema["max_modes"])
        self.assertEqual(["mode_1", "mode_2", "mode_3"], schema["legal_modes"])
        self.submit(game, spell, ["mode_2", "mode_3"], [artifact.ref, creature.ref], accepted=False)
        self.submit(game, spell, ["mode_2"], [artifact.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.replay(game, load=True)

    def test_actual_wizard_allows_artifact_and_damage_modes_in_printed_order(self):
        game, spell, _, artifact, creature, _ = self.scene(736436002)
        schema = self.offer(game, spell)["target_schema"]
        self.assertEqual(2, schema["max_modes"])
        self.submit(game, spell, ["mode_2", "mode_2"], [artifact.ref, artifact.ref], accepted=False)
        self.submit(game, spell, ["mode_2", "mode_3"], [creature.ref, artifact.ref], accepted=False)
        self.submit(game, spell, ["mode_3", "mode_2"], [artifact.ref, creature.ref])
        game = self.replay(game, load=True)
        item, = game.state.stack
        self.assertEqual(["mode_2", "mode_3"], item.modes)
        self.assertEqual([artifact.ref, creature.ref], item.targets)
        self.resolve_top(game)
        self.assertEqual(("graveyard", "graveyard"),
                         tuple(game.state.cards[card.object_id].zone for card in (artifact, creature)))
        self.replay(game, load=True)

    def test_actual_draw_mode_survives_departure_of_the_wizard_and_damage_target(self):
        game, spell, wizard, _, _, bounce = self.scene(736436003, response=True)
        before = set(game.state.players["B"].zones["hand"])
        self.assertEqual(2, self.offer(game, spell)["target_schema"]["max_modes"])
        self.submit(game, spell, ["mode_3", "mode_1"], ["B", wizard.ref])
        item, = game.state.stack
        self.assertEqual(["mode_1", "mode_3"], item.modes)
        self.cast_for(game, "C", bounce, [wizard.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[wizard.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        draws = set(game.state.players["B"].zones["hand"]) - before
        self.assertEqual(2, len(draws))
        owner = json.dumps(game.packet("pilot:B", full=True))
        foreign = [json.dumps(game.packet("pilot:" + seat, full=True)) for seat in "ACD"]
        for object_id in draws:
            ref = game.state.cards[object_id].ref
            self.assertIn(ref, owner)
            self.assertTrue(all(ref not in packet for packet in foreign))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)
