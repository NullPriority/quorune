"""The mandatory Knight resolves before the monarch guard and live damage amount."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.monarch_knight_entry_damage import MONARCH_KNIGHT_ENTRY_DAMAGE_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card
import test_monarch_movement_replacements as movement_fixture


PROFILE = "commander_review"


class MonarchKnightEntryDamageTests(InheritedNativeFixture, unittest.TestCase):
    throne = movement_fixture.MonarchMovementReplacementTests.throne
    upkeep = movement_fixture.MonarchMovementReplacementTests.upkeep
    transfer = movement_fixture.MonarchMovementReplacementTests.transfer

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "monarch-knight-entry-damage.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/monarch-knight-entry-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Court of Embereth", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, seed, *, changed=False, doubled=False, departed=False):
        game = self.session(seed)
        court = self.add(game.engine, "Court of Embereth")
        original_source = court.logical_object_id
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Grizzly Bears", seat="B", ref="foreign-creature")
        throne = self.throne(game) if changed else None
        if doubled:
            self.assert_closed(game, self.add(game.engine, "Anointed Procession"))
        if departed:
            destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
            self.assert_closed(game, destroy)
            for index in range(2):
                self.add(game.engine, "Plains", seat="B", ref=f"disenchant-mana-{index}")
        self.upkeep(game, court, throne)
        self.assertEqual(1, self.trigger_count(game, court))
        if changed:
            self.transfer(game, throne)
        if departed:
            self.cast(game, "B", destroy, [court.ref])
            self.resolve_top(game)
            self.assertEqual("graveyard", game.state.cards[court.object_id].zone)
        return game, original_source

    def knights(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == "A"
                and card.object_kind == "token" and card.printed_name == "Knight Token"]

    def test_actual_token_replacement_finishes_before_the_live_count_and_damage(self):
        game, _ = self.start(76331101, doubled=True)
        before = {seat: game.state.players[seat].life for seat in "ABCD"}
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        knights = self.knights(game)
        self.assertEqual(2, len(knights))
        for knight in knights:
            self.assertEqual((3, 1), tuple(game.engine._numeric_stat(knight.object_id, field)
                                         for field in ("power", "toughness")))
        self.assertEqual({"A": before["A"], **{seat: before[seat]-3 for seat in "BCD"}},
                         {seat: game.state.players[seat].life for seat in "ABCD"})
        self.replay(game, load=True)

    def test_title_transfer_keeps_the_token_and_departure_keeps_the_original_damage_source(self):
        for mode in ("changed", "departed"):
            with self.subTest(mode=mode):
                game, original_source = self.start(76331110 + len(mode), changed=mode=="changed", departed=mode=="departed")
                before = {seat: game.state.players[seat].life for seat in "ABCD"}
                game = self.replay(game, load=True)
                damage = []
                dispatch = game.engine._dispatch_semantic_event

                def observe(event, facts, **kwargs):
                    if event == "damage.dealt":
                        damage.append(dict(facts))
                    return dispatch(event, facts, **kwargs)

                with patch.object(game.engine, "_dispatch_semantic_event", side_effect=observe):
                    self.assertIsNone(self.resolve(game))
                if mode == "departed":
                    self.assertEqual(set("BCD"), {event["target"] for event in damage})
                    self.assertEqual({original_source}, {event["source_logical_object_id"] for event in damage})
                else:
                    self.assertEqual([], damage)
                self.assertEqual(1, len(self.knights(game)))
                loss = 0 if mode == "changed" else 2
                self.assertEqual({"A": before["A"], **{seat: before[seat]-loss for seat in "BCD"}},
                                 {seat: game.state.players[seat].life for seat in "ABCD"})
                self.replay(game, load=True)

    def test_complete_original_preserves_mandatory_prefix_and_independent_ordered_authority(self):
        cap = MONARCH_KNIGHT_ENTRY_DAMAGE_CAPABILITY
        report, semantics = register_card(self.db, self.registry, "Court of Embereth")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        programs = semantics.programs_for_oracle(report["oracle_id"])
        self.assertEqual(2, len(programs))
        program, = (row for row in programs if cap in row.capability_dependencies)
        self.assertEqual(["create_token", "apply_if_public_condition"], [effect["op"] for effect in program.effects])
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        absent = replace(program, capability_dependencies=deps,
                         capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        card = self.db.lookup("Court of Embereth", fuzzy=False)
        for old, new in (("number of creatures", "number of lands"),
                         ("3/1 red Knight", "2/1 red Knight"),
                         ("creatures you control.", "creatures you control. Rewrite history.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
            result = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
