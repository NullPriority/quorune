"""Native evidence for linked received damage and exact source self-damage."""

from dataclasses import replace
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


ORIGINAL = "Stuffy Doll"


class EntryChosenPlayerReceivedDamageTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-chosen-player-received-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-chosen-player-received-damage-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked received damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantic_programs = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantic_programs.programs_for_oracle(original.oracle_id)
        entry, = [program for program in programs if program.event == "zone.change"]
        receiving, = [program for program in programs if program.event == "damage.received"]
        activation, = [program for program in programs if program.event == "activate"]
        return original, entry, receiving, activation

    def test_complete_original_retains_entry_current_receiving_and_the_self_tap_result(self):
        original, entry, receiving, activation = self.programs()
        self.assertEqual(("all", "battlefield", "battlefield"),
                         (entry.active_zone, receiving.active_zone, activation.active_zone))
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, receiving.coverage)
        self.assertTrue(all(program.target_schema is None for program in (entry, receiving, activation)))
        for text in (
            original.oracle_text.replace("choose a player", "choose an opponent"),
            original.oracle_text.replace("to the chosen player", "to any opponent"),
            original.oracle_text.replace("that much damage", "twice that much damage"),
            original.oracle_text.replace("damage to itself", "damage to its controller"),
            original.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_raw_receiving_and_activation_require_their_original_roles_and_amount(self):
        _, entry, receiving, activation = self.programs()
        for changed in (
            replace(entry, handlers=[]),
            replace(receiving, event_condition=None),
            replace(receiving, coverage=tuple(tag for tag in receiving.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
            replace(receiving, effects=({**receiving.effects[0], "target": "$controller"},)),
            replace(activation, effects=({**activation.effects[0], "amount": 2},)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def choose_entry_player(self, game, player):
        choice = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        options = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in rows}
        self.assertEqual(set("ABCD"), set(options))
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:C", {"action_id": "choose", "replacement": options[player]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": options[player]})
        self.assertTrue(accepted.ok, accepted.summary)

    def cast_doll(self, game, source, player, *, reserve=None):
        self.cast_for(game, "A", source, reserve=reserve)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose_entry_player(game, player)
        self.assertIn("indestructible", game.engine._combat_keywords(game.state.cards[source.object_id]))
        return game

    def tap_to_damage(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith(f"activate:{source.ref}:")]
        self.assertFalse(action.get("target_schema"))
        result = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)

    def test_actual_tap_ability_is_self_damage_and_reflects_once_to_the_chosen_player(self):
        game = self.session(60724010)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        haste = self.add(game.engine, "Fervor")
        self.assert_closed(game, source)
        self.assert_closed(game, haste)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        game = self.cast_doll(game, source, "B")
        self.tap_to_damage(game, source)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[source.object_id].marked_damage)
        trigger, = game.state.stack
        self.assertEqual(("damage.received", 1, []),
                         (trigger.context["event"], trigger.context["amount"], list(trigger.targets)))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual([40, 39, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_actual_three_damage_keeps_the_old_chosen_player_across_a_new_entry(self):
        game = self.session(60724020)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
        later = self.add(game.engine, "Shock", seat="C", zone="hand")
        for card in (source, blink, bolt, later):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 5, "W": 1})
        game.state.players["C"].mana_pool["R"] = 2
        self.checkpoint(game)
        game = self.cast_doll(game, source, "B", reserve={"W": 1})
        previous = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "C", bolt, [source.ref], reserve={"R": 1})
        self.resolve_top(game)
        self.assertEqual(3, game.state.cards[source.object_id].marked_damage)
        trigger, = game.state.stack
        self.assertEqual(3, trigger.context["amount"])
        self.cast_for(game, "A", blink, [source.ref])
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose_entry_player(game, "D")
        self.assertNotEqual(previous, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(trigger.ref, game.state.stack[-1].ref)
        self.resolve_top(game)
        self.assertEqual([40, 37, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        reflected, = [event.details["damage_event"] for event in game.state.events
                      if event.code == "effect.damage" and event.details.get("target") == "B"]
        self.assertEqual(previous, reflected["source_logical_object_id"])
        self.cast_for(game, "C", later, [source.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        self.assertEqual([40, 37, 40, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_self_damage_activation_cannot_hit_a_returned_incarnation(self):
        game = self.session(60724030)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        self.add(game.engine, "Fervor")
        self.assert_closed(game, source)
        self.assert_closed(game, blink)
        self.prepare(game, mana={"C": 5, "W": 1})
        self.checkpoint(game)
        game = self.cast_doll(game, source, "B", reserve={"W": 1})
        self.tap_to_damage(game, source)
        self.cast_for(game, "A", blink, [source.ref])
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose_entry_player(game, "C")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(0, game.state.cards[source.object_id].marked_damage)
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_departed_chosen_player_cannot_receive_the_waiting_damage(self):
        game = self.session(60724040)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        bolt = self.add(game.engine, "Lightning Bolt", seat="C", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, bolt)
        self.prepare(game, mana={"C": 5})
        game.state.players["C"].mana_pool["R"] = 1
        self.checkpoint(game)
        game = self.cast_doll(game, source, "B")
        self.cast_for(game, "C", bolt, [source.ref])
        self.resolve_top(game)
        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "concede", "confirm_concede": True})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.players["B"].in_game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        self.assertEqual([40, 40, 40], [game.state.players[seat].life for seat in "ACD"])
        self.replay(game, load=True)
