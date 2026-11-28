"""A targeted hand's owner chooses the only cards the resolving caster may inspect."""

from dataclasses import replace
import json
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
from quorune.rules.hand_inspection import SELECTED_HAND_REVEAL_DISCARD_CAPABILITY
from quorune.rules.spell_self_exile import CONDITIONAL_SPELL_RETURN_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class ChosenHandSubsetRevealDiscardTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "chosen-hand-subset-reveal-discard.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/chosen-hand-subset-reveal-discard-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen hand subset reveal discard", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_blackmail_and_dross_share_one_authorized_selected_reveal(self):
        for name in ("Blackmail", "Pulse of the Dross"):
            with self.subTest(card=name):
                admitted, semantic = register_card(self.db, self.registry, name)
                self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
                card = self.db.lookup(name, fuzzy=False)
                spell, = semantic.programs_for_oracle(card.oracle_id)
                effect, = spell.effects
                self.assertEqual("$target.0", effect["player"])
                self.assertEqual("reveal_selected_three", effect["inspection"])
                self.assertIn(SELECTED_HAND_REVEAL_DISCARD_CAPABILITY, spell.capability_dependencies)
                self.assertEqual(name == "Pulse of the Dross",
                                 CONDITIONAL_SPELL_RETURN_CAPABILITY in spell.capability_dependencies)
                forged = replace(spell, effects=[{**effect, "player": "$controller"}])
                self.assertFalse(bind_semantic_program_runtime(
                    forged, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                unsupported = replace(card, oracle_text=card.oracle_text.replace("reveals three", "reveals four"))
                with patch("quorune.compiler.hand_inspection_templates._SELECTED_REVEAL_DISCARD_TEXT", ""):
                    self.assertNotEqual("exact", compile_oracle_card(
                        card, capability_registry=self.registry, capability_profile=PROFILE,
                    ).status)
                self.assertNotEqual("exact", compile_oracle_card(
                    unsupported, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)

    def empty_hand(self, game, seat):
        for object_id in tuple(game.state.players[seat].zones["hand"]):
            game.engine.move_card(object_id, "library", log=False)

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def reject_choice(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_blackmail_keeps_unrevealed_cards_private_through_both_choice_principals(self):
        game = self.playing(61127101)
        self.empty_hand(game, "B")
        spell = self.add(game.engine, "Blackmail", zone="hand")
        cards = [
            self.add(game.engine, name, seat="B", zone="hand", ref=f"private-hand-object-{index}")
            for index, name in enumerate(("Island", "Forest", "Wind Drake", "Grizzly Bears", "Jace Beleren"))
        ]
        chosen = cards[:3]
        unrevealed = cards[3:]
        cards[0].known_to = sorted({*cards[0].known_to, "C"})
        cards[0].revealed_to = ["C"]
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"B": 1})
        self.checkpoint(game)
        prior_reveals = {card.object_id: tuple(card.revealed_to) for card in cards}
        self.cast_for(game, "A", spell, ["B"])
        pending = self.resolve(game)
        self.assertEqual(["B"], pending.actors)
        audience = json.dumps(game.packet("pilot:A", full=True))
        for card in cards:
            self.assertNotIn(card.ref, audience)
        command = {"action_id": "choose", "cards": [card.ref for card in chosen]}
        self.reject_choice(game, "A", command)
        self.reject_choice(game, "B", {"action_id": "choose", "cards": [card.ref for card in chosen[:2]]})
        game = self.replay(game, load=True)

        revealed = game.act("pilot:B", command)
        self.assertTrue(revealed.ok, revealed.summary)
        self.assertEqual(["A"], game.state.pending_decision.actors)
        visible = json.dumps(game.packet("pilot:A", full=True))
        for card in chosen:
            self.assertIn(card.ref, visible)
            self.assertEqual({"A", "B", "C", "D"}, set(self.current(game, card).revealed_to))
        for card in unrevealed:
            self.assertNotIn(card.ref, visible)
            self.assertEqual(prior_reveals[card.object_id], tuple(self.current(game, card).revealed_to))
        self.reject_choice(game, "A", {"action_id": "choose", "card": unrevealed[0].ref})
        game = self.replay(game, load=True)

        discarded = game.act("pilot:A", {"action_id": "choose", "card": chosen[1].ref})
        self.assertTrue(discarded.ok, discarded.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, chosen[1]).zone)
        for card in (chosen[0], chosen[2]):
            self.assertEqual(prior_reveals[card.object_id], tuple(self.current(game, card).revealed_to))
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_dross_reveals_a_short_hand_and_compares_only_after_discard(self):
        for caster_hand, destination in ((0, "hand"), (1, "graveyard")):
            with self.subTest(caster_hand=caster_hand):
                game = self.playing(61127111 + caster_hand)
                self.empty_hand(game, "A")
                self.empty_hand(game, "B")
                if caster_hand:
                    self.add(game.engine, "Plains", zone="hand", ref="caster-remaining-card")
                spell = self.add(game.engine, "Pulse of the Dross", zone="hand")
                first = self.add(game.engine, "Forest", seat="B", zone="hand")
                second = self.add(game.engine, "Island", seat="B", zone="hand")
                self.assert_closed(game, spell)
                self.prepare(game, actor="A", active="A", mana={"B": 3})
                self.checkpoint(game)
                prior_reveal = tuple(second.revealed_to)
                self.cast_for(game, "A", spell, ["B"])
                pending = self.resolve(game)
                self.assertEqual(["A"], pending.actors)
                for card in (first, second):
                    self.assertEqual({"A", "B", "C", "D"}, set(self.current(game, card).revealed_to))
                game = self.replay(game, load=True)
                discarded = game.act("pilot:A", {"action_id": "choose", "card": first.ref})
                self.assertTrue(discarded.ok, discarded.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", self.current(game, first).zone)
                self.assertEqual("hand", self.current(game, second).zone)
                self.assertEqual(prior_reveal, tuple(self.current(game, second).revealed_to))
                self.assertEqual(destination, self.current(game, spell).zone)
                self.replay(game, load=True)
