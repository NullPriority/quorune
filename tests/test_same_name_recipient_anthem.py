"""Live public same-name anthems count matching tokens and exclude them as recipients."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.recipient_modifier_model import RECIPIENT_SHARED_NAME_CAPABILITY
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.shared_type_anthems import SHARED_NAME_ANTHEM_HANDLER
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SameNameRecipientAnthemTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        db_path = Path(cls.temporary.name) / "same-name-recipient-anthem.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "activation-paid-result-slot-cards.json",
            fixtures / "activated-self-transform-cards.json",
            fixtures / "same-name-recipient-anthem-cards.json",
        ], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Live name copies and control", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_same_name_anthem_requires_nontokens_and_its_runtime_authority(self):
        report, semantics = register_card(self.db, self.registry, "Mirror Box")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [row for row in semantics.programs_for_oracle(report["oracle_id"])
                    if row.handlers and row.handlers[0].get("handler_id") == SHARED_NAME_ANTHEM_HANDLER]
        self.assertIn(RECIPIENT_SHARED_NAME_CAPABILITY, program.capability_dependencies)
        dependencies = ("continuous.power_toughness.fixed_anthem",)
        without = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        handler = dict(program.handlers[0])
        condition = dict(handler["condition"])
        with self.assertRaises(SemanticNodeError):
            replace(program, handlers=({**handler, "condition": {**condition, "target_controller": "any"}},))
        bound = bind_semantic_program_runtime(without, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(bound["strict"], bound)

    def _cast_box(self, seed):
        game = self.session(seed)
        box = self.add(game.engine, "Mirror Box", zone="hand")
        first = self.add(game.engine, "Grizzly Bears", ref="same-name-first")
        second = self.add(game.engine, "Grizzly Bears", ref="same-name-second")
        foreign = self.add(game.engine, "Grizzly Bears", ref="same-name-foreign", seat="D")
        concealed = self.add(game.engine, "Grizzly Bears", ref="same-name-faceless")
        concealed.face_down = True
        legendary = self.add(game.engine, "Isamaru, Hound of Konda", ref="same-name-legendary")
        copy = self.add(game.engine, "Cackling Counterpart", zone="hand")
        steal = self.add(game.engine, "Act of Treason", zone="hand", seat="B")
        self.add(game.engine, "Vedalken Orrery", seat="B")
        for card in (box, copy, steal):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 2, "R": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", box, reserve={"U": 2, "C": 1})
        self.resolve_top(game)
        return game, first, second, foreign, concealed, legendary, copy, steal

    def test_actual_copy_token_joins_name_count_without_a_bonus_and_control_changes_replay(self):
        game, first, second, foreign, concealed, legendary, copy, steal = self._cast_box(20125201)
        def power(card):
            return game.engine._numeric_stat(card.object_id, "power")

        self.assertEqual((3, 3, 2, 2, 3), tuple(power(card) for card in (first, second, foreign, concealed, legendary)))
        self.cast_for(game, "A", copy, [first.ref])
        self.resolve_top(game)
        token, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual((4, 4, 2), tuple(power(card) for card in (first, second, token)))
        self.cast_for(game, "B", steal, [first.ref])
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[first.object_id].controller)
        self.assertEqual((2, 3, 2, 2), tuple(power(card) for card in (first, second, token, foreign)))
        self.replay(game, load=True)

    def test_omitting_the_live_name_set_breaks_the_native_anthem(self):
        with patch("quorune.recipient_modifier_model.same_name_objects", return_value={}):
            game, first, *_ = self._cast_box(20125202)
            with self.assertRaises(AssertionError):
                self.assertEqual(3, game.engine._numeric_stat(first.object_id, "power"))
