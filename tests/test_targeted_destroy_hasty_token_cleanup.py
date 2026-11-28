"""Whole-spell evidence for targeted destruction and an ordered temporary token batch."""

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
from quorune.fixed_token_production import CREATED_TOKEN_HASTE_FIELD
from quorune.haste import has_effective_haste
from quorune.oracle_ir import compile_oracle_card
from quorune.replacement.immutable import FrozenMap, thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.created_token_cleanup_shapes import (
    FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY, created_token_cleanup_closure,
)
from quorune.token_creation_events import publish_token_creations
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SKIRMISH = "Salt Road Skirmish"
PROFILE = "commander_review"


class TargetedDestroyHastyTokenCleanupTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "targeted-destroy-hasty-cleanup.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/targeted-destroy-hasty-token-cleanup-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Targeted destruction and tokens", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def start(self, game, *cards, active="A"):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="A", active=active, mana={"B": 3, "R": 3, "W": 3, "C": 8})
        self.checkpoint(game)

    def tokens(self, game, name="Warrior Token"):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.printed_name == name]

    def finish_end_step(self, game, player):
        self.until(game, lambda session: session.state.active_player == player and session.state.step == "end_step")
        self.assertIsNone(self.resolve(game))

    def test_actual_legal_indestructible_target_still_creates_hasty_replacement_batch_and_cleans_up(self):
        game = self.session(61270501)
        spell = self.add(game.engine, SKIRMISH, zone="hand")
        target = self.add(game.engine, "Darksteel Myr", seat="B")
        doubler = self.add(game.engine, "Anointed Procession")
        self.start(game, spell, target, doubler)
        self.cast(game, "A", spell, [target.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        warriors = self.tokens(game)
        self.assertEqual(4, len(warriors))
        self.assertTrue(all(has_effective_haste(game.engine, card) for card in warriors))
        self.assertEqual(1, sum(trigger.active for trigger in game.state.delayed_triggers))
        game = self.replay(game, load=True)
        self.attack(game, [(warriors[0], "B")])
        self.finish_end_step(game, "A")
        self.assertFalse(self.tokens(game))
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.assertEqual("battlefield", game.state.cards[target.object_id].zone)
        self.replay(game, load=True)

    def test_actual_sole_target_leaving_cancels_creation_haste_and_delayed_sacrifice(self):
        game = self.session(61270502)
        spell = self.add(game.engine, SKIRMISH, zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self.start(game, spell, response)
        self.cast(game, "A", spell, [target.ref])
        self.cast(game, "B", response, [target.ref])
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[target.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertFalse(self.tokens(game))
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_actual_entry_follows_destruction_and_cleanup_uses_the_next_opponent_end_step(self):
        game = self.session(61270503)
        spell = self.add(game.engine, SKIRMISH, zone="hand")
        spell_grant = self.add(game.engine, "Vedalken Orrery")
        alarm = self.add(game.engine, "Raise the Alarm", zone="hand")
        target = self.add(game.engine, "Grizzly Bears", seat="B")
        self.start(game, spell, spell_grant, alarm, active="B")
        self.cast(game, "A", alarm)
        self.assertIsNone(self.resolve(game))
        soldiers = self.tokens(game, "Soldier Token")
        self.assertEqual(2, len(soldiers))
        self.cast(game, "A", spell, [target.ref])
        at_entry = {}

        def record_entry(host, creations, **kwargs):
            for group in creations:
                for oid in group.objects:
                    at_entry[oid] = (host.state.cards[target.object_id].zone,
                                     has_effective_haste(host, host.state.cards[oid]))
            return publish_token_creations(host, creations, **kwargs)

        with patch("quorune.token_creation.publish_token_creations", side_effect=record_entry):
            self.assertIsNone(self.resolve(game))
        warriors = self.tokens(game)
        self.assertEqual(2, len(warriors))
        self.assertEqual({card.object_id: ("graveyard", False) for card in warriors}, at_entry)
        self.assertTrue(all(has_effective_haste(game.engine, card) for card in warriors))
        self.assertTrue(all(not has_effective_haste(game.engine, card) for card in soldiers))
        self.finish_end_step(game, "B")
        self.assertFalse(self.tokens(game))
        self.assertEqual({card.object_id for card in soldiers}, {card.object_id for card in self.tokens(game, "Soldier Token")})
        self.replay(game, load=True)

    def test_whole_spell_requires_all_clauses_and_both_ordered_token_results(self):
        record = self.db.lookup(SKIRMISH, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, "Salt Road Skirmish")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        destroy, tokens = program.effects
        self.assertEqual(("destroy", "$target.0", "creature"), (
            destroy["op"], destroy["card"], program.target_schema["types_any"][0],
        ))
        self.assertEqual(1, tokens[CREATED_TOKEN_HASTE_FIELD])
        self.assertEqual("sacrifice", tokens["token_cleanup"]["action"])
        self.assertEqual("next_end_step", tokens["token_cleanup"]["timing"])
        self.assertNotIn("Haste", tokens["characteristics"].get("keywords", ()))
        required, _ = created_token_cleanup_closure(tokens)
        self.assertIn(FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY, required)
        changed = thaw_value(FrozenMap(tokens))
        changed["token_cleanup"]["timing"] = "controller_next_end_step"
        with self.assertRaises(ValueError):
            created_token_cleanup_closure(changed)
        deps = tuple(cap for cap in program.capability_dependencies if cap != FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY)
        unbound = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        runtime = bind_semantic_program_runtime(unbound, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(runtime["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{FIXED_HASTY_TOKEN_CLEANUP_CAPABILITY}", runtime["blockers"])
        for before, after in (
            ("target creature", "target card in your hand"),
            ("the next end step.", "your next end step."),
            ("the next end step.", "the next end step. Change the name of each card in your hand."),
        ):
            with self.subTest(after=after):
                changed = replace(record, oracle_text=record.oracle_text.replace(before, after))
                self.assertNotEqual("exact", compile_oracle_card(
                    changed, capability_registry=self.registry, capability_profile=PROFILE,
                ).status)
        with patch("quorune.compiler.created_token_cleanup_templates._effect_with_cleanup", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                record, capability_registry=self.registry, capability_profile=PROFILE,
            ).status)
