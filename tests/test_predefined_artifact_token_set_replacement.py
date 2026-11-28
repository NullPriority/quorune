"""Predefined artifact substitution through the whole-card and token owners."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.characteristic_evaluation import type_parts
from quorune.compiler.token_templates import (
    static_creature_token_definition_replacement_handler,
    static_predefined_token_set_replacement_handler,
)
from quorune.creature_token_definition_replacement import (
    CREATION_TYPE_LINE,
    PREDEFINED_TOKEN_SET_CAPABILITY,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement_effects import ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.token_replacements import (
    CreatureTokenDefinitionReplacementHandler,
    PredefinedArtifactTokenSetReplacementHandler,
    TokenCreationReplacementContext,
    resolve_token_creation_replacements,
)
from quorune.token_creation import _seal_creation_type_lines
from scripts.build_test_database import build_fixture_database


TEXT = "If you would create a Clue, Food, or Treasure token, instead create one of each."
ORIGINAL = "Academy Manufactor"
PREDEFINED = ("Clue Token", "Food Token", "Treasure Token")


class PredefinedArtifactSetCompilerTests(unittest.TestCase):
    def test_predefined_artifact_set_accepts_closed_oracle_and_rejects_open_neighbors(self):
        registry = load_default_capability_registry()
        record = CardRecord(
            oracle_id="00000000-0000-4000-8000-614500000011",
            name="Constructed Replacement Source", mana_cost="{3}", mana_value=3,
            type_line="Artifact Creature — Assembly-Worker", oracle_text=TEXT,
            power="1", toughness="3", loyalty=None, defense=None, colors=(),
            color_identity=(), keywords=(), produced_mana=(), layout="normal",
            released_at="2026-01-01", legalities={"commander": "legal"}, faces=(), raw={},
        )
        args = {"capability_registry": registry, "capability_profile": "commander_review"}
        ir = compile_oracle_card(record, **args)
        self.assertEqual("exact", ir.status)
        node, = ir.faces[0].nodes
        self.assertIn(PREDEFINED_TOKEN_SET_CAPABILITY, node.capability_dependencies)
        self.assertEqual((0, len(TEXT)), (node.span.start, node.span.end))
        for wording in (
            TEXT.replace("you", "an opponent"),
            TEXT.replace("one of each", "twice that many"),
            TEXT.replace("Food", "Map"),
            TEXT + " Those tokens gain haste.",
        ):
            with self.subTest(wording=wording):
                self.assertIsNone(static_predefined_token_set_replacement_handler(wording))
                other = compile_oracle_card(replace(record, oracle_text=wording), **args)
                self.assertTrue(other.material_residuals)

    def test_predefined_artifact_set_rewrites_only_matching_current_members(self):
        _, descriptor, _ = static_predefined_token_set_replacement_handler(TEXT)
        handler = PredefinedArtifactTokenSetReplacementHandler()
        context = TokenCreationReplacementContext(
            source_ref="source", source_controller="A", event_controller="A",
            created_types=("artifact", "creature"), created_subtypes=("food", "treasure", "soldier"),
        )
        first = handler.replacement_effect(descriptor, context)
        second = handler.replacement_effect(descriptor, replace(context, source_ref="second"))
        self.assertEqual(first, ReplacementEffect.from_dict(first.to_dict()))
        copied = {
            "name": "Different Food copy", "quantity": 2, "tapped": True,
            "copy_of": "card", "copy_snapshot": {"sealed": True}, "attacking": "B",
            "_attacking_group": "branch",
            CREATION_TYPE_LINE: "Artifact Creature — Food Treasure Golem",
            "effect_entry_counters": [{"counter": "+1/+1", "amount": 1}],
        }
        unrelated = {
            "name": "Food", "quantity": 1,
            "characteristics": {"type_line": "Token Creature — Soldier"},
        }
        for controller, n in (("A", 6), ("B", 0)):
            with self.subTest(controller=controller):
                result = resolve_token_creation_replacements(
                    event_id="creation:" + controller, controller=controller,
                    tokens=(copied, unrelated), created_types=context.created_types,
                    created_subtypes=context.created_subtypes, effects=(first, second),
                    selections=(first.effect_id,) if controller == "A" else (),
                    apnap_order=tuple("ABCD"),
                )
                self.assertIsNone(result.pending)
                counts = Counter()
                for spec in result.tokens:
                    counts[spec["name"]] += spec["quantity"]
                if controller == "B":
                    self.assertEqual({"Different Food copy": 2, "Food": 1}, counts)
                    self.assertEqual((), result.journal)
                    continue
                self.assertEqual({**dict.fromkeys(PREDEFINED, n), "Food": 1}, counts)
                self.assertEqual([first.effect_id, second.effect_id], [row.effect_id for row in result.journal])
                self.assertEqual(("clue", "food", "soldier", "treasure"), tuple(result.event.payload["created_subtypes"]))
                for spec in result.tokens[:-1]:
                    self.assertTrue(spec["tapped"])
                    self.assertEqual(1, spec["effect_entry_counters"][0]["amount"])
                    for removed in ("copy_of", "copy_snapshot", "attacking", "_attacking_group"):
                        self.assertNotIn(removed, spec)
                self.assertEqual(unrelated, result.tokens[-1])
        with self.assertRaises(SemanticNodeError):
            handler.validate({**descriptor, "event_controller": "opponent"})

        copied_type = "Artifact Creature — Construct"
        override_type = "Artifact — Food"
        overridden = {
            "name": "Rewritten Copy", "quantity": 1, "copy_of": "card",
            "copy_snapshot": {"characteristics": {"type_line": copied_type}},
            "characteristics": {"type_line": override_type},
        }
        host = SimpleNamespace(_type_parts=type_parts)
        old_types, old_subtypes = {"artifact", "creature"}, {"construct"}
        _, older_descriptor, _ = static_creature_token_definition_replacement_handler(
            "If one or more creature tokens would be created under your control, "
            "that many 4/4 white Angel creature tokens with flying and vigilance are created instead."
        )
        legacy = CreatureTokenDefinitionReplacementHandler().replacement_effect(older_descriptor, context)
        for effect, expected_type, expected_types, expected_subtypes in (
            (first, override_type, {"artifact"}, {"food"}),
            (legacy, copied_type, old_types, old_subtypes),
        ):
            (member,), types, subtypes = _seal_creation_type_lines(
                host, "A", (overridden,), (effect,), old_types, old_subtypes,
            )
            self.assertEqual((expected_type, expected_types, expected_subtypes),
                             (member[CREATION_TYPE_LINE], types, subtypes))


class PredefinedArtifactSetNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "predefined-artifact-token-set.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([fixtures / name for name in (
            "predefined-artifact-token-set-cards.json",
            "additional-token-definition-cards.json",
            "bound-effect-program-cards.json",
            "devour-cards.json",
            "fixed-distributed-token-cards.json",
            "token-creation-trigger-cards.json",
        )], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Artifact token replacement", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @staticmethod
    def tokens(game, controller="A"):
        return [
            card for card in game.state.cards.values()
            if card.zone == "battlefield" and card.is_token and card.controller == controller
        ]

    def counts(self, game, controller="A"):
        return Counter(card.printed_name for card in self.tokens(game, controller))

    def stage(self, seed, *names):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        self.assert_closed(game, source)
        cards = [self.add(game.engine, name, zone="hand") for name in names]
        for card in cards:
            self.assert_closed(game, card)
        return game, source, cards

    def choose(self, game, source, *, invalid_commands=False):
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        option, = [row for row in options if f":{source.ref}:" in row["id"]]
        command = {"action_id": "choose", "replacement": option["id"]}
        if invalid_commands:
            before = authoritative_state_hash(game.state)
            self.assertFalse(game.act("pilot:C", command).ok)
            self.assertFalse(game.act("pilot:A", {**command, "replacement": "unissued"}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_actual_treasure_order_uses_current_tokens_and_resumes_private_choice(self):
        for first in ("manufactor", "xorn"):
            with self.subTest(first=first):
                game, source, (maker,) = self.stage(61450011 + (first == "xorn"), "Strike It Rich")
                xorn = self.add(game.engine, "Xorn")
                self.assert_closed(game, xorn)
                private = game.state.cards[game.state.players["D"].zones["hand"][0]]
                self.prepare(game, mana={"R": 1})
                self.checkpoint(game)
                self.cast(game, "A", maker)
                decision = self.resolve(game)
                self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
                self.assertFalse(self.tokens(game))
                for viewer in "BCD":
                    self.assertIsNone(game.packet("pilot:" + viewer, full=True)["decision"])
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                game = self.replay(game, load=True)
                self.choose(game, source if first == "manufactor" else xorn, invalid_commands=True)
                self.assertIsNone(self.resolve(game))
                expected = dict.fromkeys(PREDEFINED, 2)
                if first == "manufactor":
                    expected.update({"Clue Token": 1, "Food Token": 1})
                self.assertEqual(expected, self.counts(game))
                self.assertEqual(1, len({card.zone_timestamp for card in self.tokens(game)}))
                self.replay(game, load=True)

    def test_actual_two_sources_see_same_batch_then_apply_again_to_next_investigation(self):
        game, first, (initial, second) = self.stage(61450013, "Strike It Rich", "Hard Evidence")
        other = self.add(game.engine, ORIGINAL, ref="second-manufactor")
        self.assert_closed(game, other)
        self.prepare(game, mana={"R": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", initial)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose(game, first)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(dict.fromkeys(PREDEFINED, 3), self.counts(game))
        self.cast(game, "A", second)
        self.assertEqual("replacement.order", self.resolve(game).kind)
        self.choose(game, other)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({**dict.fromkeys(PREDEFINED, 6), "Crab Token": 1}, self.counts(game))
        batches = [event.details for event in game.state.events if event.code == "token.create"]
        self.assertEqual([9, 1, 9], [len(event["objects"]) for event in batches])
        self.assertEqual([2, 0, 2], [len(event["replacement_order"]) for event in batches])
        self.replay(game, load=True)

    def test_actual_distributed_food_follows_creator_and_rediscovers_xorn(self):
        game = self.session(61450014)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL, seat="D")
        game.engine.change_control(source.object_id, "A")
        xorn = self.add(game.engine, "Xorn")
        maker = self.add(game.engine, "Elephant-Mandrill", seat="C", zone="hand")
        for card in (source, xorn, maker):
            self.assert_closed(game, card)
        self.prepare(game, actor="C", active="C", mana={"C": 2, "G": 1})
        self.checkpoint(game)
        self.cast(game, "C", maker)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"Clue Token": 1, "Food Token": 1, "Treasure Token": 2}, self.counts(game))
        for seat in "BCD":
            self.assertEqual({"Food Token": 1}, self.counts(game, seat))
        created = [card for seat in "ABCD" for card in self.tokens(game, seat)]
        self.assertEqual(1, len({card.zone_timestamp for card in created}))
        self.assertTrue(all(card.owner == card.controller for card in created))
        current = game.state.cards[source.object_id]
        self.assertEqual(("D", "A"), (current.owner, current.controller))
        self.replay(game, load=True)

    def test_actual_copy_uses_predefined_characteristics_and_typed_abilities(self):
        game, _, (maker,) = self.stage(61450015, "Cackling Counterpart")
        cookie = self.add(game.engine, "Gingerbrute")
        self.assert_closed(game, cookie)
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", maker, [cookie.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(dict.fromkeys(PREDEFINED, 1), self.counts(game))
        for card in self.tokens(game):
            self.assertNotEqual(cookie.oracle_id, card.oracle_id)
            values = game.engine._copyable_characteristics(card)
            self.assertEqual("Token Artifact — " + card.printed_name.split()[0], values["type_line"])
            ability, = game.engine._activated_abilities(card)
            self.assertTrue(ability.sacrifice_source)
            self.assertEqual(card.printed_name == "Treasure Token", ability.mana_ability)
        self.replay(game, load=True)

    def test_runtime_capability_is_inferred_and_omitting_native_operation_breaks_outcome(self):
        game, source, (maker,) = self.stage(61450016, "Strike It Rich")
        program, = game.engine.semantics.runtime_handler_programs_for_oracle(
            source.oracle_id, active_zone="battlefield", event="token.create",
        )
        remaining = tuple(value for value in program.capability_dependencies if value != PREDEFINED_TOKEN_SET_CAPABILITY)
        self.assertNotEqual(program.capability_dependencies, remaining)
        changed = replace(
            program, capability_dependencies=remaining,
            capability_closure=None,
        )
        binding = bind_semantic_program_runtime(
            changed, capability_registry=self.registry, profile="commander_review",
        )
        self.assertFalse(binding["strict"])
        self.assertIn(f"capability:undeclared_runtime_dependency:{PREDEFINED_TOKEN_SET_CAPABILITY}", binding["blockers"])

        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", maker)
        with patch("quorune.replacement.application._apply_predefined_artifact_token_set"):
            self.assertIsNone(self.resolve(game))
        self.assertEqual({"Treasure Token": 1}, self.counts(game))
