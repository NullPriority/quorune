"""Native Strive and one-per-opponent targeting over one existing control owner."""

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
from quorune.rules.targeted_control_set import TARGETED_CONTROL_SET_VERSION
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


HARNESS = "Harness by Force"
MUTINY = "Mass Mutiny"


class AnnouncedTargetCreatureControlTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-target-creature-control.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/announced-target-creature-control-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced creature control", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def cast_action(self, game, spell):
        self.priority(game, "A")
        return self.action(game, "A", game.state.cards[spell.object_id])

    def submit(self, game, action, targets, *, accepted=True):
        before = authoritative_state_hash(game.state)
        outcome = game.act("pilot:A", {
            "action_id": action["id"], "targets": list(targets), "pay": "auto",
        })
        self.assertEqual(accepted, outcome.ok, outcome.summary)
        if not accepted:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def current(self, game, card):
        return game.state.cards[card.object_id]

    def haste(self, game, card):
        row = game.engine._effective_card_data(self.current(game, card))
        return "haste" in {value.casefold() for value in row["keywords"]}

    def test_actual_strive_prices_the_announced_count_and_only_remaining_legal_targets_change(self):
        game = self.playing(61127284)
        spell = self.add(game.engine, HARNESS, zone="hand")
        lost = self.add(game.engine, "Grizzly Bears", seat="B")
        remains = self.add(game.engine, "Centaur Courser", seat="C")
        third = self.add(game.engine, "Rumbling Baloth", seat="D")
        reply = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        for card in (spell, lost, remains, third, reply):
            self.assert_closed(game, card)
        for card in (lost, remains, third):
            card.tapped = True
        incarnation = (remains.logical_object_id, remains.zone_timestamp)
        self.prepare(game, actor="A", active="A", mana={"C": 3, "R": 3})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)

        action = self.cast_action(game, spell)
        self.submit(game, action, [lost.ref, lost.ref], accepted=False)
        self.submit(game, action, [lost.ref, remains.ref, third.ref], accepted=False)
        self.submit(game, action, [lost.ref, remains.ref])
        self.assertEqual({lost.ref, remains.ref}, set(game.state.stack[-1].targets))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast_for(game, "B", reply, [lost.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))

        self.assertEqual("hand", self.current(game, lost).zone)
        controlled = self.current(game, remains)
        self.assertEqual(("A", "C", False), (controlled.controller, controlled.owner, controlled.tapped))
        self.assertEqual(incarnation, (controlled.logical_object_id, controlled.zone_timestamp))
        self.assertTrue(self.haste(game, remains))
        self.assertEqual(("D", True), (self.current(game, third).controller, self.current(game, third).tapped))
        self.assertFalse(self.haste(game, third))
        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("C", self.current(game, remains).controller)
        self.assertFalse(self.haste(game, remains))
        self.replay(game, load=True)

    def test_actual_zero_strive_targets_pay_the_base_price_and_resolve_without_a_set(self):
        game = self.playing(61127285)
        spell = self.add(game.engine, HARNESS, zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, actor="A", active="A", mana={"C": 1, "R": 2})
        self.checkpoint(game)
        self.submit(game, self.cast_action(game, spell), [])
        self.assertEqual([], list(game.state.stack[-1].targets))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self.current(game, spell).zone)
        self.replay(game, load=True)

    def test_actual_mutiny_limits_targets_by_original_opponent_and_then_checks_that_controller(self):
        game = self.playing(61127286)
        spell = self.add(game.engine, MUTINY, zone="hand")
        own = self.add(game.engine, "Grizzly Bears")
        b_first = self.add(game.engine, "Centaur Courser", seat="B")
        b_second = self.add(game.engine, "Rumbling Baloth", seat="B")
        c_first = self.add(game.engine, "Grizzly Bears", seat="C", ref="mutiny-original-c")
        d_first = self.add(game.engine, "Rumbling Baloth", seat="D", ref="mutiny-original-d")
        reply = self.add(game.engine, "Act of Aggression", zone="hand", seat="D")
        for card in (spell, own, b_first, b_second, c_first, d_first, reply):
            self.assert_closed(game, card)
        for card in (own, b_first, b_second, c_first, d_first):
            card.tapped = True
        self.prepare(game, actor="A", active="A", mana={"C": 3, "R": 2})
        game.state.players["D"].mana_pool["R"] = 5
        self.checkpoint(game)

        offered = self.cast_action(game, spell)
        schema = offered["target_schema"]["groups"][0]
        self.assertNotIn(own.ref, schema["legal_refs"])
        self.assertIn(b_first.ref, schema["legal_refs"])
        self.assertIn(c_first.ref, schema["legal_refs"])
        self.submit(game, offered, [b_first.ref, b_second.ref], accepted=False)
        self.submit(game, offered, [own.ref], accepted=False)
        self.submit(game, offered, [b_first.ref, c_first.ref])
        self.priority(game, "D")
        response = self.action(game, "D", reply)
        mana_only, = (option for option in response["cost_options"] if not option.get("additional_life"))
        transfer = game.act("pilot:D", {"action_id": response["id"], "cost_option": mana_only["id"],
                                         "targets": [c_first.ref], "pay": "auto"})
        self.assertTrue(transfer.ok, transfer.summary)
        self.resolve_top(game)
        self.assertEqual("D", self.current(game, c_first).controller)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("A", False), (self.current(game, b_first).controller, self.current(game, b_first).tapped))
        self.assertTrue(self.haste(game, b_first))
        self.assertEqual("D", self.current(game, c_first).controller)
        self.assertEqual(("B", True), (self.current(game, b_second).controller, self.current(game, b_second).tapped))
        self.assertEqual(("D", True), (self.current(game, d_first).controller, self.current(game, d_first).tapped))
        self.assertEqual(("A", True), (self.current(game, own).controller, self.current(game, own).tapped))
        turn = game.state.turn_sequence
        self.until(game, lambda item: item.state.turn_sequence > turn)
        self.assertEqual("B", self.current(game, b_first).controller)
        self.assertEqual("C", self.current(game, c_first).controller)
        self.assertFalse(self.haste(game, b_first))
        self.replay(game, load=True)

    def test_full_original_programs_keep_the_paid_sibling_and_exact_target_shapes(self):
        harness, harness_semantics = register_card(self.db, self.registry, "Harness by Force")
        mutiny, mutiny_semantics = register_card(self.db, self.registry, "Mass Mutiny")
        for report in (harness, mutiny):
            self.assertTrue(report["strict_capability_ready"], report["blockers"])
        first = harness_semantics.programs_for_oracle(harness["oracle_id"])
        second = mutiny_semantics.programs_for_oracle(mutiny["oracle_id"])
        self.assertTrue(any(program.event == "cast.cost" for program in first))
        for views, per_opponent in ((first, False), (second, True)):
            spell, = (program for program in views if program.event == "resolve")
            effect, = spell.effects
            self.assertEqual(1, effect[TARGETED_CONTROL_SET_VERSION])
            self.assertEqual(("gain_control", "untap", "haste"), tuple(effect["steps"]))
            self.assertEqual(per_opponent, spell.target_schema.get("retain_controller", False))
            self.assertEqual(per_opponent, spell.target_schema.get("distinct_controllers", False))
        for name in ("Harness by Force", "Mass Mutiny"):
            original = self.db.lookup(name, fuzzy=False)
            changed = replace(original, oracle_text=original.oracle_text.replace("Untap those creatures.", "Tap those creatures."))
            self.assertNotEqual("exact", compile_oracle_card(
                changed, capability_registry=self.registry, capability_profile="commander_review",
            ).status)
