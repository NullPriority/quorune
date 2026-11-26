"""Actual cast-triggered targeting restriction for the live creature set."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.turn_creature_target_prohibition_model import (
    TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY, TURN_CREATURE_TARGET_PROHIBITION_WINDOW,
    turn_creature_target_prohibition_instruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"


class ControllerCreatureTargetProhibitionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controller-creature-target-prohibition.sqlite3"
        fixture = ROOT / "tests/fixtures/controller-creature-target-prohibition-cards.json"
        build_fixture_database([fixture], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Current creature targeting", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def ready(self, game, *cards):
        game.state.config.semantic_policy = "trusted_only"
        for card in cards:
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"C": 1, "U": 3, "W": 1})
        self.prepare(game, mana={"G": 1, "C": 1, "U": 3})
        self.checkpoint(game)

    def target_offer(self, game, seat, *, action=None, source=None):
        self.priority(game, seat)
        options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        if source is not None:
            offer, = (row for row in options if row["id"].startswith(f"activate:{source.ref}:"))
        else:
            offer, = (row for row in options if row["id"] == action)
        return offer

    def reject_target(self, game, seat, offer, *, refused, available):
        legal = offer["target_schema"]["groups"][0]["legal_refs"]
        self.assertIn(available, legal)
        self.assertNotIn(refused, legal)
        self.assertIsNone(game.packet("pilot:D", full=True)["decision"])
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, {"action_id": offer["id"], "targets": [refused], "pay": "auto"})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_cast_rule_revalidates_older_targets_after_the_amulet_leaves(self):
        game = self.session(76343001)
        source = self.add(game.engine, "Veilstone Amulet")
        bear = self.add(game.engine, "Grizzly Bears")
        old = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        draw = self.add(game.engine, "Reach Through Mists", zone="hand")
        destroy = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.ready(game, source, bear, old, draw, destroy)
        self.cast(game, "B", old, [bear.ref])
        self.assertEqual(0, self.trigger_count(game, source))
        self.cast(game, "A", draw)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", destroy, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotIn(TURN_CREATURE_TARGET_PROHIBITION_WINDOW, game.state.players["A"].stats)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(game.state.turn_sequence, game.state.players["A"].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[old.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertNotIn("hexproof", game.engine._combat_keywords(bear))
        self.replay(game, load=True)

    def test_actual_live_creature_menu_denies_opponents_allows_self_and_expires(self):
        game = self.session(76343002)
        for object_id in tuple(game.state.players["A"].zones["hand"]):
            game.engine.move_card(object_id, "library", reason="Prepare an ordinary starting hand for cleanup")
        source = self.add(game.engine, "Veilstone Amulet")
        resident = self.add(game.engine, "Grizzly Bears", ref="target-rule-resident")
        arriving = self.add(game.engine, "Grizzly Bears", zone="hand", ref="target-rule-arriving")
        own = self.add(game.engine, "Unsummon", zone="hand", ref="target-rule-own")
        opposed = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="target-rule-opposed")
        eligible = self.add(game.engine, "Grizzly Bears", seat="B", ref="target-rule-unprotected")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="C")
        pinger.acquired_control_turn_count = -1
        self.add(game.engine, "Island", seat="B")
        self.ready(game, source, arriving, opposed, own, pinger)
        self.cast(game, "A", arriving)
        self.assertEqual("stack", game.state.cards[arriving.object_id].zone)
        self.resolve_top(game)
        self.assertEqual(game.state.turn_sequence, game.state.players["A"].stats[TURN_CREATURE_TARGET_PROHIBITION_WINDOW])
        self.resolve_top(game)
        arriving = game.state.cards[arriving.object_id]
        self.assertEqual("battlefield", arriving.zone)
        spell_offer = self.target_offer(game, "B", action=f"cast:{opposed.ref}")
        self.reject_target(game, "B", spell_offer, refused=arriving.ref, available=eligible.ref)
        ability_offer = self.target_offer(game, "C", source=pinger)
        self.reject_target(game, "C", ability_offer, refused=resident.ref, available=eligible.ref)
        result = game.act("pilot:C", {"action_id": ability_offer["id"], "targets": [eligible.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[eligible.object_id].marked_damage)
        own_offer = self.target_offer(game, "A", action=f"cast:{own.ref}")
        self.assertIn(resident.ref, own_offer["target_schema"]["groups"][0]["legal_refs"])
        self.cast(game, "A", own, [resident.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[resident.object_id].zone)
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertNotIn(TURN_CREATURE_TARGET_PROHIBITION_WINDOW, game.state.players["A"].stats)
        offer = self.target_offer(game, "B", action=f"cast:{opposed.ref}")
        self.assertIn(arriving.ref, offer["target_schema"]["groups"][0]["legal_refs"])
        self.cast(game, "B", opposed, [arriving.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[arriving.object_id].zone)
        self.replay(game, load=True)

    def test_complete_original_requires_the_independent_live_targeting_rule(self):
        cap = TURN_CREATURE_TARGET_PROHIBITION_CAPABILITY
        report, programs = register_card(self.db, self.registry, "Veilstone Amulet")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(report["oracle_id"])
                    if cap in row.capability_dependencies)
        self.assertEqual(turn_creature_target_prohibition_instruction(), dict(program.effects[0]))
        deps = tuple(key for key in program.capability_dependencies if key != cap)
        unavailable = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(unavailable, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        original = self.db.lookup("Veilstone Amulet", fuzzy=False)
        for before, after in (("creatures", "permanents"), ("spells or abilities", "abilities"),
                              ("this turn.", "until your next turn."), ("you cast", "an opponent casts"),
                              ("this turn.", "this turn. Rewrite history.")):
            with self.subTest(unsupported=after):
                synthetic = replace(original, oracle_text=original.oracle_text.replace(before, after))
                result = compile_oracle_card(synthetic, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", result.status)
