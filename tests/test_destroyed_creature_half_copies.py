"""Copy the real dying creature from its sealed battlefield snapshot."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destroyed_creature_copy import DESTROYED_CREATURE_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class DestroyedCreatureHalfCopiesTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "destroyed-creature-half-copies.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "actual-card-play-and-fixed-cleanup-limit-cards.json",
            fixtures / "destroyed-creature-half-copies-cards.json",
            fixtures / "enchanted-player-static-cards.json",
            *[fixtures / name for name in ('activation-paid-creature-subtype-cards.json', 'activation-paid-result-slot-cards.json', 'activation-public-state-cards.json', 'attack-declaration-power-cards.json')],
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Committed death snapshots", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def test_exact_original_rejects_an_undeclared_death_copy_owner(self):
        bind, registry = register_card(self.db, self.registry, "Saw in Half")
        self.assertTrue(bind["strict_capability_ready"], bind["blockers"])
        program, = registry.programs_for_oracle(bind["oracle_id"])
        self.assertIn(DESTROYED_CREATURE_COPY_CAPABILITY, program.capability_dependencies)
        reduced = tuple(cap for cap in program.capability_dependencies if cap != DESTROYED_CREATURE_COPY_CAPABILITY)
        without = replace(program, capability_dependencies=reduced,
                          capability_closure=self.registry.closure(reduced, profile="commander_review").to_dict())
        runtime = bind_semantic_program_runtime(without, capability_registry=self.registry, profile="commander_review")
        self.assertFalse(runtime["strict"], runtime)

    def _resolve_negative_copy(self, seed):
        game = self.session(seed)
        effect = self.add(game.engine, "Saw in Half", zone="hand")
        weaken = self.add(game.engine, "Disfigure", zone="hand")
        original = self.add(game.engine, "Char-Rumbler", seat="C")
        game.engine.change_control(original.object_id, "B", reason="Prepare borrowed creature")
        for card in (effect, weaken, original):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "B": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", weaken, [original.ref], reserve={"C": 2, "B": 1})
        self.resolve_top(game)
        self.assertEqual((-3, 1), tuple(game.engine._numeric_stat(original.object_id, s) for s in ("power", "toughness")))
        self.cast_for(game, "A", effect, [original.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        copies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies), "Both committed death copies must enter")
        for card in copies:
            self.assertEqual(("B", "B"), (card.owner, card.controller))
            self.assertEqual(("-1", "1"), tuple(card.annotations["copy_overrides"][k] for k in ("power", "toughness")))
            self.assertEqual((-1, 1), tuple(game.engine._numeric_stat(card.object_id, s) for s in ("power", "toughness")))
            self.assertIn("double strike", {i.casefold() for i in game.engine._effective_card_data(card)["keywords"]})
        return game

    def test_actual_negative_last_known_stats_use_the_borrowing_controller_and_replay(self):
        game = self._resolve_negative_copy(70790001)
        self.replay(game, load=True)

    def test_destruction_death_omission_mutant_loses_both_actual_copies(self):
        from quorune import destruction

        owner = destruction.destroy_permanent_refs
        def without_death(*args, **kwargs):
            result = owner(*args, **kwargs)
            return replace(result, died_object_ids=())
        with patch.object(destruction, "destroy_permanent_refs", side_effect=without_death):
            with self.assertRaisesRegex(AssertionError, "Both committed death copies must enter"):
                self._resolve_negative_copy(70790002)

    def test_actual_indestructible_and_redirected_destruction_never_copy(self):
        for redirect in (False, True):
            with self.subTest(redirect=redirect):
                game = self.session(70790005 + redirect)
                spell = self.add(game.engine, "Saw in Half", zone="hand")
                self.assert_closed(game, spell)
                if redirect:
                    replacement = self.add(game.engine, "Rest in Peace", seat="D")
                    self.assert_closed(game, replacement)
                target = self.add(game.engine, "Grizzly Bears" if redirect else "Darksteel Myr", seat="B")
                self.prepare(game, mana={"C": 2, "B": 1})
                self.checkpoint(game)
                self.cast_for(game, "A", spell, [target.ref])
                self.resolve_top(game)
                self.assertEqual("exile" if redirect else "battlefield", game.state.cards[target.object_id].zone)
                self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
                self.replay(game, load=True)

    def test_actual_copiable_aura_uses_the_death_controller_for_two_replayable_entries(self):
        from quorune.continuous_effect_model import (ContinuousEffect, ContinuousEffectDuration,
            ContinuousEffectOrigin, ContinuousObjectIdentity, ContinuousOperation, Layer)
        from quorune.continuous_effect_state import commit_continuous_effect
        from quorune.record import authoritative_state_hash

        game = self.session(70790007)
        source = self.add(game.engine, "Saw in Half", zone="hand")
        original = self.add(game.engine, "Pacifism", seat="B", ref="copiable-aura-destruction-model")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="first-attachment")
        second = self.add(game.engine, "Grizzly Bears", seat="B", ref="second-attachment")
        identity = ContinuousObjectIdentity(original.object_id, original.logical_object_id)
        for layer, sublayer, operations in (
            (Layer.TYPE, "4", (ContinuousOperation("set_types", ["Creature"], field="card_types"),
                               ContinuousOperation("set_types", [], field="subtypes"))),
            (Layer.POWER_TOUGHNESS, "7b", (ContinuousOperation("set_power_toughness", [3, 5]),)),
        ):
            commit_continuous_effect(game.state, ContinuousEffect(
                effect_id=f"fixture:noncopiable-animation:{sublayer}", source_id=original.object_id,
                layer=layer, sublayer=sublayer, timestamp=game.engine._next_zone_timestamp(),
                operations=operations, origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN, locked_objects=(identity,),
            ))
        self.assert_closed(game, source)
        self.assert_closed(game, original)
        self.prepare(game, mana={"C": 2, "B": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", source, [original.ref])
        self.until(game, lambda state: state.state.pending_decision is not None
                   and state.state.pending_decision.kind == "aura.entry")
        self.assertEqual("graveyard", game.state.cards[original.object_id].zone)
        game = self.replay(game, load=True)
        for attachment in (first, second):
            decision = game.state.pending_decision
            self.assertEqual(("aura.entry", ["B"]), (decision.kind, decision.actors))
            self.assertFalse(any(card.is_token for card in game.state.cards.values()))
            before = authoritative_state_hash(game.state)
            command = {"action_id": "choose", "aura_target": attachment.ref}
            self.assertFalse(game.act("pilot:A", command).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
            result = game.act("pilot:B", command)
            self.assertTrue(result.ok, result.summary)
        copies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(2, len(copies))
        self.assertEqual({"B"}, {card.controller for card in copies})
        self.assertEqual({first.object_id, second.object_id}, {card.attached_to for card in copies})
        self.assertEqual({"Pacifism"}, {game.engine.display_name(card.object_id) for card in copies})
        self.replay(game, load=True)
