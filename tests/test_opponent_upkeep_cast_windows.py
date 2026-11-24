"""Exercise printed opponent-upkeep cast windows through ordinary actions."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig, GameState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.self_cast_restrictions import SelfCastRestrictionHandler
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = (("Festival", "during_upkeep"), ("Reset", "after_upkeep"))


class OpponentUpkeepCastWindowsTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "opponent-upkeep-cast-windows.sqlite3"
        fixture = ROOT / "tests/fixtures/opponent-upkeep-cast-windows-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent upkeep windows", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def components(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        rule, = [item for item in programs if item.event == "cast.restriction"]
        body, = [item for item in programs if item.event == "resolve"]
        return card, rule, body

    def test_both_complete_originals_keep_their_timing_and_ordinary_result(self):
        handler = SelfCastRestrictionHandler()
        for name, window in ORIGINALS:
            with self.subTest(name=name):
                card, rule, body = self.components(name)
                descriptor, = rule.handlers
                self.assertEqual(4, descriptor["intrinsic_condition_version"])
                self.assertEqual({"kind": "opponent_upkeep_window", "window": window, "schema_version": 1},
                                 descriptor["source_condition"])
                self.assertEqual(window, handler.validate(descriptor).window.value)
                self.assertIsNone(body.target_schema)
                self.assertTrue(body.effects)
                first, rest = card.oracle_text.split("\n", 1)
                for changed in (
                    first.replace("opponent's", "teammate's") + "\n" + rest,
                    first.replace("upkeep", "modified upkeep") + "\n" + rest,
                ):
                    result = compile_oracle_card(replace(card, oracle_text=changed), capability_registry=self.registry)
                    self.assertNotEqual("exact", result.status, changed)
                for raw in (
                    {**descriptor["source_condition"], "window": "any"},
                    {**descriptor["source_condition"], "schema_version": True},
                    {**descriptor["source_condition"], "caster_turn": True},
                ):
                    with self.assertRaises(SemanticNodeError):
                        handler.validate({**descriptor, "source_condition": raw})

    def test_upkeep_completion_checkpoint_and_manifest_are_versioned_and_closed(self):
        state = GameState("upkeep-fact", GameConfig(seed=1), {}, {}, {}, {}, [], None, None)
        payload = state.to_dict()
        self.assertIs(payload["upkeep_completed"], False)
        valid_format = format_state_versions(state)
        self.assertEqual(1, valid_format["upkeep_completion_version"])
        validate_state_versions({"format": valid_format}, state)
        for malformed in (None, 1, "true"):
            with self.subTest(fact=malformed), self.assertRaises(ValueError):
                GameState.from_dict({**payload, "upkeep_completed": malformed})
        for malformed in (None, True, 0, 2, "1"):
            with self.subTest(version=malformed), self.assertRaisesRegex(ValueError, "Upkeep completion provenance"):
                validate_state_versions({"format": {**valid_format, "upkeep_completion_version": malformed}}, state)
        legacy_payload = {key: value for key, value in payload.items() if key != "upkeep_completed"}
        legacy = GameState.from_dict(legacy_payload)
        legacy.complete_upkeep()
        legacy.reset_upkeep_completion()
        self.assertEqual(legacy_payload, legacy.to_dict())
        self.assertNotIn("upkeep_completion_version", format_state_versions(legacy))
        with self.assertRaisesRegex(ValueError, "Upkeep completion provenance"):
            validate_state_versions({"format": valid_format}, legacy)

    def scene(self, seed, *, skip=False, legacy=False):
        game = self.session(seed)
        engine = game.engine
        festival, reset = (self.add(engine, name, zone="hand") for name, _ in ORIGINALS)
        lands = [self.add(engine, "Island", ref=f"reset-island-{index}") for index in range(2)]
        plain = self.add(engine, "Plains", ref="festival-white-mana")
        artifact = self.add(engine, "Sol Ring")
        foreign = self.add(engine, "Island", seat="C", ref="reset-foreign-island")
        attacker = self.add(engine, "Grizzly Bears", seat="B")
        hub = self.add(engine, "Eon Hub", seat="B") if skip else None
        for card in (festival, reset, hub):
            if card is not None:
                self.assert_closed(game, card)
        artifact.tapped = foreign.tapped = True
        if skip or legacy:
            plain.tapped = True
        for seat in "AB":
            hand = tuple(game.state.players[seat].zones["hand"])
            opening_plains = [object_id for object_id in hand if game.state.cards[object_id].printed_name == "Plains"]
            for object_id in opening_plains[:-3]:
                engine.move_card(object_id, "library", position="bottom", log=False, semantic_events=False)
        self.prepare(game)
        game.state.upkeep_completed = None if legacy else True
        self.checkpoint(game)
        return game, festival, reset, lands, plain, foreign, artifact, attacker

    def enter(self, game, seat, step):
        self.until(game, lambda current: current.state.active_player == seat and current.state.step == step)

    def offer_ids(self, game):
        self.priority(game, "A")
        return {row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}

    def rejected(self, game, card):
        action_id = "cast:" + card.ref
        self.assertNotIn(action_id, self.offer_ids(game))
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_festival_uses_only_the_opponents_upkeep_and_prevents_ordinary_attacks(self):
        game, festival, reset, _, _, _, _, attacker = self.scene(61343501)
        self.rejected(game, festival)
        self.rejected(game, reset)
        self.enter(game, "B", "upkeep")
        self.assertIs(game.state.upkeep_completed, False)
        self.rejected(game, reset)
        self.assertIn("cast:" + festival.ref, self.offer_ids(game))
        game = self.replay(game, load=True)
        self.cast_for(game, "A", festival)
        self.resolve_top(game)
        self.enter(game, "B", "declare_attackers")
        self.assertEqual("priority", game.state.pending_decision.kind)
        self.assertTrue(game.state.combat.attackers_declared)
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:B", {"action_id": "attack", "attackers": {attacker.ref: "A"}})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.combat.attackers)
        self.replay(game, load=True)

    def test_actual_reset_starts_after_the_upkeep_and_untaps_the_lands_spent_to_cast_it(self):
        game, festival, reset, lands, plain, foreign, artifact, _ = self.scene(61343502)
        self.enter(game, "B", "upkeep")
        self.rejected(game, reset)
        self.enter(game, "B", "draw")
        self.assertIs(game.state.upkeep_completed, True)
        self.rejected(game, festival)
        self.assertIn("cast:" + reset.ref, self.offer_ids(game))
        self.cast_for(game, "A", reset)
        self.assertTrue(all(game.state.cards[land.object_id].tapped for land in lands))
        self.resolve_top(game)
        self.assertTrue(all(not game.state.cards[land.object_id].tapped for land in (*lands, plain)))
        self.assertTrue(game.state.cards[foreign.object_id].tapped)
        self.assertTrue(game.state.cards[artifact.object_id].tapped)
        self.replay(game, load=True)

    def test_actual_skipped_upkeep_provides_neither_window_and_legacy_state_keeps_the_field_absent(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                game, festival, reset, _, _, _, _, _ = self.scene(61343503 + int(legacy), skip=True, legacy=legacy)
                self.enter(game, "B", "draw")
                self.assertEqual(None if legacy else False, game.state.upkeep_completed)
                game = self.replay(game, load=True)
                self.assertEqual(not legacy, "upkeep_completed" in game.state.to_dict())
                self.rejected(game, festival)
                self.rejected(game, reset)
