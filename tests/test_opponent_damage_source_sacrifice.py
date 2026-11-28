"""Choose one permanent for each committed opposing damage occurrence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_damage_source_sacrifice import opponent_damage_source_player_set
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


MICHIKO = "Michiko Konda, Truth Seeker"


class OpponentDamageSourceSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-damage-source-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/opponent-damage-source-sacrifice-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed damage source", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def sacrifice(self, game, card, *, seat="B", accepted=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref]})
        self.assertEqual(accepted, result.ok, result.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_departed_spell_uses_its_original_opposing_controller_and_one_permanent(self):
        game = self.playing(61127296)
        source = self.add(game.engine, MICHIKO)
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self_damage = self.add(game.engine, "Shock", zone="hand")
        first = self.add(game.engine, "Sol Ring", seat="B")
        second = self.add(game.engine, "Wall of Denial", seat="B")
        foreign = self.add(game.engine, "Plains", seat="C")
        for card in (source, bolt, self_damage, first, second):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        life_before = game.state.players["A"].life
        self.cast_for(game, "A", self_damage, ["A"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(life_before - 2, game.state.players["A"].life)
        self.assertEqual([], game.state.stack)

        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        self.assertEqual("graveyard", self.zone(game, bolt))
        self.assertEqual(life_before - 5, game.state.players["A"].life)
        trigger, = game.state.stack
        self.assertEqual((source.object_id, "A", []), (trigger.source_object_id, trigger.controller, list(trigger.targets)))
        game = self.replay(game, load=True)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        self.sacrifice(game, second, seat="C", accepted=False)
        self.sacrifice(game, foreign, accepted=False)
        self.sacrifice(game, second)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.zone(game, second))
        self.assertEqual("battlefield", self.zone(game, first))
        self.assertEqual("battlefield", self.zone(game, foreign))
        self.replay(game, load=True)

    def test_actual_double_strike_binds_two_independent_sacrifice_choices(self):
        game = self.playing(61127297)
        source = self.add(game.engine, MICHIKO)
        attacker = self.add(game.engine, "Fencing Ace", seat="B")
        first = self.add(game.engine, "Sol Ring", seat="B")
        second = self.add(game.engine, "Plains", seat="B")
        for card in (source, attacker, first):
            self.assert_closed(game, card)
        attacker.acquired_control_turn_count = -1
        self.prepare(game, actor="B", active="B")
        game.state.current_turn = replace(game.state.current_turn, player="B")
        game.state.last_normal_turn_player = "B"
        self.checkpoint(game)
        initial_life = game.state.players["A"].life
        self.until(game, lambda item: item.state.pending_decision.kind == "combat.attackers")
        attack = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "A"}})
        self.assertTrue(attack.ok, attack.summary)
        steps = []
        for damage, card in enumerate((first, second), start=1):
            self.until(game, lambda item: item.state.pending_decision.kind == "choice.apnap")
            self.assertEqual(["B"], game.state.pending_decision.actors)
            self.assertEqual(initial_life - damage, game.state.players["A"].life)
            steps.append(game.state.combat.damage_step_index)
            self.sacrifice(game, card)
            self.assertEqual("graveyard", self.zone(game, card))
        self.assertEqual([0, 1], steps)
        self.assertEqual("battlefield", self.zone(game, attacker))
        self.replay(game, load=True)

    def test_actual_opponent_without_a_permanent_finishes_the_trigger_without_a_choice(self):
        game = self.playing(61127298)
        source = self.add(game.engine, MICHIKO)
        bolt = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(game, source)
        self.prepare(game, actor="A", active="A")
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "B", bolt, ["A"])
        self.resolve_top(game)
        trigger, = game.state.stack
        self.assertEqual(source.object_id, trigger.source_object_id)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.zone(game, bolt))
        self.replay(game, load=True)

    def test_complete_original_requires_an_opposing_damage_source_and_a_single_sacrifice(self):
        original = self.db.lookup(MICHIKO, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Michiko Konda, Truth Seeker")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        trigger, = (program for program in semantics.programs_for_oracle(original.oracle_id)
                    if program.ability_id.startswith("trigger:"))
        self.assertIsNone(trigger.target_schema)
        effect, = trigger.effects
        self.assertEqual(("damage.dealt", "sacrifice", 1), (trigger.event, effect["then"], effect["count"]))
        self.assertEqual(opponent_damage_source_player_set(), effect["players"])
        for old, new in (("an opponent controls", "you control"),
                         ("a permanent of their choice", "two permanents of their choice")):
            with self.subTest(new=new):
                changed = replace(original, oracle_text=original.oracle_text.replace(old, new))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile="commander_review",
                ).status)
