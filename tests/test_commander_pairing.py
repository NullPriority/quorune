from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.commander import initial_commander_state
from quorune.commander_pairing import (
    COMMANDER_PAIRING_TEMPLATE_ID,
    CommanderPairingError,
    CommanderPairingKind,
    commander_pairing_declaration,
    validate_commander_pair,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import CommanderEngine
from quorune.model import GameConfig, GameState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.spell_cast_events import SpellCastEvent
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


PAIRING_FIXTURE = ROOT / "tests" / "fixtures" / "commander-pairing-cards.json"


def pairing_deck(first: str, second: str) -> DeckDefinition:
    return DeckDefinition(
        name=f"{first} and {second}",
        entries=[
            DeckEntry(first, board="commander"),
            DeckEntry(second, board="commander"),
        ],
        commanders=[first, second],
    )


class CommanderPairingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "commander-pairing.sqlite3"
        build_fixture_database(PAIRING_FIXTURE, path)
        cls.db = CardDatabase(path)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def registry_for(self, *names: str) -> SemanticRegistry:
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_capability_declarations=True,
        )
        return registry

    def test_pairing_keywords_compile_as_exact_typed_setup_declarations(self):
        expected = {
            "Thrasios, Triton Hero": CommanderPairingKind.PARTNER,
            "Wilson, Refined Grizzly": (
                CommanderPairingKind.CHOOSE_A_BACKGROUND
            ),
            "Rose Tyler": CommanderPairingKind.DOCTORS_COMPANION,
        }
        for name, kind in expected.items():
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                ]
                self.assertEqual(1, len(nodes))
                self.assertTrue(nodes[0].exact)
                self.assertEqual("game.setup", nodes[0].event)
                self.assertEqual((kind.value,), nodes[0].mechanics)

        for name in (
            "Toothy, Imaginary Friend",
            "Bjorna, Nightfall Alchemist",
        ):
            with self.subTest(excluded=name):
                ir = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(
                        node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                        for face in ir.faces
                        for node in face.nodes
                    )
                )

    def test_partner_pair_is_designated_in_four_player_public_state_and_round_trips(self):
        cases = (
            ("Thrasios, Triton Hero", "Tymna the Weaver"),
            ("Wilson, Refined Grizzly", "Flaming Fist"),
            ("Rose Tyler", "The Tenth Doctor"),
        )
        for index, names in enumerate(cases, start=1):
            with self.subTest(pair=names):
                registry = self.registry_for(*names)
                deck = pairing_deck(*names)
                state = initial_commander_state(
                    self.db,
                    {seat: deck for seat in "ABCD"},
                    first_player="A",
                    config=GameConfig(seed=702_124_000 + index),
                    semantics=registry,
                )

                for seat in state.turn_order:
                    command_cards = [
                        state.cards[object_id]
                        for object_id in state.players[seat].zones["command"]
                    ]
                    self.assertEqual(2, len(command_cards))
                    self.assertEqual(
                        2,
                        len(
                            {
                                card.commander_designation_id
                                for card in command_cards
                            }
                        ),
                    )
                    self.assertTrue(
                        all(card.is_commander for card in command_cards)
                    )
                self.assertEqual(
                    state.to_dict(),
                    GameState.from_dict(state.to_dict()).to_dict(),
                )

    def test_choose_a_background_and_doctors_companion_accept_exact_type_pairs(self):
        cases = (
            (
                "Wilson, Refined Grizzly",
                "Flaming Fist",
                CommanderPairingKind.CHOOSE_A_BACKGROUND,
            ),
            (
                "Rose Tyler",
                "The Tenth Doctor",
                CommanderPairingKind.DOCTORS_COMPANION,
            ),
        )
        for first_name, second_name, expected_kind in cases:
            with self.subTest(kind=expected_kind.value):
                registry = self.registry_for(first_name, second_name)
                first = self.db.lookup(first_name)
                second = self.db.lookup(second_name)
                forward = validate_commander_pair(
                    self.db,
                    registry,
                    (first, second),
                )
                reverse = validate_commander_pair(
                    self.db,
                    registry,
                    (second, first),
                )
                self.assertIn(
                    expected_kind,
                    {value.kind for value in forward if value is not None},
                )
                self.assertEqual(tuple(reversed(forward)), reverse)

    def test_engine_create_shares_pairing_registry_with_setup(self):
        names = ("Thrasios, Triton Hero", "Tymna the Weaver")
        registry = self.registry_for(*names)
        deck = pairing_deck(*names)

        engine = CommanderEngine.create(
            self.db,
            {"A": deck, "B": deck},
            first_player="A",
            config=GameConfig(seed=702_124_001),
            semantics=registry,
        )

        self.assertIs(registry, engine.semantics)
        self.assertTrue(
            all(
                len(engine.state.players[seat].zones["command"]) == 2
                for seat in engine.state.turn_order
            )
        )

    def test_pairing_and_activated_mill_preserve_commander_identity(self):
        names = ("Zellix, Sanity Flayer", "Flaming Fist")
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        deck = pairing_deck(*names)
        deck.entries.extend(
            DeckEntry(name)
            for name in (
                "Thrasios, Triton Hero",
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
                "Rose Tyler",
            )
        )
        state = initial_commander_state(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="A",
            config=GameConfig(seed=702_124_006),
            semantics=registry,
        )
        engine = CommanderEngine(self.db, state, registry)
        source = next(
            card
            for card in state.cards.values()
            if card.owner == "A" and card.printed_name == names[0]
        )
        designation_id = source.commander_designation_id
        source = engine.move_card(source.object_id, "battlefield", log=False)
        top_cards = tuple(
            state.cards[object_id]
            for object_id in reversed(
                state.players["B"].zones["library"][-3:]
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.players["A"].mana_pool["C"] = 1
        ability = engine._activated_abilities(source)[0]

        engine._activate(
            "A",
            {
                "source": source.ref,
                "ability": ability.ability_id,
                "targets": ["B"],
            },
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(
            ["graveyard", "graveyard", "graveyard"],
            [card.zone for card in top_cards],
        )
        self.assertTrue(source.tapped)
        self.assertTrue(source.is_commander)
        self.assertEqual(designation_id, source.commander_designation_id)
        self.assertIn(source.oracle_id, state.commander_oracle_ids["A"])
        self.assertEqual(2, len(state.players["B"].zones["command"]))
        self.assertEqual(
            state.to_dict(),
            GameState.from_dict(state.to_dict()).to_dict(),
        )

    def test_partner_commander_second_opponent_cast_draws_without_losing_identity(
        self,
    ):
        names = ("Kraum, Ludevic's Opus", "Thrasios, Triton Hero")
        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            [self.db.lookup(name) for name in names],
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        deck = pairing_deck(*names)
        deck.entries.extend(
            DeckEntry(name)
            for name in (
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
                "Rose Tyler",
                "The Tenth Doctor",
            )
        )
        state = initial_commander_state(
            self.db,
            {seat: deck for seat in "ABCD"},
            first_player="B",
            config=GameConfig(seed=702_124_007),
            semantics=registry,
        )
        engine = CommanderEngine(self.db, state, registry)
        kraum = next(
            card
            for card in state.cards.values()
            if card.owner == "A" and card.printed_name == names[0]
        )
        designation_id = kraum.commander_designation_id
        kraum = engine.move_card(kraum.object_id, "battlefield", log=False)
        hand_before = len(state.players["A"].zones["hand"])
        library_top = state.players["A"].zones["library"][-1]

        engine.state.active_player = "B"
        engine._dispatch_semantic_event(
            "spell.cast",
            SpellCastEvent(
                schema_version=3,
                card_ref="fixture:B:second-spell",
                object_id="fixture:B:second-spell",
                logical_object_id="fixture:B:second-spell@1",
                controller="B",
                owner="B",
                active_player="B",
                origin="hand",
                stack_ref="fixture:stack:B:second-spell",
                types=("instant",),
                mana_value=1,
                caster_spell_number=2,
                kicked=False,
                has_x_cost=False,
                has_adventure=False,
                keywords=(),
            ).to_context(),
        )
        engine._stabilize()

        self.assertEqual(1, len(state.stack))
        self.assertEqual("A", state.stack[-1].controller)
        self.assertEqual(kraum.object_id, state.stack[-1].source_object_id)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertEqual(hand_before + 1, len(state.players["A"].zones["hand"]))
        self.assertIn(library_top, state.players["A"].zones["hand"])
        self.assertEqual("battlefield", kraum.zone)
        self.assertTrue(kraum.is_commander)
        self.assertEqual(designation_id, kraum.commander_designation_id)
        self.assertIn(kraum.oracle_id, state.commander_oracle_ids["A"])
        self.assertEqual(
            state.to_dict(),
            GameState.from_dict(state.to_dict()).to_dict(),
        )

    def test_unsupported_or_mismatched_pairings_fail_closed_without_mutation(self):
        all_names = (
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
            "Wilson, Refined Grizzly",
            "Flaming Fist",
            "Rose Tyler",
            "The Tenth Doctor",
            "Toothy, Imaginary Friend",
            "Bjorna, Nightfall Alchemist",
        )
        registry = self.registry_for(*all_names)
        thrasios = self.db.lookup("Thrasios, Triton Hero")
        tymna = self.db.lookup("Tymna the Weaver")
        wilson = self.db.lookup("Wilson, Refined Grizzly")
        rose = self.db.lookup("Rose Tyler")
        doctor = self.db.lookup("The Tenth Doctor")
        cases = (
            (thrasios, wilson),
            (self.db.lookup("Toothy, Imaginary Friend"), thrasios),
            (self.db.lookup("Bjorna, Nightfall Alchemist"), thrasios),
            (
                replace(
                    thrasios,
                    type_line="Legendary Enchantment",
                ),
                tymna,
            ),
            (
                wilson,
                replace(
                    self.db.lookup("Flaming Fist"),
                    type_line="Enchantment — Background",
                ),
            ),
            (
                rose,
                replace(
                    doctor,
                    type_line=(
                        "Legendary Creature — Time Lord Doctor Shapeshifter"
                    ),
                ),
            ),
            (thrasios, thrasios),
        )
        before = registry.card_program_fingerprints()
        for first, second in cases:
            with self.subTest(first=first.name, second=second.name):
                with self.assertRaises(CommanderPairingError):
                    validate_commander_pair(
                        self.db,
                        registry,
                        (first, second),
                    )
        self.assertEqual(before, registry.card_program_fingerprints())

        arbitrary = pairing_deck(
            "Thrasios, Triton Hero",
            "Wilson, Refined Grizzly",
        )
        with self.assertRaises(CommanderPairingError):
            initial_commander_state(
                self.db,
                {"A": arbitrary, "B": arbitrary},
                first_player="A",
                config=GameConfig(seed=702_124_002),
                semantics=registry,
            )

        too_many = DeckDefinition(
            name="Three commanders",
            entries=[
                DeckEntry(name, board="commander")
                for name in (
                    "Thrasios, Triton Hero",
                    "Tymna the Weaver",
                    "Wilson, Refined Grizzly",
                )
            ],
            commanders=[
                "Thrasios, Triton Hero",
                "Tymna the Weaver",
                "Wilson, Refined Grizzly",
            ],
        )
        with self.assertRaisesRegex(ValueError, "at most two"):
            initial_commander_state(
                self.db,
                {"A": too_many, "B": too_many},
                first_player="A",
                config=GameConfig(seed=702_124_003),
                semantics=registry,
            )

        inconsistent_board = pairing_deck(
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
        )
        inconsistent_board.entries.append(
            DeckEntry("Wilson, Refined Grizzly", board="commander")
        )
        with self.assertRaisesRegex(ValueError, "must match"):
            initial_commander_state(
                self.db,
                {"A": inconsistent_board, "B": inconsistent_board},
                first_player="A",
                config=GameConfig(seed=702_124_004),
                semantics=registry,
            )

        missing_designation = pairing_deck(
            "Thrasios, Triton Hero",
            "Tymna the Weaver",
        )
        missing_designation.entries.pop()
        with self.assertRaisesRegex(ValueError, "must exist"):
            initial_commander_state(
                self.db,
                {"A": missing_designation, "B": missing_designation},
                first_player="A",
                config=GameConfig(seed=702_124_005),
                semantics=registry,
            )

    def test_pairing_program_and_compiler_mutations_fail_closed(self):
        names = ("Thrasios, Triton Hero", "Tymna the Weaver")
        registry = self.registry_for(*names)
        records = tuple(self.db.lookup(name) for name in names)
        declaration = commander_pairing_declaration(
            self.db,
            registry,
            records[0],
        )
        self.assertIsNotNone(declaration)
        registry.remove(declaration.program_key)
        with self.assertRaises(CommanderPairingError):
            validate_commander_pair(self.db, registry, records)

        with patch(
            "quorune.compiler.keyword_nodes.commander_pairing_keyword_node",
            return_value=None,
        ):
            ir = compile_oracle_card(
                records[0],
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id == COMMANDER_PAIRING_TEMPLATE_ID
                and node.exact
                for face in ir.faces
                for node in face.nodes
            )
        )


if __name__ == "__main__":
    unittest.main()
