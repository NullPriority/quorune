"""Native first-resolution counter, life gain and source-incarnation evidence."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.ability_resolution_ordinal import (
    ABILITY_RESOLVED_HISTORY,
    FIRST_ABILITY_COUNTER_CONDITION,
    FIRST_ABILITY_COUNTER_VERSION,
    FIRST_ARTIFACT_ENTRY_COUNTER_CAPABILITY,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class FirstArtifactEntryResolutionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "first-artifact-entry-resolution.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/first-artifact-entry-resolution-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("First artifact entry resolution", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def compile(self, card):
        return compile_oracle_card(
            card, capability_registry=self.registry, capability_profile="commander_review",
        )

    def test_exact_artifact_event_and_first_counter_shape_reject_changes(self):
        card = self.db.lookup("Teething Wurmlet", fuzzy=False)
        ir = self.compile(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        obligation, = (node for node in ir.faces[0].nodes if node.event == "artifact.enter")
        self.assertIn(FIRST_ARTIFACT_ENTRY_COUNTER_CAPABILITY, obligation.capability_dependencies)
        self.assertIsNone(obligation.target_schema)
        life, conditional = obligation.effects
        self.assertEqual({"op": "life", "player": "$controller", "delta": 1}, life)
        self.assertEqual(FIRST_ABILITY_COUNTER_VERSION, conditional["schema_version"])
        self.assertEqual(FIRST_ABILITY_COUNTER_CONDITION, conditional["condition"])
        self.assertIsNone(conditional["resolution_ordinal"])
        counter, = conditional["effects"]
        self.assertEqual({"op": "place_counters", "card": "$source.zone_object", "counter": "+1/+1",
                          "amount": 1, "source": "$source"}, counter)
        for old, new in (
            ("an artifact you control", "another artifact you control"),
            ("an artifact you control", "an artifact"),
            ("gain 1 life", "gain 2 life"),
            ("first time", "second time"),
            ("on this creature.", "on target creature."),
        ):
            with self.subTest(changed_span=old, replacement=new):
                self.assertNotEqual("exact", self.compile(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)

        binding, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        program, = (pr for pr in semantics.programs_for_oracle(card.oracle_id) if pr.event == "artifact.enter")
        for mutant in (
            replace(program, event="permanent.enter"),
            replace(program, event_condition=None),
            replace(program, effects=({**life, "delta": 2}, conditional)),
            replace(program, effects=(life, {**conditional, "condition": {**conditional["condition"], "ordinal": 2}})),
            replace(program, effects=(life, {**conditional, "effects": [{**counter, "card": "$source"}]})),
        ):
            with self.subTest(mutant=mutant):
                result = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile="commander_review")
                self.assertFalse(result["strict"], result)

    def build_game(self, seed, *, blink=False):
        game = self.session(seed)
        source = self.add(game.engine, "Teething Wurmlet")
        rival = self.add(game.engine, "Teething Wurmlet", seat="B", ref="rival-wurmlet")
        artifacts = [
            self.add(game.engine, "Ornithopter", zone="hand", ref=f"ordinal-artifact-{i}") for i in range(3)
        ]
        spell = self.add(game.engine, "Cloudshift", zone="hand") if blink else None
        if spell is not None:
            self.add(game.engine, "Plains", ref="blink-plains")
        for card in (source, rival, *artifacts):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        return game, source, rival, artifacts, spell

    def queue_artifact(self, game, source, rival, artifact):
        self.cast_for(game, "A", artifact)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(0, self.trigger_count(game, rival))

    def history(self, game):
        return [entry.object_incarnation for entry in game.engine._current_turn_history(ABILITY_RESOLVED_HISTORY)]

    def test_actual_every_resolution_gains_life_but_only_the_first_places_a_counter(self):
        game, source, rival, artifacts, _ = self.build_game(735432001)
        self.assertNotIn("deathtouch", game.engine._combat_keywords(source))
        for number, artifact in enumerate(artifacts, 1):
            self.queue_artifact(game, source, rival, artifact)
            if number == 1:
                game = self.replay(game, load=True)
            self.resolve_top(game)
            self.assertEqual(40 + number, game.state.players["A"].life)
            self.assertEqual(40, game.state.players["B"].life)
            self.assertEqual({"+1/+1": 1}, game.state.cards[source.object_id].counters)
        self.assertEqual([source.logical_object_id] * 3, self.history(game))
        self.assertIn("deathtouch", game.engine._combat_keywords(game.state.cards[source.object_id]))
        self.replay(game, load=True)

    def test_actual_blink_keeps_the_old_trigger_and_starts_a_fresh_ordinal(self):
        game, source, rival, artifacts, spell = self.build_game(735432002, blink=True)
        previous = source.logical_object_id
        self.queue_artifact(game, source, rival, artifacts[0])
        self.cast_for(game, "A", spell, [source.ref])
        self.resolve_top(game)
        returned = game.state.cards[source.object_id]
        self.assertEqual("battlefield", returned.zone)
        self.assertNotEqual(previous, returned.logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(41, game.state.players["A"].life)
        self.assertFalse(game.state.cards[source.object_id].counters)
        self.assertEqual([previous], self.history(game))

        for number, artifact in enumerate(artifacts[1:], 2):
            self.queue_artifact(game, source, rival, artifact)
            self.resolve_top(game)
            self.assertEqual(40 + number, game.state.players["A"].life)
            self.assertEqual({"+1/+1": 1}, game.state.cards[source.object_id].counters)
        self.assertEqual([previous, returned.logical_object_id, returned.logical_object_id], self.history(game))
        self.replay(game, load=True)
