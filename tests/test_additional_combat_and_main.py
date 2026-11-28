from dataclasses import replace
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.main_phase_combat_model import (
    INSERTED_MAIN_COUNT_FIELD, LATER_MAIN_PHASE_FIELD, MAIN_COMBAT_FIELD, MainPhaseCombatInstruction,
    MainPhaseCombatSchedule, main_phase_combat_effect, main_phase_combat_instruction,
)
from quorune.rules.main_phase_combats import IMMEDIATE_COMBAT_DEPENDENCIES, main_phase_combat_node_capabilities
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
from test_granted_skulk import record


CARD = "Relentless Assault"
PROFILE = "commander_review"
PAIR = ("combat", "postcombat_main")


class AdditionalCombatAndMainTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        support = {
            "additional-combat-and-main-cards.json": {CARD, "Cautious Survivor"},
            "ability-source-counter-cards.json": {"Vedalken Orrery"},
            "activation-restriction-material-cards.json": {"Cloudshift", "Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "external-entry-tapped-cards.json": {"Thalia, Heretic Cathar"},
            "per-opponent-attacking-token-cards.json": {"Adeline, Resplendent Cathar"},
            "self-public-zone-cards.json": {"Mountain"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = []
        for filename, names in support.items():
            document = json.loads((fixtures / filename).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert {card["name"] for card in document["cards"]} == names, filename
            oracle_ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [r for r in document.get("rulings", ()) if r["oracle_id"] in oracle_ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            pieces.append(piece)
        path = folder / "additional-combat-main.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Additional combat and main", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def test_exact_spell_shape_keeps_legacy_recipe_and_rejects_forged_counts(self):
        card = self.db.lookup(CARD, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        effect, = program.effects
        instruction = MainPhaseCombatInstruction.COMBAT_THEN_MAIN_IMMEDIATE_UNTAP
        self.assertIs(instruction, main_phase_combat_instruction(effect))
        self.assertEqual(PAIR, instruction.phases)
        self.assertIsNone(program.target_schema)
        self.assertEqual(set(IMMEDIATE_COMBAT_DEPENDENCIES), set(program.capability_dependencies))
        self.assertEqual(({"op": "delayed_trigger", MAIN_COMBAT_FIELD: 1}, ("combat", "combat")),
                         (main_phase_combat_effect(), MainPhaseCombatInstruction.TWO_COMBATS_DELAYED_UNTAP.phases))
        for wrong in (0, -1, 3, True, 2.0, "2"):
            with self.subTest(wrong=wrong):
                self.assertEqual((), main_phase_combat_node_capabilities(({**effect, MAIN_COMBAT_FIELD: wrong},), None, None))
        for missing in IMMEDIATE_COMBAT_DEPENDENCIES:
            with self.subTest(missing=missing):
                deps = tuple(value for value in program.capability_dependencies if value != missing)
                altered = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                witness = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(witness["strict"], witness)
        for old, new in (("all creatures", "all creatures you control"), ("this turn", "this combat"),
                         ("this main phase", "this combat phase"), ("by an additional main phase", "by two additional main phases")):
            with self.subTest(unsupported=new):
                ir = compile_oracle_card(record(card.oracle_text.replace(old, new), "Sorcery"), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.main_phase_combats._IMMEDIATE", re.compile(r"(?!)")):
            self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=self.registry).status)
        raw = self.session(1594000).state.to_dict()
        self.assertNotIn(INSERTED_MAIN_COUNT_FIELD, raw)
        for wrong in (False, 0, -1, 1.0):
            with self.subTest(count=wrong), self.assertRaises(ValueError):
                GameState.from_dict({**raw, INSERTED_MAIN_COUNT_FIELD: wrong})
        active = MainPhaseCombatSchedule("precombat_main", PAIR).begin_next().begin_next()
        self.assertEqual(active, MainPhaseCombatSchedule.from_dict(active.to_dict()))
        beginning = active.prepend("beginning").begin_next()
        self.assertEqual("beginning", beginning.active_phase)
        self.assertEqual(beginning, MainPhaseCombatSchedule.from_dict(beginning.to_dict()))
        for pending, reached in ((("postcombat_main",), False), (("combat",), True)):
            with self.subTest(pending=pending, reached=reached), self.assertRaises(ValueError):
                MainPhaseCombatSchedule("precombat_main", pending, reached_inserted_main=reached)
        for printed, later in (("your second main phase", True), ("your postcombat main phase", False)):
            ir = compile_oracle_card(record(f"At the beginning of {printed}, draw a card.", "Enchantment"),
                                     capability_registry=self.registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            clauses = ir.faces[0].nodes[0].event_condition["all"]
            self.assertEqual(later, {"field": LATER_MAIN_PHASE_FIELD, "op": "ne", "value": True} in clauses)

    def start(self, game, *cards, mana=None):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def end_combat(self, game):
        self.until(game, lambda s: (s.state.phase, s.state.step) == ("combat", "end_combat"))

    def ordinary_or_added_main(self, game):
        self.until(game, lambda s: (s.state.phase, s.state.step) == ("postcombat_main", "main"))

    def finish_empty_combat(self, game):
        self.attack(game, [])
        self.end_combat(game)

    def combat_start(self, game):
        self.until(game, lambda s: (s.state.phase, s.state.step) == ("combat", "beginning_combat"))
        return game.state.combat.damage_sequence_id

    def test_actual_opponent_spell_untaps_current_attackers_and_adds_pair_only_in_main(self):
        for in_main in (False, True):
            with self.subTest(in_main=in_main):
                game = self.session(1594001 + in_main)
                spell = self.add(game.engine, CARD, seat="B", zone="hand")
                orrery = self.add(game.engine, "Vedalken Orrery", seat="B")
                thalia = self.add(game.engine, "Thalia, Heretic Cathar", seat="B")
                blink = self.add(game.engine, "Cloudshift", zone="hand")
                self.add(game.engine, "Generic Bound Plains")
                for index in range(4):
                    self.add(game.engine, "Mountain", seat="B", ref=f"assault-mountain-{index}")
                adeline = self.add(game.engine, "Adeline, Resplendent Cathar")
                staying = self.add(game.engine, "Grizzly Bears", ref="declared-stays")
                departing = self.add(game.engine, "Grizzly Bears", ref="declared-blinks")
                nonattacker = self.add(game.engine, "Grizzly Bears", seat="B", ref="caster-nonattacker")
                nonattacker.tapped = True
                staying.acquired_control_turn_count = departing.acquired_control_turn_count = -1
                self.start(game, spell, orrery, thalia, blink, adeline)
                self.attack(game, [(staying, "B"), (departing, "C")])
                first_combat = game.state.combat.damage_sequence_id
                self.assertIsNone(self.resolve(game))
                self.end_combat(game)
                tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
                self.assertEqual(3, len(tokens))
                original = game.state.cards[departing.object_id].logical_object_id
                self.cast_for(game, "A", blink, [departing.ref])
                self.assertIsNone(self.resolve(game))
                self.assertNotEqual(original, game.state.cards[departing.object_id].logical_object_id)
                self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (staying, departing, nonattacker, *tokens)))
                if in_main:
                    self.ordinary_or_added_main(game)
                turn = game.state.current_turn.turn_id
                self.cast_for(game, "B", spell)
                self.assertEqual([], game.state.stack[-1].targets)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.cards[staying.object_id].tapped)
                self.assertTrue(all(game.state.cards[card.object_id].tapped for card in (departing, nonattacker, *tokens)))
                self.assertFalse(game.state.delayed_triggers)
                self.assertEqual(("A", turn), (game.state.active_player, game.state.current_turn.turn_id))
                if in_main:
                    self.assertEqual(MainPhaseCombatSchedule("postcombat_main", PAIR), game.state.main_phase_combat_schedule)
                    game = self.replay(game, load=True)
                    self.assertNotEqual(first_combat, self.combat_start(game))
                    self.attack(game, [(staying, "B")])
                    self.assertIsNone(self.resolve(game))
                    self.end_combat(game)
                    self.ordinary_or_added_main(game)
                    self.assertEqual(1, game.state.inserted_main_phase_count)
                    self.assertTrue(game.state.cards[staying.object_id].tapped)
                    self.until(game, lambda s: s.state.step == "end_step")
                else:
                    self.ordinary_or_added_main(game)
                    self.assertEqual(0, game.state.inserted_main_phase_count)
                self.assertIsNone(game.state.main_phase_combat_schedule)
                self.assertEqual(("A", turn), (game.state.active_player, game.state.current_turn.turn_id))
                self.replay(game, load=True)

    def test_actual_inserted_main_supports_another_sorcery_and_only_second_main_triggers(self):
        game = self.session(1594004)
        first = self.add(game.engine, CARD, zone="hand")
        second = self.add(game.engine, CARD, zone="hand", ref="assault-cast-in-added-main")
        survivor = self.add(game.engine, "Cautious Survivor")
        survivor.tapped = True
        eligible = self.add(game.engine, "Grizzly Bears", ref="unused-legal-attacker")
        eligible.acquired_control_turn_count = -1
        for index in range(4):
            self.add(game.engine, "Mountain", ref=f"next-assault-mountain-{index}")
        self.start(game, first, second, survivor, mana={"C": 2, "R": 2})
        turn = game.state.current_turn.turn_id
        self.cast_for(game, "A", first)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(MainPhaseCombatSchedule("precombat_main", PAIR), game.state.main_phase_combat_schedule)
        first_combat = self.combat_start(game)
        self.finish_empty_combat(game)
        self.ordinary_or_added_main(game)
        self.assertEqual(1, game.state.inserted_main_phase_count)
        self.assertEqual(1, self.trigger_count(game, survivor))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(42, game.state.players["A"].life)
        self.cast_for(game, "A", second)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(PAIR, game.state.main_phase_combat_schedule.pending)
        game = self.replay(game, load=True)
        second_combat = self.combat_start(game)
        self.finish_empty_combat(game)
        self.ordinary_or_added_main(game)
        self.assertEqual(2, game.state.inserted_main_phase_count)
        self.assertEqual(0, self.trigger_count(game, survivor))
        ordinary_combat = self.combat_start(game)
        self.assertEqual(3, len({first_combat, second_combat, ordinary_combat}))
        self.finish_empty_combat(game)
        self.ordinary_or_added_main(game)
        self.assertEqual(0, self.trigger_count(game, survivor))
        self.assertEqual(42, game.state.players["A"].life)
        self.assertIsNone(game.state.main_phase_combat_schedule)
        self.assertEqual(("A", turn), (game.state.active_player, game.state.current_turn.turn_id))
        self.replay(game, load=True)
