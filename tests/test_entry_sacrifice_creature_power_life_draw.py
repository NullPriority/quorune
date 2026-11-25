"""Native evidence for life and private draws from one committed sacrifice."""

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


MANDATORY = "Disciple of Bolas"
OPTIONAL = "Disciple of Freyalise // Garden of Freyalise"


class EntrySacrificeCreaturePowerLifeDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-sacrifice-creature-power-life-draw.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-sacrifice-creature-power-life-draw-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Entry sacrifice power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def entry_program(self, name):
        original = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        entry, = [program for program in semantics.programs_for_oracle(original.oracle_id)
                  if program.event in {"permanent.enter", "creature.enter.self", "creature.enter"} and program.effects]
        return original, entry

    def test_both_complete_originals_share_one_nontargeted_entry_choice(self):
        for name in (MANDATORY, OPTIONAL):
            with self.subTest(name=name):
                _original, entry = self.entry_program(name)
                self.assertIsNone(entry.target_schema)
                self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, entry.coverage)
                effect, = entry.effects
                self.assertEqual(("choose_cards_apnap", "sacrifice", "$source.zone_object"),
                                 (effect["op"], effect["then"], effect["exclude_ref"]))
        original = self.db.lookup(MANDATORY, fuzzy=False)
        for changed in (
            original.oracle_text.replace("another creature", "a creature"),
            original.oracle_text.replace("creature's power", "creature's toughness"),
            original.oracle_text.replace("creature's power.", "creature's power. Rewrite history."),
        ):
            candidate = replace(original, oracle_text=changed)
            self.assertNotEqual("exact", compile_oracle_card(candidate, capability_registry=self.registry).status)

    def test_raw_result_cannot_drop_the_original_source_exclusion_or_current_ability(self):
        _, entry = self.entry_program(MANDATORY)
        effect, = entry.effects
        shortened = {key: value for key, value in effect.items() if key != "exclude_ref"}
        for changed in (
            replace(entry, event_condition=None),
            replace(entry, coverage=tuple(tag for tag in entry.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(entry, effects=(shortened,)),
            replace(entry, effects=({**effect, "count": 2},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def cast_entry(self, game, source, payment, *, reserve=None):
        self.prepare(game, mana=payment)
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve=reserve)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)

    def assert_rejected_sacrifice(self, game, actor, cards):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, {"action_id": "choose", "cards": cards})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_mandatory_entry_uses_live_power_then_draws_privately_after_replaced_sacrifice(self):
        game = self.session(60820310)
        source = self.add(game.engine, MANDATORY, zone="hand")
        spider = self.add(game.engine, "Giant Spider")
        stronger = self.add(game.engine, "Colossal Dreadmaw")
        foreign = self.add(game.engine, "Grizzly Bears", seat="C")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        rest = self.add(game.engine, "Rest in Peace", seat="B")
        for card in (source, spider, stronger, growth, rest):
            self.assert_closed(game, card)
        self.cast_entry(game, source, {"C": 3, "B": 1, "G": 1}, reserve={"G": 1})
        self.cast_for(game, "A", growth, [spider.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(spider.object_id, "power"))
        self.assertEqual(7, game.engine._numeric_stat(spider.object_id, "toughness"))
        before = game.state.players["A"].life
        hand_before = set(game.state.players["A"].zones["hand"])
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        packet = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual({spider.ref, stronger.ref}, set(packet["ctx"]["options"]))
        choose, = packet["legal_actions"]
        field, = choose["form"]["fields"]
        self.assertEqual(("cards", 1, 1), (field["name"], field["minimum"], field["maximum"]))
        self.assert_rejected_sacrifice(game, "A", [])
        self.assert_rejected_sacrifice(game, "A", [source.ref])
        self.assert_rejected_sacrifice(game, "A", [foreign.ref])
        self.assert_rejected_sacrifice(game, "B", [spider.ref])
        game = self.replay(game, load=True)
        result_start = game.state.event_sequence
        result = game.act("pilot:A", {"action_id": "choose", "cards": [spider.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before + 5, game.state.players["A"].life)
        drawn = set(game.state.players["A"].zones["hand"]) - hand_before
        self.assertEqual(5, len(drawn))
        self.assertEqual("exile", game.state.cards[spider.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[stronger.object_id].zone)
        codes = [event.code for event in game.state.events if event.event_id > result_start]
        self.assertLess(codes.index("choice.sacrifice"), codes.index("effect.life"))
        self.assertLess(codes.index("effect.life"), codes.index("card.draw"))
        opposing_view = json.dumps(game.packet("pilot:B", full=True))
        for card_id in drawn:
            self.assertNotIn(json.dumps(game.state.cards[card_id].ref), opposing_view)
        self.replay(game, load=True)

    def test_actual_mandatory_entry_with_only_itself_does_nothing(self):
        game = self.session(60820312)
        source = self.add(game.engine, MANDATORY, zone="hand")
        self.assert_closed(game, source)
        self.cast_entry(game, source, {"C": 3, "B": 1})
        before = (game.state.players["A"].life, len(game.state.players["A"].zones["hand"]))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(before, (game.state.players["A"].life, len(game.state.players["A"].zones["hand"])))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_optional_entry_declines_or_sacrifices_with_the_same_power_result(self):
        game = self.session(60820316)
        source = self.add(game.engine, OPTIONAL, zone="hand")
        spider = self.add(game.engine, "Giant Spider")
        wall = self.add(game.engine, "Wall of Wood")
        for card in (source, spider, wall):
            self.assert_closed(game, card)
        self.cast_entry(game, source, {"C": 3, "G": 3})
        before = game.state.players["A"].life
        hand_before = len(game.state.players["A"].zones["hand"])
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["A"]), (decision.kind, decision.actors))
        self.assert_rejected_sacrifice(game, "A", [source.ref])
        choose, = game.packet("pilot:A", full=True)["decision"]["legal_actions"]
        field, = choose["form"]["fields"]
        self.assertEqual(("cards", 0, 1), (field["name"], field["minimum"], field["maximum"]))
        for selected, amount in ((None, 0), (wall, 0), (spider, 2)):
            with self.subTest(choice=None if selected is None else selected.printed_name):
                branch = self.replay(game, load=True)
                result = branch.act("pilot:A", {"action_id": "choose", "cards": [] if selected is None else [selected.ref]})
                self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(branch))
                self.assertEqual((before + amount, hand_before + amount),
                                 (branch.state.players["A"].life, len(branch.state.players["A"].zones["hand"])))
                for candidate in (wall, spider):
                    self.assertEqual("graveyard" if candidate is selected else "battlefield",
                                     branch.state.cards[candidate.object_id].zone)
                self.replay(branch, load=True)

    def test_actual_garden_can_enter_tapped_or_pay_life_and_produce_green_without_a_creature_trigger(self):
        for pay_life in (False, True):
            with self.subTest(pay_life=pay_life):
                game = self.session(60820318 + pay_life)
                modal = self.add(game.engine, OPTIONAL, zone="hand")
                self.add(game.engine, "Grizzly Bears")
                self.assert_closed(game, modal)
                self.prepare(game)
                self.checkpoint(game)
                before = game.state.players["A"].life
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                land, = [action for action in options if action["action"] == "play_land" and action.get("card") == modal.ref]
                played = game.act("pilot:A", {"action_id": land["id"], "pay_life": pay_life})
                self.assertTrue(played.ok, played.summary)
                current = game.state.cards[modal.object_id]
                self.assertEqual(("battlefield", "Garden of Freyalise", not pay_life),
                                 (current.zone, current.active_face, current.tapped))
                self.assertEqual(before - (3 if pay_life else 0), game.state.players["A"].life)
                self.assertFalse(game.state.stack)
                if pay_life:
                    options = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                    mana, = [action for action in options if action["id"].startswith(f"activate:{modal.ref}:")]
                    tapped = game.act("pilot:A", {"action_id": mana["id"], "pay": "auto"})
                    self.assertTrue(tapped.ok, tapped.summary)
                    self.assertEqual(1, game.state.players["A"].mana_pool["G"])
                self.replay(game, load=True)
