from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class MixedPreventionAftermathNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        output = Path(cls.temporary.name) / "mixed-prevention.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/mixed-prevention-aftermath-cards.json"], output)
        cls.db = CardDatabase(output)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Mixed Prevention Aftermath", [
            DeckEntry(commander, 1, "commander"),
            DeckEntry("Plains", 30),
        ], [commander])

    def prepare_prevented_spell(self, game, name, cost, *, x=None, reflect=True):
        glow = self.add(game.engine, "Candles' Glow", zone="hand")
        source = self.add(game.engine, name, seat="C", zone="hand")
        for card in (glow, source):
            self.assert_closed(game, card)
        palm = self.add(game.engine, "Deflecting Palm", seat="B", zone="hand") if reflect else None
        if palm is not None:
            self.assert_closed(game, palm)
        self.prepare(game, actor="C", active="C", mana=cost)
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"R": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", glow, ["A"])
        self.resolve_top(game)
        self.priority(game, "C")
        action = self.action(game, "C", source)
        command = {"action_id": action["id"], "pay": "auto"}
        if x is not None:
            command["x"] = x
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        if palm is not None:
            original = game.state.stack[-1]
            self.cast_for(game, "B", palm)
            choice = self.resolve(game)
            self.assertEqual(("semantic.choice", ["B"]), (choice.kind, choice.actors))
            result = game.act("pilot:B", {"action_id": "choose", "source": original.ref})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(2 if reflect else 1, len(game.state.damage_prevention_shields))
        return self.replay(game, load=True), source.object_id

    def test_original_life_gain_and_reflected_damage_follow_one_prevention_batch(self):
        game, source_id = self.prepare_prevented_spell(self.session(6155035), "Syphon Soul", {"C": 2, "B": 1})
        self.resolve_top(game)
        self.assertEqual([42, 40, 40, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard", game.state.cards[source_id].zone)
        events = [event.details for event in game.state.events if event.code == "damage.prevention.aftermath"]
        self.assertEqual({("gain_life", "A", 2), ("deal_damage", "C", 2)}, {
            (event["kind"], event["subject"], event["applied_amount"]) for event in events
        })
        self.replay(game, load=True)

    def add_worship_battlefield(self, game, seat):
        worship = self.add(game.engine, "Worship", seat=seat)
        spider = self.add(game.engine, "Giant Spider", seat=seat)
        # The reviewed Worship pack provides the life-floor replacement.
        programs = game.engine.semantics.runtime_handler_programs_for_oracle(
            worship.oracle_id, active_zone="battlefield", event="damage.results",
        )
        self.assertTrue(any(game.engine.semantic_program_is_current_trusted(program) for program in programs))
        return spider

    def test_reflected_damage_uses_life_after_the_primary_damage_result(self):
        game = self.session(6155036)
        spider = self.add_worship_battlefield(game, "C")
        game.state.players["C"].life = 2
        game, source_id = self.prepare_prevented_spell(game, "Earthquake", {"C": 2, "R": 1}, x=2)
        self.resolve_top(game)
        self.assertEqual([42, 40, 1, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard", game.state.cards[source_id].zone)
        self.assertEqual("battlefield", game.state.cards[spider.object_id].zone)
        self.assertEqual(2, game.state.cards[spider.object_id].marked_damage)
        self.replay(game, load=True)

    def test_prevention_gain_participates_in_the_original_damage_life_floor(self):
        game = self.session(6155037)
        self.add_worship_battlefield(game, "A")
        game.state.players["A"].life = 2
        game, source_id = self.prepare_prevented_spell(
            game, "Earthquake", {"C": 5, "R": 1}, x=5, reflect=False,
        )
        self.resolve_top(game)
        self.assertEqual([3, 35, 35, 35], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("graveyard", game.state.cards[source_id].zone)
        self.replay(game, load=True)

    def test_historical_prevention_result_order_survives_save_and_load(self):
        game = self.session(6155038)
        self.add_worship_battlefield(game, "A")
        game.state.damage_prevention_result_version = None
        game.state.players["A"].life = 2
        game, _ = self.prepare_prevented_spell(
            game, "Earthquake", {"C": 5, "R": 1}, x=5, reflect=False,
        )
        self.resolve_top(game)
        self.assertEqual([4, 35, 35, 35], [game.state.players[seat].life for seat in "ABCD"])
        resumed = self.replay(game, load=True)
        self.assertIsNone(resumed.state.damage_prevention_result_version)

    def test_reflected_replacement_resumes_before_any_prevention_result_commits(self):
        game = self.session(6155039)
        healers = [
            self.add(game.engine, "Samite Healer", seat="C", ref=f"reflection-healer-{index}")
            for index in range(2)
        ]
        for healer in healers:
            healer.acquired_control_turn_count = -1
            self.assert_closed(game, healer)
        game, source_id = self.prepare_prevented_spell(game, "Syphon Soul", {"C": 2, "B": 1})
        for healer in healers:
            self.priority(game, "C")
            legal = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
            action, = [row for row in legal if row["id"].startswith(f"activate:{healer.ref}:")]
            result = game.act("pilot:C", {"action_id": action["id"], "targets": ["C"], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.resolve_top(game)
        shield_ids = {shield.effect_id for shield in game.state.damage_prevention_shields}
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["C"]), (pending.kind, pending.actors))
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(shield_ids, {shield.effect_id for shield in game.state.damage_prevention_shields})
        self.assertEqual("stack", game.state.cards[source_id].zone)
        game = self.replay(game, load=True)
        options = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        selection = {"action_id": "choose", "replacement": options[0]["id"]}
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", selection).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:C", selection)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([42, 40, 42, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)
