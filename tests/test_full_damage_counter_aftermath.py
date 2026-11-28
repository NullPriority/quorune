"""Actual targeted shields count even unpreventable damage at their application."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import PropertyMock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.damage_modifier_state import DamagePreventionShield
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.prevention_component_shapes import TARGET_FULL_COUNTER_CAPABILITY, TARGET_FULL_COUNTER_TEMPLATE
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FullDamageCounterAftermathTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "full-damage-counter-aftermath.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "unpreventable-damage-cards.json",
            fixtures / "full-damage-counter-aftermath-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Instant-speed counter aftermath", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def _exercise_damage(self, *, unpreventable, replay=True):
        game = self.session(61512010 + unpreventable)
        source = self.add(game.engine, "Gatta and Luzzu", zone="hand")
        creature = self.add(game.engine, "Grizzly Bears", ref="shielded-object")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="other-seat-object")
        shocks = [self.add(game.engine, "Shock", seat="B", zone="hand", ref=f"aftermath-shock-{index}")
                  for index in range(3)]
        if unpreventable:
            leyline = self.add(game.engine, "Leyline of Punishment", seat="D")
            self.assert_closed(game, leyline)
        for card in (source, creature, *shocks):
            self.assert_closed(game, card)
        self.prepare(game, mana={"W": 1, "C": 2})
        game.state.players["B"].mana_pool.update({"R": 3})
        self.checkpoint(game)
        self.cast_for(game, "B", shocks[0], [creature.ref])
        self.cast_for(game, "A", source)
        self.target(game, "A", [creature.ref])
        self.resolve_top(game)
        shield, = game.state.damage_prevention_shields
        self.assertEqual(shield, DamagePreventionShield.from_dict(shield.to_dict()))
        if replay:
            game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.cast_for(game, "B", shocks[1], [creature.ref])
        self.resolve_top(game)
        self.cast_for(game, "B", shocks[2], [other.ref])
        self.resolve_top(game)
        live = game.state.cards[creature.object_id]
        self.assertEqual("battlefield", live.zone)
        self.assertEqual(4, live.counters.get("+1/+1", 0))
        self.assertEqual(4 if unpreventable else 0, live.marked_damage)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        if replay:
            self.replay(game, load=True)

    def test_actual_flash_and_repeated_prevention_place_counters_for_one_creature_and_replay(self):
        self._exercise_damage(unpreventable=False)

    def test_actual_unpreventable_damage_still_places_counters_before_state_actions_and_replays(self):
        self._exercise_damage(unpreventable=True)

    def test_unpreventable_amount_omission_mutant_loses_the_original_creature(self):
        with patch.object(DamagePreventionShield, "tracks_unpreventable_amount", new_callable=PropertyMock, return_value=False):
            with self.assertRaisesRegex(AssertionError, "battlefield"):
                self._exercise_damage(unpreventable=True, replay=False)

    def test_exact_trigger_rejects_crossed_recipient_and_undeclared_aftermath(self):
        report, semantics = register_card(self.db, self.registry, "Gatta and Luzzu")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [item for item in semantics.programs_for_oracle(report["oracle_id"])
                    if item.provenance.get("template_id") == TARGET_FULL_COUNTER_TEMPLATE]
        dependencies = tuple(cap for cap in program.capability_dependencies if cap != TARGET_FULL_COUNTER_CAPABILITY)
        omitted = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
        effect = dict(program.effects[0])
        counter = dict(effect["aftermath"][0])
        crossed = replace(program, effects=({**effect, "aftermath": [{**counter, "subject": "$source"}]},))
        for candidate in (omitted, crossed):
            bound = bind_semantic_program_runtime(candidate, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"], bound)
