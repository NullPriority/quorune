"""Equipment combat damage binds separate discard and untap controllers."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler.affected_player_discard_templates import EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Feast and Famine"


class EquippedCombatPlayerDiscardUntapTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "equipped-combat-player-discard-untap.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/equipped-combat-player-discard-untap-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipped combat discard and untap", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_the_exact_untargeted_discard_and_actor_land_set(self):
        bound, semantic = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        programs = semantic.programs_for_oracle(record.oracle_id)
        trigger, = (row for row in programs if row.event == "damage.dealt")
        self.assertGreater(len(programs), 1)
        self.assertIsNone(trigger.target_schema)
        self.assertIn(EQUIPPED_DAMAGE_DISCARD_UNTAP_MECHANIC, trigger.coverage)
        discard, untap = trigger.effects
        self.assertEqual(("choose_cards_apnap", ["$context.target"], 1, True),
                         (discard["op"], discard["players"], discard["count"], discard["hidden"]))
        self.assertEqual(("set_public_tap_state", False, "actor", ["land"]),
                         (untap["op"], untap["tapped"], untap["set"]["controller_relation"],
                          untap["set"]["query"]["types_all"]))
        bad_recipient = {**discard, "players": ["$controller"]}
        for changes in (
            {"effects": [discard]}, {"effects": [bad_recipient, untap]},
            {"effects": [discard, {**untap, "tapped": True}]},
            {"event_condition": {"field": "target_kind", "op": "eq", "value": "player"}},
        ):
            result = bind_semantic_program_runtime(
                replace(trigger, **changes), capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(result["strict"], result)
        for text in (
            record.oracle_text.replace("a card and", "two cards and"),
            record.oracle_text.replace("all lands you control", "all creatures you control"),
            record.oracle_text.replace("combat damage", "damage"),
        ):
            self.assertNotEqual("exact", compile_oracle_card(
                replace(record, oracle_text=text), capability_registry=self.registry,
            ).status)

    def scene(self, seed, *, empty=False, remove_source=False, walker=False, lethal=False):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="feast-sword-carrier")
        carrier.acquired_control_turn_count = -1
        land = self.add(game.engine, "Forest", ref="feast-original-controller-land")
        outside = self.add(game.engine, "Forest", seat="C", ref="feast-other-controller-land")
        outside.tapped = True
        if lethal:
            game.state.players["B"].life = 4
        if empty:
            for object_id in tuple(game.state.players["B"].zones["hand"]):
                game.engine.move_card(object_id, "exile", log=False, semantic_events=False)
        victim_card = None if empty else self.add(
            game.engine, "Runeclaw Bear", seat="B", zone="hand", ref="feast-private-discard",
        )
        removal = None
        if remove_source:
            removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
            self.add(game.engine, "Plains", seat="B", ref="feast-response-plains")
            self.add(game.engine, "Island", seat="B", ref="feast-response-island")
            self.assert_closed(game, removal)
        defender = "B"
        if walker:
            other = self.add(game.engine, "Jace Beleren", seat="B")
            other.counters["loyalty"] = 3
            defender = other.ref
        self.assert_closed(game, sword)
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast(game, "A", sword)
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip = next(row for row in actions if row["id"].startswith(f"activate:{sword.ref}:"))
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))
        self.attack(game, [(carrier, defender)])
        return game, sword, land, outside, victim_card, removal

    def tap_land(self, game, land):
        self.priority(game, "A")
        current = game.state.cards[land.object_id]
        ability = next(row for row in game.engine._activated_abilities(current) if row.mana_ability)
        output, = ability.fixed_mana_outputs
        result = game.act("pilot:A", {"action_id": f"activate:{land.ref}:{ability.ability_id}",
                                     "mana_output": dict(output.bundle), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[land.object_id].tapped)

    def test_actual_damaged_player_discards_privately_then_original_equipment_controller_untaps(self):
        game, sword, land, outside, chosen, removal = self.scene(60330121, remove_source=True)
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        self.assertEqual(36, game.state.players["B"].life)
        self.tap_land(game, land)
        self.cast_for(game, "B", removal, [sword.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[sword.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (pending.kind, pending.actors))
        context = game.packet("pilot:B", full=True)["decision"]["ctx"]
        self.assertEqual(1, context["count"])
        self.assertIn(chosen.ref, context["options"])
        self.assertTrue(game.state.cards[land.object_id].tapped)
        for viewer in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(chosen.ref, json.dumps(game.packet(viewer, full=True)))
        choice = {"action_id": "choose", "cards": [chosen.ref]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", choice).ok)
        self.assertFalse(game.act("pilot:B", {**choice, "cards": []}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:B", choice)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assertTrue(game.state.cards[outside.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_empty_damaged_hand_still_untaps_the_original_controller_land(self):
        game, sword, land, outside, _, _ = self.scene(60330122, empty=True)
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        self.tap_land(game, land)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.players["B"].zones["hand"])
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assertTrue(game.state.cards[outside.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_combat_damage_to_a_planeswalker_offers_neither_result(self):
        game, sword, land, _, chosen, _ = self.scene(60330123, walker=True)
        self.tap_land(game, land)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(0, self.trigger_count(game, sword))
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual("hand", game.state.cards[chosen.object_id].zone)
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_lethal_damage_preserves_untap_after_the_damaged_player_leaves(self):
        game, _, land, outside, _, _ = self.scene(60330124, lethal=True)
        self.tap_land(game, land)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertNotIn("B", game.state.active_seats())
        self.assertFalse(game.state.cards[land.object_id].tapped)
        self.assertTrue(game.state.cards[outside.object_id].tapped)
        self.replay(game, load=True)
