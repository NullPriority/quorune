from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import protection_specs
from quorune.carddb import CardDatabase
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.fixed_resolution_characteristic_shapes import EACH_COLOR_PROTECTION_FIELD
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card

CARD_NAME = "Akroma's Will"
PROFILE = "commander_review"
ATTACK_KEYWORDS = {"flying", "vigilance", "double strike"}
PROTECTION_KEYWORDS = {"lifelink", "indestructible"}


class AkromaWillTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "akroma-will.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/akroma-will-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Commander creature-set grants", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def keywords(self, game, card):
        return game.engine._combat_keywords(game.state.cards[card.object_id])

    def protections(self, game, card):
        current = game.state.cards[card.object_id]
        return {fragment.quality for fragment in protection_specs(game.engine._effective_ability_fragments(current))}

    def block(self, game, assignments, *, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:B", {"action_id": "block", "blocks": assignments})
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_one_or_both_modes_resolve_on_current_creatures_and_replay(self):
        for index, modes in enumerate((["mode_1"], ["mode_2"], ["mode_2", "mode_1"])):
            with self.subTest(modes=modes):
                game = self.session(61121600 + index)
                source = self.add(game.engine, CARD_NAME, zone="hand")
                self.assert_closed(game, source)
                attacker = self.add(game.engine, "Grizzly Bears")
                attacker.acquired_control_turn_count = -1
                opposing = self.add(game.engine, "Grizzly Bears", seat="B", ref="akroma-opposing-bear")
                neutral = self.add(game.engine, "Ornithopter", seat="B")
                bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
                late = self.add(game.engine, "Grizzly Bears", zone="hand", ref="akroma-late-bear")
                wrath = (self.add(game.engine, "Wrath of God", zone="hand")
                         if "mode_2" in modes else None)
                for land_index in range(4):
                    self.add(game.engine, "Plains", ref=f"akroma-plains-{land_index}")
                both = len(modes) == 2
                if both:
                    commander = next(card for card in game.state.cards.values()
                                     if card.owner == "C" and card.is_commander)
                    game.engine.move_card(commander.object_id, "battlefield", controller="A", log=False)
                self.prepare(game, mana={"W": 3, "C": 7, "G": 1})
                game.state.players["B"].mana_pool["R"] = 1
                self.checkpoint(game)
                offer = self.action(game, "A", source)
                self.assertEqual(2 if both else 1, offer["target_schema"]["max_modes"])
                command = {"action_id": offer["id"], "modes": modes, "pay": "auto"}
                if not both:
                    before = authoritative_state_hash(game.state)
                    invalid = game.act("pilot:A", {**command, "modes": ["mode_1", "mode_2"]})
                    self.assertFalse(invalid.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                cast = game.act("pilot:A", command)
                self.assertTrue(cast.ok, cast.summary)
                self.assertEqual(sorted(modes), game.state.stack[-1].modes)
                game = self.replay(game, load=True)
                self.resolve_top(game)

                attack_mode, protect_mode = "mode_1" in modes, "mode_2" in modes
                expected = (ATTACK_KEYWORDS if attack_mode else set()) | (PROTECTION_KEYWORDS if protect_mode else set())
                self.assertEqual(expected, self.keywords(game, attacker))
                self.assertEqual(set("WUBRG") if protect_mode else set(), self.protections(game, attacker))
                self.assertEqual(set(), self.keywords(game, opposing))
                self.cast_for(game, "A", late)
                self.resolve_top(game)
                self.assertEqual(set(), self.keywords(game, late))
                self.assertEqual(set(), self.protections(game, late))

                if protect_mode:
                    self.priority(game, "B")
                    reply = self.action(game, "B", game.state.cards[bolt.object_id])
                    before = authoritative_state_hash(game.state)
                    rejected = game.act("pilot:B", {"action_id": reply["id"], "pay": "auto",
                                                     "targets": [game.state.cards[attacker.object_id].ref]})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))

                life = game.state.players["A"].life
                opponent_life = game.state.players["B"].life
                self.attack(game, [(game.state.cards[attacker.object_id], "B")])
                self.assertEqual(not attack_mode, game.state.cards[attacker.object_id].tapped)
                self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
                colorless_block = protect_mode and not attack_mode
                if colorless_block:
                    self.block(game, {opposing.ref: [attacker.ref]}, valid=False)
                    self.block(game, {neutral.ref: [attacker.ref]})
                else:
                    self.block(game, {})
                self.until(game, lambda current: current.state.phase == "postcombat_main")
                damage = 4 if attack_mode else 2
                self.assertEqual(opponent_life - (0 if colorless_block else damage), game.state.players["B"].life)
                self.assertEqual(life + (damage if protect_mode else 0), game.state.players["A"].life)
                if colorless_block:
                    self.assertEqual("graveyard", game.state.cards[neutral.object_id].zone)
                if protect_mode:
                    self.cast_for(game, "A", wrath)
                    self.resolve_top(game)
                    self.assertEqual("battlefield", game.state.cards[attacker.object_id].zone)
                    self.assertEqual("graveyard", game.state.cards[late.object_id].zone)
                game = self.replay(game, load=True)
                turn = game.state.turn_sequence
                self.until(game, lambda current: current.state.turn_sequence > turn)
                self.assertEqual(set(), self.keywords(game, attacker))
                self.assertEqual(set(), self.protections(game, attacker))
                self.replay(game, load=True)

    def test_each_color_grant_shape_and_independent_owner_fail_closed(self):
        original = self.db.lookup(CARD_NAME, fuzzy=False)
        ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, CARD_NAME)
        self.assertTrue(report["strict_capability_ready"], report)
        program, = tuple(item for item in semantics.programs_for_oracle(original.oracle_id) if item.event == "resolve")
        self.assertIn("protection.typed.debt", program.capability_dependencies)
        for change in (None, 1, False):
            modes = program.target_schema["modes"]
            protect = modes["mode_2"]
            effect, = protect["effects"]
            self.assertIs(effect[EACH_COLOR_PROTECTION_FIELD], True)
            mutated = dict(effect)
            if change is None:
                del mutated[EACH_COLOR_PROTECTION_FIELD]
            else:
                mutated[EACH_COLOR_PROTECTION_FIELD] = change
            forged = replace(program, target_schema={**program.target_schema, "modes": {
                **modes, "mode_2": {**protect, "effects": [mutated]},
            }})
            self.assertFalse(bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)["strict"])
        remaining = tuple(owner for owner in program.capability_dependencies if owner != "protection.typed.debt")
        stripped = replace(program, capability_dependencies=remaining,
                           capability_closure=self.registry.closure(remaining, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        self.assertIn("capability:undeclared_runtime_dependency:protection.typed.debt", binding["blockers"])
        broadened = replace(original, oracle_text=original.oracle_text.replace("each color", "everything"))
        self.assertNotEqual("exact", compile_oracle_card(
            broadened, capability_registry=self.registry, capability_profile=PROFILE).status)
