"""Draw for and grant a keyword to one sampled set of existing creatures."""

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
from quorune.continuous_effect_model import ContinuousEffect, ContinuousObjectIdentity
from quorune.continuous_effect_state import matching_battlefield_objects
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.captured_draw_keyword_grant import (
    CAPTURED_DRAW_KEYWORD_CAPABILITY, CAPTURED_DRAW_KEYWORD_GRANT_FIELD,
    captured_countered_creature_query, captured_draw_keyword_instruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_fixed_one_counter_movement as movement_fixture


PROFILE = "commander_review"


class CapturedDrawKeywordGrantTests(InheritedNativeFixture, unittest.TestCase):
    reject = movement_fixture.FixedOneCounterMovementTests.reject
    ready = movement_fixture.FixedOneCounterMovementTests.ready
    lands = movement_fixture.FixedOneCounterMovementTests.lands
    activate = movement_fixture.FixedOneCounterMovementTests.activate

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "captured-draw-keyword-grant.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "fixed-one-counter-movement-cards.json",
            fixtures / "external-entry-counter-cards.json",
            fixtures / "captured-draw-keyword-grant-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Captured draw and grant", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_original_draw_and_keyword_grant_require_the_complete_captured_set_owner(self):
        card = self.db.lookup("Inspiring Call", fuzzy=False)

        def ir(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        self.assertEqual("exact", ir(card).status)
        report, semantics = register_card(self.db, self.registry, "Inspiring Call")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertIsNone(program.target_schema)
        self.assertEqual([captured_draw_keyword_instruction()], program.effects)
        self.assertIn(CAPTURED_DRAW_KEYWORD_CAPABILITY, program.capability_dependencies)
        deps = tuple(cap for cap in program.capability_dependencies if cap != CAPTURED_DRAW_KEYWORD_CAPABILITY)
        changed = (
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
            replace(program, effects=[{**program.effects[0], "query": captured_countered_creature_query("$opponent").canonical_dict()}]),
            replace(program, effects=[{**program.effects[0], CAPTURED_DRAW_KEYWORD_GRANT_FIELD: {"identities": []}}]),
        )
        for corrupt in changed:
            self.assertFalse(bind_semantic_program_runtime(
                corrupt, capability_registry=self.registry, profile=PROFILE,
            )["strict"])
        for text in (
            card.oracle_text.replace("you control", "an opponent controls"),
            card.oracle_text.replace("+1/+1", "charge"),
            card.oracle_text.replace("Those creatures", "Other creatures"),
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", ir(replace(card, oracle_text=text)).status)

    def scene(self, seed, *, count, replacement=False):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"]):
                game.engine.move_card(object_id, "library", log=False)
        source = self.add(game.engine, "Inspiring Call", zone="hand")
        eligible = [
            self.add(game.engine, "Grizzly Bears", ref=f"inspiring-original-{index}")
            for index in range(count)
        ]
        for card in eligible:
            card.counters["+1/+1"] = 1
        other = self.add(game.engine, "Grizzly Bears", ref="inspiring-other-counter")
        other.counters["charge"] = 1
        foreign = self.add(game.engine, "Grizzly Bears", seat="B", ref="inspiring-opposing-counter")
        foreign.counters["+1/+1"] = 1
        top = self.add(game.engine, "Forest", zone="library", ref="inspiring-private-draw")
        known = [source]
        if replacement:
            words = self.add(game.engine, "Words of Wilding")
            renata = self.add(game.engine, "Renata, Called to the Hunt")
            known.extend((words, renata))
            self.lands(game, "Forest", 5)
            self.ready(game, *known)
            for _ in range(2):
                self.activate(game, words, "ab1", [])
                self.resolve_top(game)
        else:
            self.lands(game, "Forest", 3)
            self.ready(game, *known, actor="B")
        return game, source, eligible, other, foreign, top

    def indestructible(self, game, card):
        current = game.state.cards[card.object_id]
        return any(keyword.casefold() == "indestructible"
                   for keyword in game.engine._effective_card_data(current).get("keywords", ()))

    def test_actual_draw_and_grant_only_use_controlled_qualifying_creatures_until_the_real_turn_changes(self):
        game, source, eligible, other, foreign, top = self.scene(78530101, count=2)
        self.assertEqual("B", game.state.active_player)
        turn = game.state.turn_sequence
        size = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", source)
        self.resolve_top(game)
        hand = game.state.players["A"].zones["hand"]
        self.assertEqual(size + 1, len(hand))
        self.assertIn(top.object_id, hand)
        self.assertNotIn(top.ref, json.dumps(game.packet("pilot:B", full=True)))
        self.assertTrue(all(self.indestructible(game, card) for card in eligible))
        self.assertFalse(any(self.indestructible(game, card) for card in (other, foreign)))
        for _ in range(72):
            if game.state.turn_sequence > turn:
                break
            decision = game.state.pending_decision
            if decision.kind == "priority":
                command = {"action_id": "pass"}
            elif decision.kind == "combat.attackers":
                command = {"action_id": "attack", "attackers": {}}
            elif decision.kind == "combat.blockers":
                command = {"action_id": "block", "blocks": {}}
            else:
                self.fail(f"Unexpected turn transition decision {decision.kind}")
            principal, = game.pending_principals()
            result = game.act(principal, command)
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("The ordinary turn change did not occur")
        self.assertFalse(any(self.indestructible(game, card) for card in eligible))
        self.replay(game, load=True)

    def test_actual_draw_replacements_create_new_eligible_bears_without_enlarging_the_captured_set(self):
        game, source, eligible, other, foreign, top = self.scene(78530102, count=3, replacement=True)
        size = len(game.state.players["A"].zones["hand"])
        self.cast_for(game, "A", source)
        pending = self.resolve(game)
        self.assertEqual(("draw.replacement", ["A"]), (pending.kind, pending.actors))
        self.assertFalse(any(self.indestructible(game, card) for card in eligible))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "choice": options[0]["id"]}
        self.reject(game, "B", command)
        game = self.replay(game, load=True)
        applied = game.act("pilot:A", command)
        self.assertTrue(applied.ok, applied.summary)
        self.assertIsNone(self.resolve(game))
        bears = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(bears))
        self.assertTrue(all(card.counters.get("+1/+1") == 1 for card in bears))
        self.assertTrue(all(self.indestructible(game, card) for card in eligible))
        self.assertFalse(any(self.indestructible(game, card) for card in bears),
                         "A Bear created during the draw must stay outside the original grant")
        self.assertFalse(any(self.indestructible(game, card) for card in (other, foreign)))
        hand = game.state.players["A"].zones["hand"]
        self.assertEqual(size, len(hand))
        self.assertIn(top.object_id, hand)
        captured, = (effect for effect in game.state.continuous_effects
                     if isinstance(effect, ContinuousEffect) and effect.locked_objects)
        self.assertEqual({card.object_id for card in eligible}, {identity.object_id for identity in captured.locked_objects})
        self.replay(game, load=True)

    def test_inspiring_call_witness_detects_reselecting_the_creatures_after_draw(self):
        from quorune.effect_runtime import captured_draw_keyword_grant

        original = captured_draw_keyword_grant.apply_captured_draw_keyword_grant

        def resample(host, effect, *, actor, reason):
            targets = matching_battlefield_objects(host, captured_countered_creature_query(actor))
            identities = sorted(ContinuousObjectIdentity(card.object_id, card.logical_object_id) for card in targets)
            wrong = {**effect, CAPTURED_DRAW_KEYWORD_GRANT_FIELD: {
                **effect[CAPTURED_DRAW_KEYWORD_GRANT_FIELD],
                "identities": [identity.to_dict() for identity in identities],
            }}
            return original(host, wrong, actor=actor, reason=reason)

        with patch.object(captured_draw_keyword_grant, "apply_captured_draw_keyword_grant", side_effect=resample):
            with self.assertRaisesRegex(AssertionError, "Bear created"):
                self.test_actual_draw_replacements_create_new_eligible_bears_without_enlarging_the_captured_set()
