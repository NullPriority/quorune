"""Terminal return compares current facts after the original spell's first instruction."""

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
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.spell_self_exile import CONDITIONAL_SPELL_RETURN_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
PULSES = ("Pulse of the Fields", "Pulse of the Forge", "Pulse of the Grid", "Pulse of the Tangle")


class ConditionalTerminalSpellReturnTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "conditional-terminal-spell-return.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/conditional-terminal-spell-return-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional terminal spell return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_four_pulse_originals_require_the_typed_post_instruction_return(self):
        for name in PULSES:
            with self.subTest(card=name):
                report, semantic = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantic.programs_for_oracle(card.oracle_id)
                self.assertEqual("resolve", spell.event)
                self.assertEqual("graveyard", spell.destination)
                self.assertIn(CONDITIONAL_SPELL_RETURN_CAPABILITY, spell.capability_dependencies)
                dependencies = [cap for cap in spell.capability_dependencies if cap != CONDITIONAL_SPELL_RETURN_CAPABILITY]
                withdrawn = replace(spell, capability_dependencies=dependencies,
                                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
                result = bind_semantic_program_runtime(withdrawn, capability_registry=self.registry, profile=PROFILE)
                self.assertIn(f"capability:undeclared_runtime_dependency:{CONDITIONAL_SPELL_RETURN_CAPABILITY}",
                              result["blockers"])
                with patch("quorune.compiler.spell_self_exile.conditional_self_return_body", return_value=None):
                    self.assertNotEqual("exact", compile_oracle_card(
                        card, capability_registry=self.registry, capability_profile=PROFILE,
                    ).status)

        pulse = self.db.lookup("Pulse of the Dross", fuzzy=False)
        unopened = replace(pulse, oracle_text=pulse.oracle_text.replace("reveals three", "reveals four"))
        self.assertNotEqual("exact", compile_oracle_card(
            unopened, capability_registry=self.registry, capability_profile=PROFILE,
        ).status)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def test_actual_pulse_of_the_fields_compares_life_after_its_gain_for_each_cast(self):
        game = self.playing(61127001)
        spell = self.add(game.engine, "Pulse of the Fields", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"W": 6})
        for seat, life in {"A": 29, "B": 34, "C": 30, "D": 30}.items():
            game.state.players[seat].life = life
        self.checkpoint(game)
        self.cast_for(game, "A", spell, reserve={"W": 3})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(33, game.state.players["A"].life)
        self.assertEqual("hand", self.current(game, spell).zone)

        game = self.replay(game, load=True)
        self.cast_for(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(37, game.state.players["A"].life)
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_pulse_of_the_forge_uses_the_planeswalker_controller_or_damaged_players_current_life(self):
        game = self.playing(61127002)
        spell = self.add(game.engine, "Pulse of the Forge", zone="hand")
        walker = self.add(game.engine, "Jace Beleren", seat="B")
        walker.counters["loyalty"] = 6
        for card in (spell, walker):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"R": 6})
        game.state.players["A"].life = 33
        game.state.players["B"].life = 36
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [walker.ref], reserve={"R": 3})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, self.current(game, walker).counters["loyalty"])
        self.assertEqual(36, game.state.players["B"].life)
        self.assertEqual("hand", self.current(game, spell).zone)

        self.cast_for(game, "A", spell, ["B"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(32, game.state.players["B"].life)
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_pulse_of_the_grid_waits_for_its_own_hidden_discard_before_comparing_hand_counts(self):
        game = self.playing(61127003)
        spell = self.add(game.engine, "Pulse of the Grid", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"U": 3})
        for object_id in list(game.state.players["B"].zones["library"][:2]):
            game.engine.move_card(object_id, "hand", log=False)
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        pending = self.resolve(game)
        self.assertEqual(["A"], pending.actors)
        self.assertEqual("stack", self.current(game, spell).zone)
        self.assertEqual(5, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(5, len(game.state.players["B"].zones["hand"]))

        game = self.replay(game, load=True)
        discard_id = game.state.players["A"].zones["hand"][0]
        command = {"action_id": "choose", "cards": [game.state.cards[discard_id].ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        discarded = game.act("pilot:A", command)
        self.assertTrue(discarded.ok, discarded.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[discard_id].zone)
        self.assertEqual("hand", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_pulse_of_the_tangle_compares_creatures_after_the_new_beast(self):
        for opponent_creatures, destination in ((1, "graveyard"), (2, "hand")):
            with self.subTest(creatures=opponent_creatures):
                game = self.playing(61127004 + opponent_creatures)
                spell = self.add(game.engine, "Pulse of the Tangle", zone="hand")
                self.assert_closed(game, spell)
                for index in range(opponent_creatures):
                    creature = self.add(game.engine, "Grizzly Bears", seat="B", ref=f"comparison-creature-{index}")
                    self.assert_closed(game, creature)
                self.prepare(game, actor="A", active="A", mana={"G": 3})
                self.checkpoint(game)
                self.cast_for(game, "A", spell)
                self.assertIsNone(self.resolve(game))
                beasts = [card for card in game.state.cards.values()
                          if card.zone == "battlefield" and card.controller == "A" and card.is_token]
                self.assertEqual(1, len(beasts))
                self.assertEqual(destination, self.current(game, spell).zone)
                self.replay(game, load=True)
