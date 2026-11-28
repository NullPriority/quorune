"""The actual exile cast selects one complete opponent damage result."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as untap_fixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.cast_origin_replacement import EXILE_CAST_REPLACEMENT_VERSION
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class ExileCastOpponentDamageTests(InheritedNativeFixture, unittest.TestCase):
    playing = untap_fixture.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "exile-cast-opponent-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/exile-cast-opponent-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Original or copied spell", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_same_damage_set_and_independent_foretell(self):
        admitted, semantics = register_card(self.db, self.registry, "Delayed Blast Fireball")
        self.assertTrue(admitted["strict_capability_ready"], admitted["blockers"])
        card = self.db.lookup("Delayed Blast Fireball", fuzzy=False)
        programs = semantics.programs_for_oracle(card.oracle_id)
        self.assertTrue(any("foretell" in json.dumps(program.handlers).casefold() for program in programs))
        spell, = (program for program in programs if program.event == "resolve")
        condition, = spell.effects
        low, high = condition["otherwise_effects"][0], condition["effects"][0]
        self.assertEqual(EXILE_CAST_REPLACEMENT_VERSION, condition["schema_version"])
        self.assertEqual((2, 5), (low["amount"], high["amount"]))
        self.assertEqual({**low, "amount": 5}, high)
        self.assertIn("damage.batch.fixed_set", spell.capability_dependencies)
        changed_branch = deepcopy(condition)
        changed_branch["effects"][0]["groups"][0]["relation"] = "all"
        for modified in (changed_branch, {**condition, "cast_fact": True}):
            self.assertFalse(bind_semantic_program_runtime(
                replace(spell, effects=[modified]), capability_registry=self.registry,
                profile="commander_review",
            )["strict"])
        changed = replace(card, oracle_text=card.oracle_text.replace("cast from exile", "cast from your hand"))
        self.assertNotEqual("exact", compile_oracle_card(
            changed, capability_registry=self.registry, capability_profile="commander_review",
        ).status)

    def life(self, game):
        return tuple(game.state.players[seat].life for seat in "ABCD")

    def setup_creatures(self, game):
        own = self.add(game.engine, "Hill Giant", ref="fireball-own-creature")
        opponent = self.add(game.engine, "Hill Giant", seat="B", ref="fireball-opposing-creature")
        wurm = self.add(game.engine, "Plated Wurm", seat="C")
        return own, opponent, wurm

    def marked(self, game, cards):
        return [game.state.cards[card.object_id].marked_damage for card in cards]

    def test_actual_hand_cast_deals_two_to_each_current_opponent_and_their_creatures(self):
        game = self.session(61415011)
        spell = self.add(game.engine, "Delayed Blast Fireball", zone="hand")
        creatures = self.setup_creatures(game)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "R": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", spell)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 38, 38, 38), self.life(game))
        self.assertEqual([0, 2, 2], self.marked(game, creatures))
        self.replay(game, load=True)

    def test_actual_impulse_cast_deals_five_and_its_stack_copy_deals_two_for_the_copy_controller(self):
        game = self.session(61415012)
        impulse = self.add(game.engine, "Reckless Impulse", zone="hand")
        spell = self.add(game.engine, "Delayed Blast Fireball", zone="library")
        copy_spell = self.add(game.engine, "Twincast", seat="B", zone="hand")
        own, opposing, wurm = self.setup_creatures(game)
        for card in (impulse, spell, copy_spell):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 3})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", impulse, reserve={"C": 1, "R": 2})
        self.resolve_top(game)
        source = game.state.cards[spell.object_id]
        self.assertEqual(("exile", False), (source.zone, source.face_down))
        self.cast_for(game, "A", source)
        original = game.state.stack[-1]
        self.assertEqual("exile", original.context["cast_origin"])
        self.cast_for(game, "B", copy_spell, [original.ref])
        self.resolve_top(game)
        self.assertEqual(("spell_copy", "B"), (game.state.stack[-1].kind, game.state.stack[-1].controller))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual((38, 40, 38, 38), self.life(game))
        self.assertEqual([2, 0, 2], self.marked(game, (own, opposing, wurm)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual((38, 35, 33, 33), self.life(game))
        self.assertEqual("battlefield", game.state.cards[own.object_id].zone)
        self.assertEqual(2, game.state.cards[own.object_id].marked_damage)
        for burned in (opposing, wurm):
            self.assertEqual("graveyard", game.state.cards[burned.object_id].zone)
        self.replay(game, load=True)

    def test_actual_foretell_stays_private_until_paid_on_a_later_turn_and_deals_five(self):
        game = self.playing(61415013)
        spell = self.add(game.engine, "Delayed Blast Fireball", zone="hand")
        creatures = self.setup_creatures(game)
        for index, name in enumerate(("Mountain", "Mountain", "Wastes", "Wastes", "Wastes", "Wastes")):
            self.add(game.engine, name, ref=f"fireball-foretell-mana-{index}")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 2})
        self.checkpoint(game)
        row, = (value for value in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                if value.get("action") == "stage_cast_lifecycle" and value.get("card") == spell.ref)
        staged = game.act("pilot:A", {"action_id": row["id"], "pay": "auto"})
        self.assertTrue(staged.ok, staged.summary)
        source = game.state.cards[spell.object_id]
        self.assertEqual(("exile", True, ["A"]), (source.zone, source.face_down, source.known_to))
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(spell.printed_name, json.dumps(game.packet(viewer, full=True)))
        self.assertIn(spell.printed_name, json.dumps(game.packet("pilot:A", full=True)))
        self.priority(game, "A")
        before = authoritative_state_hash(game.state)
        early = game.act("pilot:A", {"action_id": "cast:" + source.ref, "from": "exile",
                                     "cost_option": "foretell", "pay": "auto"})
        self.assertFalse(early.ok, early.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.active_player == "B" and current.state.step == "upkeep")
        self.priority(game, "A")
        source = game.state.cards[spell.object_id]
        offer = self.action(game, "A", source)
        self.assertIn("foretell", {option["id"] for option in offer["cost_options"]})
        cast = game.act("pilot:A", {"action_id": offer["id"], "from": "exile",
                                    "cost_option": "foretell", "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("exile", game.state.stack[-1].context["cast_origin"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((40, 35, 35, 35), self.life(game))
        self.assertEqual("battlefield", game.state.cards[creatures[0].object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creatures[1].object_id].zone)
        self.assertEqual("graveyard", game.state.cards[creatures[2].object_id].zone)
        self.replay(game, load=True)
