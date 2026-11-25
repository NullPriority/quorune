"""Temporary combat prevention reads effective source power when damage would occur."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_all_damage_prevention import fixed_all_damage_prevention_specs
from quorune.damage_modifier_state import DamageModifierError, DamagePreventionScope
from quorune.deck import DeckDefinition, DeckEntry
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Vine Snare", "Fog of War")


class CombatPreventionSourcePowerCeilingTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "combat-prevention-source-power-ceiling.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "combat-prevention-source-power-ceiling-cards.json",
                                fixtures / "opponent-life-gain-to-loss-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Combat source power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        return semantics.programs_for_oracle(self.db.lookup(name, fuzzy=False).oracle_id)

    def test_both_complete_originals_bind_the_temporary_power_scope_and_fog_retains_life(self):
        for name, maximum, kinds in (
            ("Vine Snare", 4, ("create_damage_prevention_shield",)),
            ("Fog of War", 3, ("life", "create_damage_prevention_shield")),
        ):
            with self.subTest(name=name):
                spell, = self.programs(name)
                self.assertEqual("resolve", spell.event)
                self.assertEqual(kinds, tuple(effect["op"] for effect in spell.effects))
                shield = spell.effects[-1]
                self.assertEqual(("all", "combat", "until_end_of_turn", "*"),
                                 (shield["mode"], shield["damage_kind"], shield["duration"], shield["subject"]))
                self.assertEqual(maximum, DamagePreventionScope.from_dict(shield["scope"]).source_power_max)
        sentence = "Prevent all combat damage that would be dealt this turn by creatures with power 4 or less."
        for invalid in (sentence.replace("combat ", ""), sentence.replace("this turn ", ""),
                        sentence.replace("or less", "or greater"), sentence.replace("4", "X"),
                        sentence.replace("creatures", "target creatures")):
            self.assertIsNone(fixed_all_damage_prevention_specs(invalid, card_name="Example"))

    def test_scope_omission_retains_old_layout_and_inclusive_current_membership(self):
        legacy = DamagePreventionScope(source_characteristics_all=("creature",))
        encoded = legacy.to_dict()
        self.assertNotIn("source_power_max", encoded)
        self.assertEqual(encoded, DamagePreventionScope.from_dict(encoded).to_dict())
        scoped = DamagePreventionScope.from_dict({**encoded, "source_power_max": 4})
        self.assertEqual((4, scoped), (scoped.to_dict()["source_power_max"],
                                      DamagePreventionScope.from_dict(scoped.to_dict())))
        values = (("below", 3), ("equal", 4), ("above", 5))
        self.assertEqual({"in": ["below", "equal"]},
                         scoped.event_conditions(controller="A", source_powers=values)["source_logical_object_id"])
        with self.assertRaises(DamageModifierError):
            scoped.event_conditions(controller="A")
        for invalid in (None, True, -1, "4"):
            with self.assertRaises(DamageModifierError):
                DamagePreventionScope.from_dict({**encoded, "source_power_max": invalid})
        with self.assertRaises(DamageModifierError):
            DamagePreventionScope.from_dict({**encoded, "source_power_max": 4, "source_ref": "other"})

    def test_actual_vine_rechecks_growth_shrink_and_equality_when_combat_damage_occurs(self):
        game = self.session(60907104)
        source = self.add(game.engine, "Vine Snare", zone="hand")
        small = self.add(game.engine, "Centaur Courser")
        equal = self.add(game.engine, "Rumbling Baloth")
        large = self.add(game.engine, "Craw Wurm")
        pinger = self.add(game.engine, "Prodigal Sorcerer")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        diminish = self.add(game.engine, "Diminish", zone="hand")
        self.add(game.engine, "Forest")
        self.add(game.engine, "Island")
        for card in (small, equal, large, pinger):
            card.acquired_control_turn_count = -1
        for card in (source, small, equal, large, pinger, growth, diminish):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(4, shield.scope.source_power_max)

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [row for row in actions if row["id"].startswith(f"activate:{pinger.ref}:")]
        result = game.act("pilot:A", {"action_id": activation["id"], "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        self.assertEqual(39, game.state.players["D"].life)

        self.attack(game, [(small, "B"), (equal, "C"), (large, "D")])
        self.cast_for(game, "A", growth, [small.ref])
        self.resolve_top(game)
        self.cast_for(game, "A", diminish, [large.ref])
        self.resolve_top(game)
        self.assertEqual([6, 4, 1], [game.engine._numeric_stat(card.object_id, "power")
                                    for card in (small, equal, large)])
        game = self.replay(game, load=True)
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual([40, 34, 40, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.until(game, lambda current: current.state.active_player == "B")
        self.assertEqual([], game.state.damage_prevention_shields)
        self.replay(game, load=True)

    def test_actual_fog_finishes_its_current_life_count_and_choice_before_arming_the_shield(self):
        game = self.session(60907103)
        source = self.add(game.engine, "Fog of War", zone="hand")
        small = self.add(game.engine, "Centaur Courser")
        above = self.add(game.engine, "Rumbling Baloth")
        removed = self.add(game.engine, "Elvish Mystic", seat="B")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        doubler = self.add(game.engine, "Boon Reflection")
        converter = self.add(game.engine, "Tainted Remedy", seat="B")
        private = self.add(game.engine, "Humble", seat="D", zone="hand", ref="power-fog-private")
        for card in (small, above):
            card.acquired_control_turn_count = -1
        for card in (source, small, above, removed, bounce, doubler, converter):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "G": 1, "U": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, reserve={"U": 1})
        self.cast_for(game, "A", bounce, [removed.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[removed.object_id].zone)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertEqual([], game.state.damage_prevention_shields)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice = next(option for option in options if "multiply life gained" in option["label"])
        command = {"action_id": "choose", "replacement": choice["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(36, game.state.players["A"].life)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(3, shield.scope.source_power_max)

        self.attack(game, [(small, "B"), (above, "C")])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual([36, 40, 36, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)
