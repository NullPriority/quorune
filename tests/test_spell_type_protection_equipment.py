"""Both printed Protection qualities coexist with the complete combat trigger."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Sword of Wealth and Power"


class SpellTypeProtectionEquipmentTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "equipment-spell-type-protection.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/spell-type-protection-equipment-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Equipment spell type protection", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_independent_spell_types_and_both_combat_results(self):
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        rows = semantics.programs_for_oracle(record.oracle_id)
        attached, = (row for row in rows if any("add_ability_fragments" in handler.get("modifier", {})
                                               for handler in row.handlers))
        modifier = attached.handlers[0]["modifier"]
        self.assertEqual((2, 2), (modifier["power"], modifier["toughness"]))
        self.assertEqual({("protection", "card_type", "instant"), ("protection", "card_type", "sorcery")}, {
            (fragment["kind"], fragment["value"]["quality_kind"], fragment["value"]["quality"])
            for fragment in modifier["add_ability_fragments"]
        })
        combat, = (row for row in rows if row.event == "damage.dealt")
        self.assertIsNone(combat.target_schema)
        token, copy = combat.effects
        self.assertEqual(("create_token", "Treasure Token", 1), (token["op"], token["name"], token["quantity"]))
        self.assertEqual(("delayed_trigger", "instant_or_sorcery", 1),
                         (copy["op"], copy["next_cast_copy"]["scope"], copy["next_cast_copy"]["copies"]))

    def start(self, seed):
        game = self.session(seed)
        sword = self.add(game.engine, ORIGINAL, zone="hand")
        carrier = self.add(game.engine, "Runeclaw Bear", ref="wealth-power-carrier")
        carrier.acquired_control_turn_count = -1
        self.assert_closed(game, sword)
        return game, sword, carrier

    def prepare_and_equip(self, game, sword, carrier):
        self.prepare(game, mana={"C": 5})
        self.checkpoint(game)
        self.cast_for(game, "A", sword, reserve={"C": 2})
        self.resolve_top(game)
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        equip, = [row for row in actions if row["id"].startswith(f"activate:{sword.ref}:")]
        result = game.act("pilot:A", {"action_id": equip["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(carrier.object_id, game.state.cards[sword.object_id].attached_to)
        self.assertEqual(4, game.engine._numeric_stat(carrier.object_id, "power"))

    def opposing_main(self, game):
        self.until(game, lambda current: current.state.active_player == "B" and current.state.phase == "precombat_main")
        self.priority(game, "B")

    def test_actual_instant_and_sorcery_targets_are_denied_but_creature_ability_is_legal(self):
        game, sword, carrier = self.start(70216041)
        instant = self.add(game.engine, "Lightning Bolt", seat="B", zone="hand")
        sorcery = self.add(game.engine, "Strangle", seat="B", zone="hand")
        creature = self.add(game.engine, "Prodigal Pyromancer", seat="B")
        creature.acquired_control_turn_count = -1
        open_target = self.add(game.engine, "Runeclaw Bear", seat="C", ref="unprotected-bear")
        self.add(game.engine, "Mountain", seat="B")
        for card in (instant, sorcery, creature):
            self.assert_closed(game, card)
        self.prepare_and_equip(game, sword, carrier)
        self.opposing_main(game)
        for spell in (instant, sorcery):
            offer = self.action(game, "B", spell)
            legal = offer["target_schema"]["legal_refs"]
            self.assertIn(open_target.ref, legal)
            self.assertNotIn(carrier.ref, legal)
            before = authoritative_state_hash(game.state)
            result = game.act("pilot:B", {"action_id": offer["id"], "targets": [carrier.ref], "pay": "auto"})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability, = [row for row in actions if row["id"].startswith(f"activate:{creature.ref}:")]
        self.assertIn(carrier.ref, ability["target_schema"]["legal_refs"])
        result = game.act("pilot:B", {"action_id": ability["id"], "targets": [carrier.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[carrier.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_untargeted_sorcery_damage_is_prevented_only_for_the_equipped_creature(self):
        game, sword, carrier = self.start(70216042)
        spell = self.add(game.engine, "Pyroclasm", seat="B", zone="hand")
        open_target = self.add(game.engine, "Runeclaw Bear", seat="C", ref="unprotected-creature")
        self.add(game.engine, "Mountain", seat="B")
        self.add(game.engine, "Wastes", seat="B")
        self.assert_closed(game, spell)
        self.prepare_and_equip(game, sword, carrier)
        self.opposing_main(game)
        self.cast_for(game, "B", spell)
        self.resolve_top(game)
        self.assertEqual(("battlefield", 0), (game.state.cards[carrier.object_id].zone,
                                             game.state.cards[carrier.object_id].marked_damage))
        self.assertEqual("graveyard", game.state.cards[open_target.object_id].zone)
        self.replay(game, load=True)

    def waiting(self, game):
        return [row for row in game.state.delayed_triggers if row.active and "next_cast_copy" in row.condition]

    def treasure_count(self, game):
        return sum(card.is_token and card.zone == "battlefield" and card.controller == "A"
                   and card.printed_name == "Treasure Token" for card in game.state.cards.values())

    def test_actual_treasures_pay_for_first_copied_spell_and_the_next_spell_is_uncopied(self):
        game, sword, carrier = self.start(70216043)
        first = self.add(game.engine, "Lightning Bolt", zone="hand", ref="first-treasure-bolt")
        second = self.add(game.engine, "Lightning Bolt", zone="hand", ref="second-treasure-bolt")
        doubling = self.add(game.engine, "Doubling Season")
        for card in (first, doubling):
            self.assert_closed(game, card)
        self.prepare_and_equip(game, sword, carrier)
        self.attack(game, [(carrier, "B")])
        self.until(game, lambda current: self.trigger_count(current, sword) == 1)
        self.assertEqual(36, game.state.players["B"].life)
        self.resolve_top(game)
        self.assertEqual(2, self.treasure_count(game))
        self.assertEqual(1, len(self.waiting(game)))
        self.cast_for(game, "A", first, ["B"])
        self.assertEqual(1, self.treasure_count(game))
        self.assertFalse(self.waiting(game))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        command = {"action_id": "choose", "targets": ["C"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        copy, = [row for row in game.state.stack if row.kind == "spell_copy"]
        self.assertEqual(["C"], copy.targets)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((33, 37), (game.state.players["B"].life, game.state.players["C"].life))
        self.cast_for(game, "A", second, ["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, self.treasure_count(game))
        self.assertFalse(self.waiting(game))
        self.assertEqual(37, game.state.players["D"].life)
        self.replay(game, load=True)
