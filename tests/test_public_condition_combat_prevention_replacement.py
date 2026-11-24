"""Resolve one selected combat-prevention shield for the original Winds."""

from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.resolution_conditions import validate_resolution_condition_instruction
from quorune.rules.capabilities import load_default_capability_registry
from quorune.damage_prevention_scope import public_combat_prevention_replacement_side
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Winds of Qal Sisma"


class PublicConditionCombatPreventionReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "public-condition-combat-prevention-replacement.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "public-condition-combat-prevention-replacement-cards.json",
                                fixtures / "combat-prevention-source-power-ceiling-cards.json",
                                fixtures / "opponent-life-gain-to-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Conditional combat prevention", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_original_binds_one_current_power_condition_and_only_the_two_shield_scopes(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        record = self.db.lookup(ORIGINAL, fuzzy=False)
        spell, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual("resolve", spell.event)
        wrapper, = spell.effects
        self.assertEqual(9, wrapper["schema_version"])
        self.assertIn("damage.prevention.persistent_amount", spell.capability_dependencies)
        self.assertIn("condition.creature.fixed_numeric_exists", spell.capability_dependencies)
        condition, (replacement,) = validate_resolution_condition_instruction(wrapper)
        self.assertEqual(4, condition.predicate.numeric_characteristic.value)
        normal, = wrapper["otherwise_effects"]
        self.assertEqual("opponent_creatures",
                         public_combat_prevention_replacement_side(replacement, source_binding="$source"))
        self.assertEqual("all", public_combat_prevention_replacement_side(normal, source_binding="$source"))
        swapped = {**wrapper, "effects": [normal], "otherwise_effects": [replacement]}
        wrong_origin = {**wrapper, "effects": [{**replacement, "source": "$target.0"}]}
        duplicate = {**wrapper, "otherwise_effects": [normal, normal]}
        missing_scope = {**wrapper, "effects": [{key: value for key, value in replacement.items() if key != "scope"}]}
        wrong_condition = deepcopy(wrapper["condition"])
        wrong_condition["predicate"]["numeric_characteristic"]["comparison"] = "at_most"
        for invalid in (swapped, wrong_origin, duplicate, missing_scope, {**wrapper, "condition": wrong_condition}):
            with self.assertRaises(ValueError):
                validate_resolution_condition_instruction(invalid)

    def test_actual_response_growth_selects_the_opponent_shield_even_after_the_creature_leaves(self):
        game = self.session(61415001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        qualifier = self.add(game.engine, "Centaur Courser")
        attacker = self.add(game.engine, "Elvish Mystic")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        attacker.acquired_control_turn_count = -1
        for card in (source, qualifier, attacker, growth, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 1, "U": 1})
        self.cast_for(game, "A", growth, [qualifier.ref])
        self.resolve_top(game)
        self.assertEqual(6, game.engine._numeric_stat(qualifier.object_id, "power"))
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(("A", "opponent"), (shield.controller, shield.scope.source_controller_relation))
        self.cast_for(game, "A", bounce, [qualifier.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[qualifier.object_id].zone)
        game = self.replay(game, load=True)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(39, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_response_removal_selects_global_even_if_a_later_creature_qualifies(self):
        game = self.session(61415002)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        removed = self.add(game.engine, "Rumbling Baloth")
        attacker = self.add(game.engine, "Centaur Courser")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        attacker.acquired_control_turn_count = -1
        for card in (source, removed, attacker, growth, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 2, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"G": 1, "U": 1})
        self.cast_for(game, "A", bounce, [removed.ref])
        self.resolve_top(game)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual("any", shield.scope.source_controller_relation)
        self.cast_for(game, "A", growth, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual(6, game.engine._numeric_stat(attacker.object_id, "power"))
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_nonactive_caster_protects_another_player_from_the_active_opponents_attack(self):
        game = self.session(61415003)
        source = self.add(game.engine, ORIGINAL, seat="B", zone="hand")
        attacker = self.add(game.engine, "Centaur Courser")
        qualifier = self.add(game.engine, "Rumbling Baloth", seat="B")
        for index in range(2):
            self.add(game.engine, "Forest", seat="B", ref=f"spell-prevention-forest-{index}")
        attacker.acquired_control_turn_count = -1
        for card in (source, attacker, qualifier):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(attacker, "C")])
        self.cast_for(game, "B", source)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(("B", "opponent"), (shield.controller, shield.scope.source_controller_relation))
        self.assertEqual("A", game.state.active_player)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(40, game.state.players["C"].life)
        self.replay(game, load=True)
