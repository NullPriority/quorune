"""A source-exile cost stays outside its later all-creature linked return."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import (
    LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION,
    LinkedExileReturnSpec,
    LinkedPublicSetScope,
)
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class SourceExileGlobalCreatureReturnTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "source-exile-global-creature-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/source-exile-global-creature-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Global linked creature return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_planar_guide_keeps_the_source_exile_cost_and_global_creature_return(self):
        report, semantics = register_card(self.db, self.registry, "Planar Guide")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        c = self.db.lookup("Planar Guide", fuzzy=False)
        program, = semantics.programs_for_oracle(c.oracle_id)
        self.assertEqual("activate", program.event)
        self.assertTrue(program.cost_schema["exile_source"])
        self.assertIsNone(program.target_schema)
        first, second = program.effects
        self.assertEqual(first["spec"], second["spec"])
        spec = LinkedExileReturnSpec.from_dict(first["spec"])
        self.assertEqual(LINKED_PUBLIC_ALL_CREATURES_END_STEP_VERSION, spec.schema_version)
        self.assertIs(LinkedPublicSetScope.ALL_CREATURES, spec.public_set_scope)
        changed = replace(program, cost_schema={**program.cost_schema, "exile_source": False})
        self.assertFalse(bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )["strict"])
        with patch("quorune.compiler.linked_exile_return_templates._PUBLIC_END_STEP_SET_TEXT", {}):
            self.assertNotEqual("exact", compile_oracle_card(
                c, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_actual_paid_guide_waits_for_the_next_end_step_and_returns_the_original_non_tokens(self):
        game = self.playing(61126701)
        source = self.add(game.engine, "Planar Guide")
        own = self.add(game.engine, "Grizzly Bears")
        other = self.add(game.engine, "Wall of Omens", seat="B")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 3
        token_spell = self.add(game.engine, "Raise the Alarm", zone="hand")
        mana = tuple(self.add(game.engine, "Plains", ref=f"guide-payment-{i}") for i in range(4))
        for card in (source, own, other, walker, token_spell, *mana):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"W": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", token_spell, reserve={})
        self.assertIsNone(self.resolve(game))
        tokens = tuple(card.object_id for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        self.assertEqual(2, len(tokens))

        self.until(game, lambda value: value.state.active_player == "A" and value.state.step == "end_step")
        self.priority(game, "A")
        offered = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        choice, = (entry for entry in offered if entry["id"].startswith(f"activate:{source.ref}:"))
        activated = game.act("pilot:A", {"action_id": choice["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("exile", self.current(game, source).zone)
        self.assertEqual("battlefield", self.current(game, other).zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", "end_step"), (game.state.active_player, game.state.step))
        self.assertEqual("exile", self.current(game, own).zone)
        self.assertEqual("exile", self.current(game, other).zone)
        self.assertEqual("battlefield", self.current(game, walker).zone)
        game = self.replay(game, load=True)

        self.until(game, lambda value: value.state.active_player == "B" and value.state.step == "end_step" and bool(value.state.stack))
        previous_hand = len(game.state.players["B"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", self.current(game, own).controller)
        self.assertEqual("B", self.current(game, other).controller)
        self.assertEqual("battlefield", self.current(game, own).zone)
        self.assertEqual("battlefield", self.current(game, other).zone)
        self.assertEqual("exile", self.current(game, source).zone)
        self.assertEqual(previous_hand + 1, len(game.state.players["B"].zones["hand"]))
        self.assertTrue(all(key not in game.state.cards or game.state.cards[key].zone != "battlefield" for key in tokens))
        self.replay(game, load=True)
