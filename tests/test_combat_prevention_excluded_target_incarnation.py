"""The chosen original creature is exempt from one resolved combat shield."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.damage_prevention_scope import DamageModifierError, DamagePreventionScope
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.source_prevention import (
    EXCLUDED_SOURCE_TARGET, EXCLUDED_SOURCE_TARGET_VERSION, source_block_combat_runtime_dependencies,
    source_prevention_dependencies, targeted_source_exclusion_schema_is_closed,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Terrifying Presence"


class CombatPreventionExcludedTargetIncarnationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "combat-prevention-excluded-target-incarnation.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "combat-prevention-excluded-target-incarnation-cards.json",
            fixtures / "combat-prevention-source-power-ceiling-cards.json",
            fixtures / "opponent-life-gain-self-counters-cards.json",
            fixtures / "opponent-life-gain-to-loss-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Exempt original combat source", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_uses_one_creature_target_and_the_original_damage_scope(self):
        bound, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        spell, = semantics.programs_for_oracle(self.db.lookup(ORIGINAL, fuzzy=False).oracle_id)
        self.assertTrue(targeted_source_exclusion_schema_is_closed(spell.target_schema))
        self.assertIn("target.revalidate_resolution", spell.capability_dependencies)
        effect, = spell.effects
        self.assertEqual(("$target.0", 1), (effect[EXCLUDED_SOURCE_TARGET], effect[EXCLUDED_SOURCE_TARGET_VERSION]))
        for invalid in (
            {key: value for key, value in effect.items() if key != EXCLUDED_SOURCE_TARGET_VERSION},
            {**effect, EXCLUDED_SOURCE_TARGET: "$target.1"},
            {**effect, EXCLUDED_SOURCE_TARGET_VERSION: True},
            {**effect, "damage_kind": "noncombat"},
        ):
            with self.assertRaises(ValueError):
                source_prevention_dependencies(invalid)
        with self.assertRaises(ValueError):
            source_block_combat_runtime_dependencies(replace(spell, target_schema={**spell.target_schema, "count": 2}))
        historical = DamagePreventionScope(source_characteristics_all=("creature",)).to_dict()
        field = "excluded_source_logical_object_id"
        self.assertNotIn(field, historical)
        scoped = DamagePreventionScope.from_dict({**historical, field: "original-creature"})
        self.assertEqual({"not_in": [None, "original-creature"]},
                         scoped.event_conditions(controller="A")["source_logical_object_id"])
        self.assertEqual(scoped, DamagePreventionScope.from_dict(scoped.to_dict()))
        for invalid in (None, "", True, "$target.0"):
            with self.assertRaises(DamageModifierError):
                DamagePreventionScope.from_dict({**historical, field: invalid})

    def test_actual_original_target_deals_combat_damage_but_the_returned_incarnation_cannot(self):
        for blink in (False, True):
            with self.subTest(blink=blink):
                game = self.session(60820010 + blink)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                exempt = self.add(game.engine, "Raging Goblin")
                other = self.add(game.engine, "Centaur Courser")
                other.acquired_control_turn_count = -1
                if blink:
                    response = self.add(game.engine, "Cloudshift", zone="hand")
                    self.assert_closed(game, response)
                for card in (source, exempt, other):
                    self.assert_closed(game, card)
                self.prepare(game, mana={"C": 1, "G": 1, "W": int(blink)})
                self.checkpoint(game)

                self.priority(game, "A")
                action = self.action(game, "A", source)
                before = authoritative_state_hash(game.state)
                invalid = game.act("pilot:A", {"action_id": action["id"], "targets": ["B"], "pay": "auto"})
                self.assertFalse(invalid.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.cast_for(game, "A", source, [exempt.ref], reserve={"W": 1} if blink else None)
                self.resolve_top(game)
                original_id = game.state.cards[exempt.object_id].logical_object_id
                shield, = game.state.damage_prevention_shields
                self.assertEqual(original_id, shield.scope.excluded_source_logical_object_id)

                if blink:
                    self.cast_for(game, "A", response, [exempt.ref])
                    self.resolve_top(game)
                    current = game.state.cards[exempt.object_id]
                    self.assertEqual("battlefield", current.zone)
                    self.assertNotEqual(original_id, current.logical_object_id)
                game = self.replay(game, load=True)
                current = game.state.cards[exempt.object_id]
                self.assertEqual(original_id, game.state.damage_prevention_shields[0].scope.excluded_source_logical_object_id)
                self.attack(game, [(current, "B"), (other, "C")])
                self.until(game, lambda current_game: current_game.state.phase == "postcombat_main")
                self.assertEqual((40 if blink else 39, 40), tuple(game.state.players[seat].life for seat in "BC"))
                self.replay(game, load=True)

    def test_actual_illegal_target_at_resolution_prevents_the_entire_spell_from_creating_a_shield(self):
        game = self.session(60820012)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        exempt = self.add(game.engine, "Raging Goblin")
        attacker = self.add(game.engine, "Centaur Courser")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        attacker.acquired_control_turn_count = -1
        for card in (source, exempt, attacker, bounce):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "G": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [exempt.ref], reserve={"U": 1})
        self.cast_for(game, "A", bounce, [exempt.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[exempt.object_id].zone)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertEqual([], game.state.damage_prevention_shields)
        game = self.replay(game, load=True)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(37, game.state.players["B"].life)
        self.replay(game, load=True)
