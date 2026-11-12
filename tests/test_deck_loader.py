from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from quorune.deck import (
    DeckDefinition,
    DeckEntry,
    DeckLoader,
    extract_moxfield_id,
    is_moxfield_source,
    parse_deck_text,
    parse_moxfield_json,
)
from common import keep_all, load_assets, make_session
from quorune.deck_copy_limits import DECK_COPY_LIMIT_CAPABILITY, DECK_COPY_LIMIT_EVENT
from quorune.record import authoritative_state_hash, replay_record


class DeckLoaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.loader = DeckLoader(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_bundled_decks_are_complete_commander_lists(self):
        self.assertEqual(100, self.mishra.total_cards())
        self.assertEqual(100, self.zimone.total_cards())
        self.assertEqual(["Mishra, Eminent One"], self.mishra.commanders)
        self.assertEqual(["Zimone and Dina"], self.zimone.commanders)

    def test_parse_nested_moxfield_v3_shape(self):
        payload = {
            "name": "Example",
            "boards": {
                "commanders": {
                    "cards": {
                        "Mishra, Eminent One": {
                            "quantity": 1,
                            "card": {"name": "Mishra, Eminent One"},
                        }
                    }
                },
                "mainboard": {
                    "cards": {
                        "Sol Ring": {
                            "quantity": 1,
                            "card": {"name": "Sol Ring"},
                            "tags": ["Mana-Fast"],
                        },
                        "Island": {
                            "quantity": 3,
                            "card": {"name": "Island"},
                        },
                    }
                },
            },
        }
        deck = parse_moxfield_json(payload, source="https://www.moxfield.com/decks/example")
        self.assertEqual(["Mishra, Eminent One"], deck.commanders)
        by_name = {entry.name: entry for entry in deck.entries}
        self.assertEqual(3, by_name["Island"].quantity)
        self.assertEqual(["Mana-Fast"], by_name["Sol Ring"].tags)

    def test_plain_text_tags_do_not_change_card_name(self):
        deck = parse_deck_text(
            "Commander:\n1 Zimone and Dina\nMainboard:\n1 Sol Ring #Mana-Fast\n1 Forest #Land-Mana\n"
        )
        names = {entry.name for entry in deck.entries}
        self.assertIn("Sol Ring", names)
        self.assertNotIn("Sol Ring #Mana-Fast", names)

    def test_moxfield_source_parsing_is_host_and_path_aware(self):
        public_id = "g5vtVfRuS0W5KxZuYqZHGQ"
        self.assertEqual(
            public_id,
            extract_moxfield_id(
                f"https://moxfield.com/decks/{public_id}?utm_source=test"
            ),
        )
        self.assertEqual(
            public_id,
            extract_moxfield_id(f"www.moxfield.com/decks/{public_id}/primer"),
        )
        self.assertTrue(is_moxfield_source(public_id))
        self.assertFalse(
            is_moxfield_source(f"https://example.invalid/moxfield.com/decks/{public_id}")
        )
        with self.assertRaises(ValueError):
            extract_moxfield_id(f"https://moxfield.com/users/{public_id}")

    def test_declared_non_commander_format_is_rejected(self):
        deck = DeckDefinition(
            name="Not Commander",
            entries=[DeckEntry("Island", 99), DeckEntry("Zimone and Dina", 1, "commander")],
            commanders=["Zimone and Dina"],
            metadata={"format": "modern"},
        )
        issues = self.loader.validate_commander_deck(deck, check_color_identity=False)
        self.assertIn("expected 'commander'", issues[0])

    def test_printed_copy_limits_apply_to_resolved_names_and_preserve_other_checks(self):
        cases = (
            ("Persistent Petitioners", 2, "Island", "Azami, Lady of Scrolls", None),
            ("Seven Dwarves", 7, "Mountain", "Krenko, Tin Street Kingpin", None),
            ("Seven Dwarves", 8, "Mountain", "Krenko, Tin Street Kingpin", 7),
            ("Nazgûl", 9, "Swamp", "Drana, Kalastria Bloodchief", None),
            ("Nazgûl", 10, "Swamp", "Drana, Kalastria Bloodchief", 9),
        )
        for name, count, land, commander, rejected_limit in cases:
            with self.subTest(card=name, copies=count):
                deck = DeckDefinition("Printed copy limit", [DeckEntry(commander, 1, "commander"),
                    DeckEntry(name, count - 1), DeckEntry(name.casefold(), 1), DeckEntry(land, 99 - count)], [commander])
                expected = [] if rejected_limit is None else [f"Deck has {count} copies of {name}. Its printed limit is {rejected_limit}."]
                self.assertEqual(expected, self.loader.validate_commander_deck(deck))

        invalid = DeckDefinition("Independent restrictions", [DeckEntry("Azami, Lady of Scrolls", 1, "commander"),
            DeckEntry("Rat Colony", 2), DeckEntry("Sol Ring", 2), DeckEntry("Island", 95)], ["Azami, Lady of Scrolls"])
        issues = self.loader.validate_commander_deck(invalid)
        self.assertIn("Singleton warning: 2 copies of Sol Ring", issues)
        self.assertTrue(any(issue.startswith("Color identity warning: Rat Colony") for issue in issues))

    def test_printed_copy_limit_originals_have_trusted_pregame_bindings(self):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.compiler.deck_copy_limits import compiled_deck_copy_limit
        from quorune.oracle_ir import compile_oracle_card, register_generated_programs
        from quorune.rules.capabilities import load_default_capability_registry
        from quorune.semantics import SemanticRegistry

        expected = {"Nazgûl": 9, "Persistent Petitioners": None, "Rat Colony": None,
            "Relentless Rats": None, "Seven Dwarves": 7, "Tempest Hawk": None}
        registry = load_default_capability_registry()
        options = dict(capability_registry=registry, capability_profile="commander_review")
        semantics = SemanticRegistry()
        cards = tuple(self.db.lookup(name) for name in expected)
        register_generated_programs(self.db, semantics, cards, **options, promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        for card in cards:
            with self.subTest(card=card.name):
                ir = compile_oracle_card(card, **options)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                declaration, = (node for face in ir.faces for node in face.nodes if node.event == DECK_COPY_LIMIT_EVENT)
                self.assertEqual("deck", declaration.active_zone)
                self.assertEqual((), declaration.effects)
                self.assertTrue(card.oracle_text.splitlines()[declaration.span.line - 1].startswith("A deck can have "))
                self.assertEqual(expected[card.name], compiled_deck_copy_limit(card).maximum)
                program = compile_best_available_card_program(self.db, card, semantic_registry=semantics, **options)
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                setup, = semantics.programs_for_oracle(card.oracle_id, active_zone="deck", event=DECK_COPY_LIMIT_EVENT)
                self.assertEqual([DECK_COPY_LIMIT_CAPABILITY], setup.capability_dependencies)
                changed = replace(setup, capability_dependencies=[], capability_closure=None)
                rejected = bind_semantic_program_runtime(changed, capability_registry=registry, profile="commander_review")
                self.assertIn("capability:undeclared_runtime_dependency:" + DECK_COPY_LIMIT_CAPABILITY, rejected["blockers"])

    def test_printed_copy_limit_rejects_foreign_names_open_clauses_and_duplicates(self):
        from quorune.compiler.deck_copy_limits import compiled_deck_copy_limit
        from quorune.deck_copy_limits import DeckCopyLimitSpec

        original = self.db.lookup("Seven Dwarves")
        for text in (
            "A deck can have up to seven cards named Other Dwarves.",
            "A deck can have up to 0 cards named Seven Dwarves.",
            "A deck can have any number of cards named Seven Dwarves while you control a Dwarf.",
            "A deck can have up to seven cards named Seven Dwarves.\nA deck can have any number of cards named Seven Dwarves.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(compiled_deck_copy_limit(replace(original, oracle_text=text)))
        for malformed in ({"card_name": "Seven Dwarves", "maximum": True},
                {"card_name": "Seven Dwarves", "maximum": 7, "extra": True}):
            with self.assertRaises(ValueError):
                DeckCopyLimitSpec.from_dict(malformed)

    def test_printed_copy_limit_compiler_omission_restores_singleton_conflict(self):
        commander = "Azami, Lady of Scrolls"
        deck = DeckDefinition("Copy limit mutation", [DeckEntry(commander, 1, "commander"),
            DeckEntry("Persistent Petitioners", 2), DeckEntry("Island", 97)], [commander])
        self.assertEqual([], self.loader.validate_commander_deck(deck))
        with patch("quorune.compiler.deck_copy_limits.printed_deck_copy_limit_node", return_value=None):
            self.assertEqual(["Singleton warning: 2 copies of Persistent Petitioners"], self.loader.validate_commander_deck(deck))

    def test_valid_printed_copy_limit_deck_starts_and_replays_with_both_copies(self):
        commander = "Azami, Lady of Scrolls"
        name = "Persistent Petitioners"
        deck = DeckDefinition("Valid duplicate deck", [DeckEntry(commander, 1, "commander"),
            DeckEntry(name, 2), DeckEntry("Island", 97)], [commander])
        self.assertEqual([], self.loader.validate_commander_deck(deck))
        session = make_session(self.db, deck, self.mishra, players=2, seed=903_505)
        record = self.db.lookup(name)
        copies = [card for card in session.state.cards.values() if card.owner == "A" and card.oracle_id == record.oracle_id]
        self.assertEqual(2, len(copies))
        declaration, = session.engine.semantics.programs_for_oracle(record.oracle_id,
            active_zone="deck", event=DECK_COPY_LIMIT_EVENT)
        self.assertEqual("trusted", declaration.trust_level)
        keep_all(session)
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "printed-copy-limit"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertGreater(replay["commands"], 0)
        self.assertEqual(expected, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
