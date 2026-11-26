"""Exercise game-long named cast admission, hidden placement, and actual match win."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.named_cast_win import NAMED_CAST_WIN_CAPABILITY, NAMED_CAST_WIN_FIELD
from quorune.spell_cast_name_model import (
    CAST_NAME_RECEIPT_FIELD, SPELL_CAST_NAME_HISTORY_CAPABILITY, SpellCastNameHistory,
)
from quorune.spell_resolution_name_codec import read_resolution_name_ledger, validate_spell_name_provenance
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


APPROACH = "Approach of the Second Sun"
PROFILE = "commander_review"


class NamedCastWinTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "named-cast-win.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "attachment-source-return-cards.json",
            fixtures / "independent-target-clause-cards.json",
            fixtures / "named-cast-win-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Named cast win and library", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_source_rejects_foreign_history_targeting_and_missing_authority(self):
        record = self.db.lookup(APPROACH, fuzzy=False)
        args = dict(capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", compile_oracle_card(record, **args).status)
        ready, semantics = register_card(self.db, self.registry, APPROACH)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertIn(NAMED_CAST_WIN_CAPABILITY, program.capability_dependencies)
        self.assertEqual(APPROACH, program.effects[0][NAMED_CAST_WIN_FIELD]["name"])
        deps = tuple(dep for dep in program.capability_dependencies if dep != SPELL_CAST_NAME_HISTORY_CAPABILITY)
        variants = (
            replace(program, event="step.begin"),
            replace(program, target_schema={"zones": ["player"], "categories": ["player"], "count": 1}),
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
        )
        for changed in variants:
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for previous, changed in (("from your hand", "from exile"), ("this game", "this turn"),
                                  ("seventh", "sixth"), ("you gain 7 life", "you gain 8 life")):
            with self.subTest(changed=changed):
                copy = replace(record, oracle_text=record.oracle_text.replace(previous, changed))
                self.assertNotEqual("exact", compile_oracle_card(copy, **args).status)

    def test_cast_names_codec_keeps_distinct_casters_and_omits_history_from_old_records(self):
        game = self.session(7432001)
        state = game.state.to_dict()
        self.assertEqual(0, self.count(game))
        history = SpellCastNameHistory().note_cast(caster="A", names=(APPROACH,))
        history = history.note_cast(caster="B", names=(APPROACH,))
        self.assertEqual([1, 1, 0], [history.count(seat, APPROACH) for seat in "ABC"])
        ledger = state["spell_resolution_name_ledger"]
        restored = read_resolution_name_ledger({
            **state, "spell_resolution_name_ledger": {**ledger, "cast_history": history.to_dict()},
        })
        self.assertEqual(history, restored.cast_history)
        previous = {key: value for key, value in ledger.items() if key != "cast_history"}
        state["spell_resolution_name_ledger"] = {**previous, "schema_version": 1}
        legacy = type(game.state).from_dict(state)
        self.assertIsNone(legacy.spell_resolution_name_ledger.cast_history)
        self.assertEqual(state["spell_resolution_name_ledger"], legacy.to_dict()["spell_resolution_name_ledger"])
        validate_spell_name_provenance({"spell_resolution_name_history_version": 1}, legacy)
        for changed in (None, {"schema_version": 1, "counts_by_caster": {"Z": {APPROACH: 1}}},
                        {"schema_version": 1, "counts_by_caster": {"A": {APPROACH: True}}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                read_resolution_name_ledger({
                    **state, "spell_resolution_name_ledger": {**ledger, "cast_history": changed},
                })

    def arrange_library(self, game, remaining):
        library = game.state.players["A"].zones["library"]
        while len(library) > remaining:
            game.engine.move_card(library[0], "outside", reason="Prepared library size", log=False)
        return tuple(library)

    def assert_library_order(self, game, card, original):
        board = tuple(game.state.players["A"].zones["library"])
        insertion = max(0, len(original) - 6)
        self.assertEqual(original[:insertion] + (card.object_id,) + original[insertion:], board)

    def count(self, game, seat="A"):
        return game.state.spell_resolution_name_ledger.cast_history.count(seat, APPROACH)

    def test_first_original_cast_places_seventh_or_bottom_and_preserves_hidden_order(self):
        for remaining in (9, 4):
            with self.subTest(remaining=remaining):
                game = self.session(7432010 + remaining)
                source = self.add(game.engine, APPROACH, zone="hand")
                self.arrange_library(game, remaining - 1)
                hidden = self.add(game.engine, "Regrowth", zone="library", ref="approach-hidden-library-witness")
                original = tuple(game.state.players["A"].zones["library"])
                self.assert_closed(game, source)
                self.prepare(game, mana={"C": 6, "W": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", source)
                self.assertEqual(1, self.count(game))
                game = self.replay(game, load=True)
                self.resolve_top(game)
                self.assertIsNone(game.state.winner)
                self.assertEqual(47, game.state.players["A"].life)
                self.assert_library_order(game, source, original)
                for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
                    self.assertNotIn(hidden.ref, json.dumps(game.packet(viewer, full=True)))
                self.replay(game, load=True)

    def test_first_library_cast_can_be_countered_and_same_card_from_hand_wins_on_later_turn(self):
        game = self.session(7432031)
        source = self.add(game.engine, APPROACH, zone="library")
        library = game.state.players["A"].zones["library"]
        library.remove(source.object_id)
        library.append(source.object_id)
        sight = self.add(game.engine, "Future Sight")
        flash = self.add(game.engine, "Vedalken Orrery")
        retrieval = self.add(game.engine, "Regrowth", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        for index in range(7):
            self.add(game.engine, "Plains", ref=f"later-cast-plains-{index}")
        for index in range(2):
            self.add(game.engine, "Island", seat="B", ref=f"counterspell-island-{index}")
        for card in (source, sight, flash, retrieval, counter):
            self.assert_closed(game, card)
        starting_hand = game.state.players["A"].zones["hand"]
        game.engine.move_card(starting_hand[0], "outside", reason="Prepared hand size", log=False)
        self.prepare(game, mana={"C": 7, "W": 1, "G": 1})
        self.checkpoint(game)
        start_turn = game.state.turn_sequence
        self.cast_for(game, "A", source, reserve={"C": 1, "G": 1})
        item = game.state.stack[-1]
        self.assertEqual("library", item.context["cast_origin"])
        self.assertIsNone(item.context[CAST_NAME_RECEIPT_FIELD]["hand_owner"])
        self.assertEqual(1, self.count(game))
        self.cast_for(game, "B", counter, [item.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual(40, game.state.players["A"].life)
        self.cast_for(game, "A", retrieval, [game.state.cards[source.object_id].ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.until(game, lambda current: current.state.turn_sequence > start_turn)
        self.assertEqual(1, self.count(game))
        game = self.replay(game, load=True)
        returning = game.state.cards[source.object_id]
        self.cast_for(game, "A", returning)
        item = game.state.stack[-1]
        self.assertEqual("A", item.context[CAST_NAME_RECEIPT_FIELD]["hand_owner"])
        self.assertEqual(2, self.count(game))
        self.assertIsNone(game.state.winner)
        self.resolve_top(game)
        self.assertEqual("A", game.state.winner)
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_stack_copy_grants_fallback_life_without_counting_another_cast(self):
        game = self.session(7432041)
        source = self.add(game.engine, APPROACH, zone="hand")
        echo = self.add(game.engine, "Reverberate", zone="hand")
        original = tuple(game.state.players["A"].zones["library"])
        for card in (source, echo):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "W": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"R": 2})
        self.cast_for(game, "A", echo, [game.state.stack[-1].ref])
        self.resolve_top(game)
        self.assertEqual("spell_copy", game.state.stack[-1].kind)
        self.assertEqual(1, self.count(game))
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        self.assertEqual(47, game.state.players["A"].life)
        self.assertEqual(1, self.count(game))
        self.assertEqual(original, tuple(game.state.players["A"].zones["library"]))
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        self.assertEqual(54, game.state.players["A"].life)
        self.assert_library_order(game, source, original)
        self.replay(game, load=True)

    def test_repeated_library_cast_still_falls_back_when_current_cast_is_not_from_hand(self):
        game = self.session(7432051)
        source = self.add(game.engine, APPROACH, zone="hand")
        sight = self.add(game.engine, "Future Sight")
        for card in (source, sight):
            self.assert_closed(game, card)
        self.arrange_library(game, 0)
        self.prepare(game, mana={"C": 12, "W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 6, "W": 1})
        self.resolve_top(game)
        self.assert_library_order(game, source, ())
        self.cast_for(game, "A", game.state.cards[source.object_id])
        self.assertEqual(2, self.count(game))
        self.assertEqual("library", game.state.stack[-1].context["cast_origin"])
        self.resolve_top(game)
        self.assertIsNone(game.state.winner)
        self.assertEqual(54, game.state.players["A"].life)
        self.assert_library_order(game, source, ())
        self.replay(game, load=True)

    def test_mutation_that_counts_casts_only_on_resolution_breaks_countered_first_cast(self):
        with patch("quorune.spell_cast_name_model.SpellCastNameHistory.count", return_value=0):
            with self.assertRaises(AssertionError):
                self.test_first_library_cast_can_be_countered_and_same_card_from_hand_wins_on_later_turn()
