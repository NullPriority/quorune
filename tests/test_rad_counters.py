"""Native rules, privacy and Game Record witnesses for CR 728."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.counter_state import CounterChange, commit_counter_changes, plan_counter_changes
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.model import GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.rad_counter_model import RAD_CAPABILITY, RAD_EFFECT_FIELD, RAD_PRODUCER_FIELD, RAD_VERSION_FIELD
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import _validate_effect_versions, format_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram
from scripts.build_test_database import build_fixture_database


class RadCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "rad.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/committed-milled-creature-tokens-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Radiation", [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    @staticmethod
    def counters(game, seat="A"):
        return game.state.players[seat].counters.get("rad", 0)

    @staticmethod
    def set_rad(game, amount, seat="A"):
        # Arrange a public fixture through the counter owner before the recorded journey.
        before = RadCounterTests.counters(game, seat)
        commit_counter_changes(game.engine, plan_counter_changes(
            game.engine, (CounterChange("player", seat, "rad", amount - before),),
        ))

    def staged(self, seed, names, *, amount, other=0):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        for oid in tuple(game.state.players["A"].zones["library"]):
            game.engine.move_card(oid, "hand", reason="Prepare the bounded fixture library", log=False)
        cards = [self.add(game.engine, name, zone="library", ref=f"rad-hidden-{idx}")
                 for idx, name in enumerate(names)]
        self.add(game.engine, "Plains", zone="library", ref="rad-before-trigger-draw")
        self.set_rad(game, amount)
        self.set_rad(game, other, "B")
        self.assertEqual(("beginning", "upkeep"), (game.state.phase, game.state.step))
        for card in cards:
            card.known_to = []
            card.revealed_to = []
        game.engine._grant_priority("A")
        game.engine.pump()
        return game, cards

    def enter_main(self, game):
        self.checkpoint(game)
        self.until(game, lambda session: (session.state.phase, session.state.step) == ("precombat_main", "main"))

    def trigger(self, game):
        item, = [item for item in game.state.stack if RAD_EFFECT_FIELD in item.context]
        self.assertEqual(("A", "triggered_ability", None, None, []),
                         (item.controller, item.kind, item.source_object_id, item.card_object_id, item.targets))
        return item

    def test_precombat_trigger_is_for_active_player_and_counts_committed_nonlands(self):
        game, cards = self.staged(728001, ("Grizzly Bears", "Plains", "Dread Summons"), amount=3, other=2)
        self.enter_main(game)
        item = self.trigger(game)
        self.assertIn(item.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.assertEqual((3, 2, 40), (self.counters(game), self.counters(game, "B"), game.state.players["A"].life))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard",) * 3, tuple(game.state.cards[c.object_id].zone for c in cards))
        self.assertEqual((1, 2, 38), (self.counters(game), self.counters(game, "B"), game.state.players["A"].life))
        life, = [event for event in game.state.events if event.code == "effect.life" and event.details.get("cause") == "radiation"]
        self.assertEqual(-2, life.details["delta"])
        self.until(game, lambda session: session.state.phase == "postcombat_main")
        self.assertFalse(any(RAD_EFFECT_FIELD in entry.context for entry in game.state.stack))
        self.replay(game, load=True)

    def test_public_exile_replacements_resume_the_same_private_mill(self):
        game, (top,) = self.staged(728002, ("Grizzly Bears",), amount=2)
        self.add(game.engine, "Leyline of the Void", seat="B")
        self.add(game.engine, "Rest in Peace", seat="D")
        self.enter_main(game)
        self.trigger(game)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual((2, 40, "library"),
                         (self.counters(game), game.state.players["A"].life, game.state.cards[top.object_id].zone))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(top.ref, json.dumps(game.packet(principal, full=True)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        response = {"action_id": "choose", "replacement": options[0]["id"]}
        prior = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", response).ok)
        self.assertFalse(game.act("pilot:A", {**response, "replacement": "not offered"}).ok)
        self.assertEqual(prior, authoritative_state_hash(game.state))
        resolution = game.act("pilot:A", response)
        self.assertTrue(resolution.ok, resolution.summary)
        self.assertEqual(("exile", 1, 39),
                         (game.state.cards[top.object_id].zone, self.counters(game), game.state.players["A"].life))
        self.assertIn(top.ref, json.dumps(game.packet("pilot:D", full=True)))
        self.replay(game, load=True)

    def test_short_empty_and_private_replacement_use_only_identifiable_results(self):
        for names, amount, expected_rad, expected_life in (
            ((), 4, 4, 40), (("Plains", "Grizzly Bears"), 4, 3, 39),
            (("Blightsteel Colossus",), 4, 4, 40),
        ):
            with self.subTest(names=names):
                game, cards = self.staged(728003 + len(names), names, amount=amount)
                self.enter_main(game)
                self.trigger(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual((expected_rad, expected_life), (self.counters(game), game.state.players["A"].life))
                self.assertTrue(game.state.players["A"].in_game)
                if names == ("Blightsteel Colossus",):
                    self.assertEqual("library", game.state.cards[cards[0].object_id].zone)
                self.replay(game, load=True)

    def test_intervening_condition_and_count_are_live_at_resolution(self):
        for new_count in (0, 1, 3):
            with self.subTest(new_count=new_count):
                game, cards = self.staged(728010 + new_count, ("Grizzly Bears",) * 3, amount=2)
                self.enter_main(game)
                self.trigger(game)
                self.set_rad(game, new_count)
                self.checkpoint(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(new_count, sum(game.state.cards[card.object_id].zone == "graveyard" for card in cards))
                self.assertEqual((0, 40 - new_count), (self.counters(game), game.state.players["A"].life))
                self.replay(game, load=True)

    def test_legacy_checkpoint_without_marker_preserves_passive_counters(self):
        game, (top,) = self.staged(728020, ("Grizzly Bears",), amount=2)
        game.state.rad_trigger_version = None
        self.enter_main(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(("library", 2, 40),
                         (game.state.cards[top.object_id].zone, self.counters(game), game.state.players["A"].life))
        after = self.replay(game, load=True)
        self.assertNotIn(RAD_VERSION_FIELD, after.state.to_dict())
        self.assertEqual(authoritative_state_hash(game.state), authoritative_state_hash(after.state))


    def test_rad_provenance_accepts_omission_and_rejects_explicit_invalid_versions(self):
        state = self.session(728032).state
        current = state.to_dict()
        self.assertEqual(1, current[RAD_VERSION_FIELD])
        self.assertEqual(1, format_state_versions(state)[RAD_VERSION_FIELD])
        historical = {key: value for key, value in current.items() if key != RAD_VERSION_FIELD}
        resumed = GameState.from_dict(historical)
        self.assertIsNone(resumed.rad_trigger_version)
        self.assertNotIn(RAD_VERSION_FIELD, resumed.to_dict())
        self.assertNotIn(RAD_VERSION_FIELD, format_state_versions(resumed))
        legacy_format = format_state_versions(resumed)
        _validate_effect_versions(legacy_format, resumed)
        for invalid in (None, False, True, 0, 2, "1"):
            with self.subTest(version=invalid):
                with self.assertRaisesRegex(ValueError, "Unsupported inherent rad trigger version"):
                    GameState.from_dict({**historical, RAD_VERSION_FIELD: invalid})
                with self.assertRaisesRegex(ValueError, "Unsupported inherent rad trigger version"):
                    _validate_effect_versions({**legacy_format, RAD_VERSION_FIELD: invalid}, resumed)
        with self.assertRaisesRegex(ValueError, "provenance does not match"):
            _validate_effect_versions(legacy_format, state)
        with self.assertRaisesRegex(ValueError, "provenance does not match"):
            _validate_effect_versions(format_state_versions(state), resumed)

    def test_exact_rad_producer_and_forged_radiation_fail_closed(self):
        profile = "commander_review"
        record = replace(self.db.lookup("Dread Summons"), oracle_text="Each player gets four rad counters.", mana_cost="{2}{B}", keywords=())
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=profile)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        (node,) = ir.faces[0].nodes
        self.assertIn(RAD_CAPABILITY, node.capability_dependencies)
        dependencies = tuple(key for key in node.capability_dependencies if key != RAD_CAPABILITY)
        program = SemanticProgram(
            key="test-rad-producer", label="Dependency witness", effects=node.effects,
            capability_dependencies=dependencies, capability_closure=self.registry.closure(dependencies, profile=profile).to_dict(),
        )
        binding = bind_semantic_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertIn(f"capability:undeclared_runtime_dependency:{RAD_CAPABILITY}", binding["blockers"])
        historic = {key: value for key, value in node.effects[0].items() if key != RAD_PRODUCER_FIELD}
        prior = bind_semantic_program_runtime(
            replace(program, effects=(historic,)), capability_registry=self.registry, profile=profile,
        )
        self.assertNotIn(f"capability:undeclared_runtime_dependency:{RAD_CAPABILITY}", prior["blockers"])
        game, _ = self.staged(728030, (), amount=2)
        self.enter_main(game)
        self.trigger(game)
        before = authoritative_state_hash(game.state)
        with self.assertRaisesRegex(GameRuleError, "missing its resolving stack"):
            game.engine.apply_effect(
                {"op": "life", "player": "A", "delta": -1, "cause": "radiation", RAD_EFFECT_FIELD: 1},
                actor="A", as_cost=False,
            )
        self.assertEqual(before, authoritative_state_hash(game.state))
        with patch("quorune.rad_counters._milled_nonland", return_value=False):
            mutated, cards = self.staged(728031, ("Grizzly Bears",), amount=1)
            self.enter_main(mutated)
            self.assertIsNone(self.resolve(mutated))
            self.assertEqual("graveyard", mutated.state.cards[cards[0].object_id].zone)
            self.assertEqual((1, 40), (self.counters(mutated), mutated.state.players["A"].life))
        confirmed, _ = self.staged(728031, ("Grizzly Bears",), amount=1)
        self.enter_main(confirmed)
        self.assertIsNone(self.resolve(confirmed))
        self.assertEqual((0, 39), (self.counters(confirmed), confirmed.state.players["A"].life))
