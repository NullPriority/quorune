"""One current graveyard card supplies copied token values and a separate lifecycle."""

from copy import deepcopy
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
from quorune.token_copy_recipes import (
    GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN, GRAVEYARD_TARGET_TOKEN_COPY_VERSION,
    TokenCopyRecipeSpec, graveyard_target_token_copy_schema,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINAL = "Feldon of the Third Path"


class GraveyardTargetTokenCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "graveyard-target-token-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/graveyard-target-token-copy-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current graveyard token copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_keeps_the_graveyard_target_and_noncopyable_lifecycle(self):
        binding, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        original = self.db.lookup(ORIGINAL, fuzzy=False)
        program, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(("activate", "battlefield"), (program.event, program.active_zone))
        self.assertEqual(graveyard_target_token_copy_schema(), program.target_schema)
        self.assertEqual((2, 1, True), (program.cost_schema["mana"]["GENERIC"],
                                       program.cost_schema["mana"]["R"], program.cost_schema["tap_source"]))
        effect, = program.effects
        recipe = TokenCopyRecipeSpec.from_dict(effect["copy_spec"])
        self.assertEqual((GRAVEYARD_TARGET_TOKEN_COPY_VERSION, GRAVEYARD_TARGET_TOKEN_COPY_ORIGIN),
                         (recipe.schema_version, recipe.origin))
        self.assertEqual(("artifact",), recipe.exception.add_card_types)
        self.assertEqual((), recipe.exception.add_keywords)
        self.assertEqual(("Haste",), recipe.temporary_keywords)
        self.assertEqual("sacrifice_next_end_step", recipe.cleanup)
        self.assertLessEqual({"token.creation.fixed_copy", "continuous.resolution.fixed_keyword_zone_object",
                              "target.revalidate_resolution", "trigger.placement.apnap"},
                             set(program.capability_dependencies))
        wrong = deepcopy(effect)
        wrong["copy_spec"]["cleanup"] = "none"
        for parts in ({"target_schema": {**program.target_schema, "owner_relation": "any"}},
                      {"effects": [wrong]}, {"event": "resolve", "active_zone": "stack", "handlers": []},
                      {"effects": [{**effect, "quantity": 2}]}):
            with self.subTest(parts=parts):
                bound = bind_semantic_program_runtime(
                    replace(program, **parts), capability_registry=self.registry, profile="commander_review",
                )
                self.assertFalse(bound["strict"], bound)
        for text in (
            original.oracle_text.replace("your graveyard", "a graveyard"),
            original.oracle_text.replace("It gains haste. ", ""),
            original.oracle_text.replace("Sacrifice it", "Exile it"),
        ):
            ir = compile_oracle_card(replace(original, oracle_text=text), capability_registry=self.registry)
            self.assertNotEqual("exact", ir.status)

    def activate_copy(self, game, source, chosen, excluded, *, reserve):
        prefix = f"activate:{source.ref}:"
        action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(prefix))
        legal = action["target_schema"]["legal_refs"]
        self.assertIn(chosen.ref, legal)
        command = {"action_id": action["id"], "targets": [chosen.ref], "pay": "auto", "reserve": reserve}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        for card in excluded:
            self.assertNotIn(card.ref, legal)
            self.assertFalse(game.act("pilot:A", {**command, "targets": [card.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(game.state.cards[source.object_id].tapped)

    def tokens_for(self, game, model):
        return tuple(card for card in game.state.cards.values() if card.zone == "battlefield"
                     and card.is_token and card.oracle_id == model.oracle_id)

    def actual_types(self, game, card):
        effective = game.engine._effective_card_data(game.state.cards[card.object_id])
        return game.engine._type_parts(effective["type_line"])[0]

    def test_actual_doubled_copies_enter_draw_and_attack_with_haste_and_only_they_are_sacrificed(self):
        game = self.session(70790221)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        model = self.add(game.engine, "Elvish Visionary", zone="graveyard", ref="feldon-actual-input")
        foreign = self.add(game.engine, "Elvish Visionary", seat="B", zone="graveyard",
                           ref="feldon-other-owner")
        noncreature = self.add(game.engine, "Plains", zone="graveyard", ref="feldon-mismatched-input")
        doubling = self.add(game.engine, "Doubling Season")
        derivative = self.add(game.engine, "Cackling Counterpart", zone="hand")
        for card in (source, model, doubling, derivative):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 1, "U": 2})
        self.checkpoint(game)

        initial_hand = len(game.state.players["A"].zones["hand"])
        self.activate_copy(game, source, model, [foreign, noncreature], reserve={"C": 1, "U": 2})
        game = self.replay(game, load=True)
        self.settle(game)
        originals = self.tokens_for(game, model)
        self.assertEqual(2, len(originals))
        self.assertEqual(initial_hand + 2, len(game.state.players["A"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[model.object_id].zone)
        for token in originals:
            self.assertEqual({"artifact", "creature"}, self.actual_types(game, token))
            self.assertIn("haste", game.engine._combat_keywords(token))

        self.cast_for(game, "A", derivative, [originals[0].ref])
        self.settle(game)
        original_ids = {card.object_id for card in originals}
        new_copies = tuple(card for card in self.tokens_for(game, model) if card.object_id not in original_ids)
        self.assertEqual(2, len(new_copies))
        self.assertEqual(initial_hand + 3, len(game.state.players["A"].zones["hand"]))
        for token in new_copies:
            self.assertEqual({"artifact", "creature"}, self.actual_types(game, token))
            self.assertNotIn("haste", game.engine._combat_keywords(token))

        self.attack(game, [(token, "B") for token in originals])
        self.until(game, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(38, game.state.players["B"].life)
        self.until(game, lambda current: current.state.pending_decision is not None
                   and current.state.pending_decision.kind == "cleanup.discard")
        self.assertEqual({token.object_id for token in new_copies},
                         {token.object_id for token in self.tokens_for(game, model)})
        self.replay(game, load=True)

    def test_actual_card_returns_to_the_same_graveyard_before_resolution_and_remains_illegal(self):
        game = self.session(70790222)
        source = self.add(game.engine, ORIGINAL)
        source.acquired_control_turn_count = -1
        model = self.add(game.engine, "Elvish Visionary", zone="graveyard", ref="feldon-returning-model")
        other = self.add(game.engine, "Runeclaw Bear", zone="graveyard", ref="feldon-unselected-model")
        reclaim = self.add(game.engine, "Reclaim", zone="hand")
        scour = self.add(game.engine, "Thought Scour", zone="hand")
        for card in (source, reclaim, scour):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 1, "G": 1, "U": 1})
        self.checkpoint(game)

        old = model.logical_object_id
        self.activate_copy(game, source, model, [], reserve={"G": 1, "U": 1})
        self.cast_for(game, "A", reclaim, [model.ref])
        self.resolve_top(game)
        self.assertEqual("library", game.state.cards[model.object_id].zone)
        self.cast_for(game, "A", scour, ["A"])
        self.resolve_top(game)
        returned = game.state.cards[model.object_id]
        self.assertEqual("graveyard", returned.zone)
        self.assertNotEqual(old, returned.logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
        self.assertFalse(any(card.is_token and card.zone == "battlefield" for card in game.state.cards.values()))
        self.replay(game, load=True)
