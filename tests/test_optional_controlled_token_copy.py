"""Optional controlled-token copy uses actual choices after normal token entry."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_next_player_untap_restrictions as native_turns
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class OptionalControlledTokenCopyTests(InheritedNativeFixture, unittest.TestCase):
    playing = native_turns.NextPlayerUntapRestrictionTests.playing

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "optional-controlled-token-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/optional-controlled-token-copy-cards.json",
            ROOT / "tests/fixtures/temporary-controlled-artifact-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional controlled token copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def tokens(self, game, seat):
        return tuple(card for card in game.state.cards.values()
                     if card.zone == "battlefield" and card.is_token and card.controller == seat)

    def subtypes(self, game, card):
        data = game.engine._effective_card_data(card)
        return game.engine._type_parts(data["type_line"])[1]

    def scene(self, seed, *, phased=False, reviewed=False):
        game = self.playing(seed)
        chief = self.add(game.engine, "Brudiclad, Telchor Engineer", zone="hand")
        rich = self.add(game.engine, "Strike It Rich", zone="hand")
        self.assert_closed(game, chief)
        self.assert_closed(game, rich)
        legacy = None
        if reviewed:
            all_views = game.engine.semantics.programs_for_oracle(chief.oracle_id)
            current, = (program for program in all_views if program.event == "step.begin")
            legacy, = (program for program in SemanticRegistry().programs_for_oracle(chief.oracle_id)
                       if program.event == "step.begin")
            game.engine.semantics.remove(current.key)
            game.engine.semantics.put(legacy)

        if phased:
            soldiers = self.add(game.engine, "Raise the Alarm", zone="hand")
            rival = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand", ref="other-token-maker")
            ripple = self.add(game.engine, "Reality Ripple", zone="hand")
            tap = self.add(game.engine, "Twiddle", zone="hand")
            for card in (soldiers, rival, ripple, tap):
                self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 8, "R": 2, "U": 5, "W": 1})
        game.state.players["B"].mana_pool["W"] = 2
        self.checkpoint(game)
        self.cast_for(game, "A", rich)
        self.assertIsNone(self.resolve(game))
        treasure, = self.tokens(game, "A")
        phased_token = foreign = None
        if phased:
            self.cast_for(game, "A", soldiers)
            self.assertIsNone(self.resolve(game))
            self.cast_for(game, "B", rival)
            self.assertIsNone(self.resolve(game))
            foreign = self.tokens(game, "B")[0]
            phased_token = next(card for card in self.tokens(game, "A") if "soldier" in self.subtypes(game, card))
            self.cast_for(game, "A", ripple, [phased_token.ref])
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[phased_token.object_id].phased_out)
            self.cast_for(game, "A", tap, [treasure.ref])
            self.assertEqual("semantic.choice", self.resolve(game).kind)
            selected = game.act("pilot:A", {"action_id": "choose", "choice": "tap"})
            self.assertTrue(selected.ok, selected.summary)
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[treasure.object_id].tapped)

        self.cast_for(game, "A", chief)
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda item: item.state.phase == "combat" and item.state.step == "beginning_combat"
                   and bool(item.state.stack))
        self.assertEqual([chief.object_id], [item.source_object_id for item in game.state.stack])
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        myr, = (card for card in self.tokens(game, "A") if "myr" in self.subtypes(game, card))
        return game, treasure, myr, phased_token, foreign, legacy

    def test_actual_optional_copy_uses_the_new_myr_and_preserves_original_token_incarnations(self):
        report, semantics = register_card(self.db, self.registry, "Brudiclad, Telchor Engineer")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        original = self.db.lookup("Brudiclad, Telchor Engineer", fuzzy=False)
        trigger, = (program for program in semantics.programs_for_oracle(original.oracle_id)
                    if program.event == "step.begin")
        self.assertEqual(("create_token", "copy_all_tokens"), tuple(effect["op"] for effect in trigger.effects))
        create, copy = trigger.effects
        for mutated in (replace(trigger, effects=({**create, "controller": "$opponent"}, copy)),
                        replace(trigger, effects=(create, {**copy, "schema_version": 1})),
                        replace(trigger, event_condition={"field": "step", "op": "eq", "value": "end_combat"})):
            self.assertFalse(bind_semantic_program_runtime(
                mutated, capability_registry=self.registry, profile="commander_review",
            )["strict"])
        with patch("quorune.compiler.fixed_effect_clause_sequences._optional_controlled_token_copy_clauses",
                   return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(
                original, capability_registry=self.registry, capability_profile="commander_review",
            ).status)

        game, treasure, myr, phased_token, foreign, _ = self.scene(61127268, phased=True)
        public = game.packet("pilot:A", full=True)["decision"]["ctx"]
        schema = public["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 1), (schema["minimum"], schema["maximum"]))
        self.assertNotIn(phased_token.ref, schema["legal_refs"])
        self.assertIn(myr.ref, schema["legal_refs"])
        prior = game.state.cards[treasure.object_id]
        original_identity = (prior.logical_object_id, prior.zone_timestamp, prior.tapped)
        phased_copy = game.state.cards[phased_token.object_id].annotations.get("copy_overrides")
        before = authoritative_state_hash(game.state)
        for actor, ref in (("B", myr.ref), ("A", foreign.ref), ("A", phased_token.ref)):
            self.assertFalse(game.act("pilot:" + actor, {"action_id": "choose", "card": ref}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "card": myr.ref})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        changed = game.state.cards[treasure.object_id]
        self.assertEqual(original_identity, (changed.logical_object_id, changed.zone_timestamp, changed.tapped))
        self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, changed))
        self.assertEqual({"soldier"}, self.subtypes(game, game.state.cards[foreign.object_id]))
        phased = game.state.cards[phased_token.object_id]
        self.assertTrue(phased.phased_out)
        self.assertEqual(phased_copy, phased.annotations.get("copy_overrides"))
        for token in self.tokens(game, "A"):
            if not token.phased_out:
                self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, token))
                self.assertIn("haste", {keyword.casefold() for keyword in game.engine._effective_card_data(token)["keywords"]})
        self.replay(game, load=True)

    def test_actual_decline_keeps_the_created_myr_and_the_original_treasure(self):
        game, treasure, myr, _, _, _ = self.scene(61127269)
        declined = game.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"treasure"}, self.subtypes(game, game.state.cards[treasure.object_id]))
        self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, game.state.cards[myr.object_id]))
        self.replay(game, load=True)

    def test_the_reviewed_mandatory_choice_retains_its_serialized_replay(self):
        game, treasure, myr, _, _, legacy = self.scene(61127270, reviewed=True)
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"][0]["choice_schema"]
        self.assertFalse(schema.get("optional", False))
        chosen = game.act("pilot:A", {"action_id": "choose", "card": myr.ref})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, game.state.cards[treasure.object_id]))
        reloaded = self.replay(game, load=True)
        self.assertEqual(legacy.effects, reloaded.engine.semantics.get(legacy.key).effects)


    def test_later_indefinite_token_copy_survives_the_earlier_temporary_copy_cleanup(self):
        game = self.playing(61127274)
        chief = self.add(game.engine, "Brudiclad, Telchor Engineer", zone="hand")
        rich = self.add(game.engine, "Strike It Rich", zone="hand")
        temporary = self.add(game.engine, "Masterful Replication", zone="hand")
        tap = self.add(game.engine, "Twiddle", zone="hand")
        model = self.add(game.engine, "Sol Ring")
        for land_name in ("Island", "Plains"):
            for i in range(4):
                self.add(game.engine, land_name, ref=f"copy-cleanup-{land_name.lower()}-{i}")
        for card in (chief, rich, temporary, model, tap):
            self.assert_closed(game, card)
        self.prepare(game, actor="A", active="A", mana={"C": 8, "R": 2, "U": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", rich)
        self.assertIsNone(self.resolve(game))
        treasure, = self.tokens(game, "A")
        self.cast_for(game, "A", tap, [treasure.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        tapped = game.act("pilot:A", {"action_id": "choose", "choice": "tap"})
        self.assertTrue(tapped.ok, tapped.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[treasure.object_id].tapped)
        self.cast_for(game, "A", chief)
        self.assertIsNone(self.resolve(game))
        self.until(game, lambda item: item.state.phase == "combat" and item.state.step == "beginning_combat"
                   and bool(item.state.stack))
        self.assertEqual([chief.object_id], [item.source_object_id for item in game.state.stack])
        self.priority(game, "A")
        action = self.action(game, "A", game.state.cards[temporary.object_id])
        cast = game.act("pilot:A", {"action_id": action["id"], "modes": ["mode_2"], "targets": [model.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.resolve_top(game)
        current_treasure = game.state.cards[treasure.object_id]
        self.assertEqual("battlefield", current_treasure.zone)
        self.assertEqual("Sol Ring", game.engine._effective_card_data(current_treasure)["name"])
        pending = self.resolve(game)
        self.assertEqual((pending.kind, pending.actors), ("semantic.choice", ["A"]))
        myr, = (token for token in self.tokens(game, "A") if "myr" in self.subtypes(game, token))
        chosen = game.act("pilot:A", {"action_id": "choose", "card": myr.ref})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, game.state.cards[treasure.object_id]))
        self.until(game, lambda item: item.state.active_player == "B" and item.state.step == "upkeep")
        self.assertEqual({"phyrexian", "myr"}, self.subtypes(game, game.state.cards[treasure.object_id]))
        self.replay(game, load=True)
