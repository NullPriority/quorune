"""Native evidence for turn life loss and a two-stat sacrifice result."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_fragments import CURRENT_ABILITY_FRAGMENT_COVERAGE
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Greven, Predator Captain"


class AttackSacrificePowerDrawToughnessLossTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "attack-sacrifice-power-draw-toughness-loss.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/attack-sacrifice-power-draw-toughness-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Attack sacrifice separate stats", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(original.oracle_id)
        static, = [program for program in programs if program.event == "continuous" and program.handlers]
        attack, = [program for program in programs if program.event == "creature.attacks"]
        return original, static, attack

    def test_complete_original_admits_one_current_loss_modifier_and_one_other_creature_choice(self):
        original, static, attack = self.programs()
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, static.coverage)
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, attack.coverage)
        self.assertEqual(("battlefield", "battlefield"), (static.active_zone, attack.active_zone))
        self.assertIsNone(attack.target_schema)
        result, = attack.effects
        self.assertEqual(("choose_cards_apnap", "sacrifice", "$source.zone_object"),
                         (result["op"], result["then"], result["exclude_ref"]))
        for text in (
            original.oracle_text.replace("life you've lost this turn", "life you've gained this turn"),
            original.oracle_text.replace("+X/+0", "+X/+X"),
            original.oracle_text.replace("another creature", "a creature"),
            original.oracle_text.replace("that creature's power", "that creature's toughness"),
            original.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_raw_attack_cannot_replace_current_self_attack_and_committed_creature_exclusion(self):
        _, static, attack = self.programs()
        effect, = attack.effects
        for changed in (
            replace(attack, event_condition=None),
            replace(attack, coverage=tuple(tag for tag in attack.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(attack, effects=({key: value for key, value in effect.items() if key != "exclude_ref"},)),
            replace(attack, effects=({**effect, "count": 2},)),
            replace(static, coverage=tuple(tag for tag in static.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(static, handlers=[]),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def stats(self, game, source):
        return tuple(game.engine._numeric_stat(source.object_id, stat) for stat in ("power", "toughness"))

    def test_actual_attack_reads_live_separate_stats_and_draws_before_losing_life(self):
        game = self.session(61340310)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        spider = self.add(game.engine, "Giant Spider")
        boost = self.add(game.engine, "Giant Growth", zone="hand")
        gain = self.add(game.engine, "Revitalize", zone="hand")
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        self.add(game.engine, "Forest")
        for card in (source, spider, boost, gain, shot):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "B", shot, ["A"])
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["A"].life)
        self.assertEqual((7, 5), self.stats(game, source))
        self.cast_for(game, "A", gain)
        self.resolve_top(game)
        self.assertEqual(41, game.state.players["A"].life)
        self.assertEqual((7, 5), self.stats(game, source))
        self.assertIn("menace", game.engine._combat_keywords(source))
        self.attack(game, [(source, "B")])
        trigger, = [item for item in game.state.stack if item.source_object_id == source.object_id]
        self.assertEqual(("A", []), (trigger.controller, list(trigger.targets)))
        self.cast_for(game, "A", boost, [spider.ref])
        self.resolve_top(game)
        self.assertEqual((5, 7), self.stats(game, spider))
        old_hand = set(game.state.players["A"].zones["hand"])
        choice = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (choice.kind, choice.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual([spider.ref], packet["ctx"]["options"])
        field, = packet["legal_actions"][0]["form"]["fields"]
        self.assertEqual((0, 1), (field["minimum"], field["maximum"]))
        for actor, selected in (("A", [source.ref]), ("B", [spider.ref]), ("A", [spider.ref, spider.ref])):
            before = authoritative_state_hash(game.state)
            denied = game.act("pilot:" + actor, {"action_id": "choose", "cards": selected})
            self.assertFalse(denied.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        last_event = game.state.event_sequence
        submitted = game.act("pilot:A", {"action_id": "choose", "cards": [spider.ref]})
        self.assertTrue(submitted.ok, submitted.summary)
        self.assertIsNone(self.resolve(game))
        drawn = set(game.state.players["A"].zones["hand"]) - old_hand
        self.assertEqual(5, len(drawn))
        self.assertEqual(34, game.state.players["A"].life)
        self.assertEqual((14, 5), self.stats(game, source))
        self.assertEqual("graveyard", game.state.cards[spider.object_id].zone)
        events = [event.code for event in game.state.events if event.event_id > last_event]
        self.assertLess(events.index("choice.sacrifice"), events.index("card.draw"))
        self.assertLess(max(index for index, code in enumerate(events) if code == "card.draw"), events.index("effect.life"))
        opposing_view = json.dumps(game.packet("pilot:B", full=True))
        for card_id in drawn:
            self.assertNotIn(json.dumps(game.state.cards[card_id].ref), opposing_view)
        self.replay(game, load=True)

    def test_actual_decline_and_zero_power_loss_are_independent_branches(self):
        game = self.session(61340320)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        wall = self.add(game.engine, "Wall of Wood")
        self.assert_closed(game, source)
        self.assert_closed(game, wall)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, "B")])
        initial_hand = set(game.state.players["A"].zones["hand"])
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                branch = self.replay(game, load=True)
                result = branch.act("pilot:A", {"action_id": "choose", "cards": [wall.ref] if accepted else []})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(branch))
                self.assertEqual(37 if accepted else 40, branch.state.players["A"].life)
                self.assertEqual((8 if accepted else 5, 5), self.stats(branch, source))
                self.assertEqual(initial_hand, set(branch.state.players["A"].zones["hand"]))
                self.assertEqual("graveyard" if accepted else "battlefield", branch.state.cards[wall.object_id].zone)
                self.replay(branch, load=True)

    def test_actual_donate_switches_the_gross_life_loss_lens_on_an_opponents_turn(self):
        game = self.session(61340330)
        source = self.add(game.engine, ORIGINAL)
        blood = self.add(game.engine, "Sign in Blood", zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        blank = self.add(game.engine, "Lignify", zone="hand")
        for card in (source, blood, donate, blank):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "U": 1, "B": 2, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", blood, ["B"], reserve={"C": 3, "U": 1, "G": 1})
        self.resolve_top(game)
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual((5, 5), self.stats(game, source))
        self.cast_for(game, "A", donate, ["B", source.ref], reserve={"C": 1, "G": 1})
        self.resolve_top(game)
        self.assertEqual(("A", "B"), (source.owner, game.state.cards[source.object_id].controller))
        self.assertEqual((7, 5), self.stats(game, source))
        self.cast_for(game, "A", blank, [source.ref])
        self.resolve_top(game)
        self.assertEqual((0, 4), self.stats(game, source))
        self.assertNotIn("menace", game.engine._combat_keywords(source))
        self.replay(game, load=True)
