"""Native evidence for a linked any-player entry and recipient damage quantity."""

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


ORIGINAL = "Sawhorn Nemesis"


class EntryChosenPlayerDamageQuantityTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "entry-chosen-player-damage-quantity.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/entry-chosen-player-damage-quantity-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Chosen player damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self):
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        all_programs = semantics.programs_for_oracle(original.oracle_id)
        entry, = [program for program in all_programs if program.event == "zone.change"]
        damage, = [program for program in all_programs if program.event == "damage"]
        return original, entry, damage

    def test_original_binds_one_public_entry_and_its_current_damage_consumer(self):
        original, entry, damage = self.programs()
        self.assertEqual(("all", "battlefield"), (entry.active_zone, damage.active_zone))
        self.assertIn(CURRENT_ABILITY_FRAGMENT_COVERAGE, damage.coverage)
        self.assertTrue(all(not program.target_schema and not program.effects and len(program.handlers) == 1
                            for program in (entry, damage)))
        for text in (
            original.oracle_text.replace("choose a player.", "choose an opponent."),
            original.oracle_text.replace("deals double", "deals triple"),
            original.oracle_text.replace("a permanent they control", "a permanent they own"),
            original.oracle_text.splitlines()[0],
            original.oracle_text.splitlines()[1],
            original.oracle_text + " Rewrite history.",
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status, text)

    def test_raw_linked_damage_cannot_drop_current_ability_or_consume_a_different_program(self):
        _, entry, damage = self.programs()
        for changed in (
            replace(entry, handlers=[]),
            replace(damage, active_zone="command"),
            replace(damage, handlers=[]),
            replace(damage, coverage=tuple(tag for tag in damage.coverage if tag != CURRENT_ABILITY_FRAGMENT_COVERAGE)),
        ):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(binding["strict"], binding)

    def choose_entry_player(self, game, entering, player):
        choice = game.state.pending_decision
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        offered = {row["id"].rsplit(":", 1)[-1]: row["id"] for row in options}
        self.assertEqual(set("ABCD"), set(offered))
        self.assertEqual("stack" if entering.zone == "stack" else "exile", game.state.cards[entering.object_id].zone)
        state_hash = authoritative_state_hash(game.state)
        denied = game.act("pilot:D", {"action_id": "choose", "replacement": offered[player]})
        self.assertFalse(denied.ok)
        self.assertEqual(state_hash, authoritative_state_hash(game.state))
        denied = game.act("pilot:A", {"action_id": "choose", "replacement": offered[player][:-1] + "outside"})
        self.assertFalse(denied.ok)
        self.assertEqual(state_hash, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": offered[player]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("battlefield", game.state.cards[entering.object_id].zone)

    def test_actual_self_choice_uses_affected_player_ordering_and_keeps_the_original_damage_source(self):
        game = self.session(614120510)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        healer = self.add(game.engine, "Samite Healer")
        healer.acquired_control_turn_count = -1
        for card in (source, shot, healer):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose_entry_player(game, game.state.cards[source.object_id], "A")
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activate, = [row["id"] for row in rows if row["id"].startswith(f"activate:{healer.ref}:")]
        accepted = game.act("pilot:A", {"action_id": activate, "targets": ["A"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.cast_for(game, "B", shot, ["A"])
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        by_double, = [row["id"] for row in options if row["id"] != shield.effect_id]
        for selected, amount in ((shield.effect_id, 2), (by_double, 3)):
            with self.subTest(first=selected):
                branch = self.replay(game, load=True)
                applied = branch.act("pilot:A", {"action_id": "choose", "replacement": selected})
                self.assertTrue(applied.ok, applied.summary)
                self.assertIsNone(self.resolve(branch))
                self.assertEqual(40 - amount, branch.state.players["A"].life)
                damages = branch.engine._current_turn_history("player_damaged")
                self.assertEqual([("B", "A", amount)], [(event.actor, event.target, event.amount) for event in damages])
                self.replay(branch, load=True)

    def test_actual_source_control_change_keeps_the_chosen_player_and_reads_recipient_control(self):
        game = self.session(614120520)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        donate = self.add(game.engine, "Donate", zone="hand")
        chosen_creature = self.add(game.engine, "Giant Spider", seat="B")
        other_creature = self.add(game.engine, "Giant Spider", ref="sawhorn-unchosen-spider")
        first = self.add(game.engine, "Shock", seat="D", zone="hand", ref="sawhorn-chosen-damage")
        second = self.add(game.engine, "Shock", seat="D", zone="hand", ref="sawhorn-unchosen-damage")
        third = self.add(game.engine, "Shock", seat="D", zone="hand", ref="sawhorn-blanked-damage")
        blank = self.add(game.engine, "Lignify", zone="hand")
        for card in (source, donate, chosen_creature, other_creature, first, second, third, blank):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "R": 1, "U": 1, "G": 1})
        game.state.players["D"].mana_pool["R"] = 3
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 3, "U": 1, "G": 1})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose_entry_player(game, game.state.cards[source.object_id], "B")
        self.cast_for(game, "A", donate, ["C", source.ref], reserve={"C": 1, "G": 1})
        self.resolve_top(game)
        self.assertEqual("C", game.state.cards[source.object_id].controller)
        self.cast_for(game, "D", first, [chosen_creature.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[chosen_creature.object_id].zone)
        self.cast_for(game, "D", second, [other_creature.ref])
        self.resolve_top(game)
        self.assertEqual(("battlefield", 2), (game.state.cards[other_creature.object_id].zone,
                                             game.state.cards[other_creature.object_id].marked_damage))
        self.assertEqual([40, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.cast_for(game, "A", blank, [source.ref])
        self.resolve_top(game)
        self.cast_for(game, "D", third, ["B"])
        self.resolve_top(game)
        self.assertEqual([40, 38, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_blink_chooses_again_and_only_the_new_recipient_gets_doubled_damage(self):
        game = self.session(614120530)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        first = self.add(game.engine, "Shock", seat="D", zone="hand", ref="sawhorn-old-player")
        second = self.add(game.engine, "Shock", seat="D", zone="hand", ref="sawhorn-new-player")
        for card in (source, blink, first, second):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 1, "W": 1})
        game.state.players["D"].mana_pool["R"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"W": 1})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose_entry_player(game, game.state.cards[source.object_id], "B")
        first_entry = game.state.cards[source.object_id].logical_object_id
        self.cast_for(game, "A", blink, [source.ref])
        self.assertEqual("replacement.order", self.resolve(game).kind)
        game = self.replay(game, load=True)
        self.choose_entry_player(game, game.state.cards[source.object_id], "C")
        self.assertNotEqual(first_entry, game.state.cards[source.object_id].logical_object_id)
        self.cast_for(game, "D", first, ["B"])
        self.resolve_top(game)
        self.cast_for(game, "D", second, ["C"])
        self.resolve_top(game)
        self.assertEqual([40, 38, 36, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)


    def test_actual_fully_prevented_damage_does_not_apply_the_quantity_replacement(self):
        game = self.session(614120540)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        shield_spell = self.add(game.engine, "Candles' Glow", zone="hand")
        shot = self.add(game.engine, "Shock", seat="B", zone="hand")
        for card in (source, shield_spell, shot):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"C": 1, "W": 1})
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose_entry_player(game, game.state.cards[source.object_id], "A")
        self.cast_for(game, "A", shield_spell, ["A"])
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.cast_for(game, "B", shot, ["A"])
        choice = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (choice.kind, choice.actors))
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        doubler, = [row["id"] for row in rows if row["id"] != shield.effect_id]
        game = self.replay(game, load=True)
        last_event = game.state.event_sequence
        accepted = game.act("pilot:A", {"action_id": "choose", "replacement": shield.effect_id})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(42, game.state.players["A"].life)
        self.assertFalse(game.engine._current_turn_history("player_damaged"))
        replaced = [event.details.get("effect_id") for event in game.state.events
                    if event.event_id > last_event and event.code == "replacement.apply"]
        self.assertNotIn(doubler, replaced)
        self.replay(game, load=True)
