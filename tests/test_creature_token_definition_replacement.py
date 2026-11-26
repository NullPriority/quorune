from __future__ import annotations

"""Exact controller-scoped creature token definition replacement."""

from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.token_templates import static_creature_token_definition_replacement_handler
from quorune.creature_token_definition_replacement import (
    CREATION_TYPE_LINE, CREATURE_TOKEN_DEFINITION_CAPABILITY,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement_effects import ReplacementEffect
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.token_replacements import (
    CreatureTokenDefinitionReplacementHandler, TokenCreationReplacementContext,
    resolve_token_creation_replacements,
)
from scripts.build_test_database import build_fixture_database


ORIGINAL = "Divine Visitation"


class CreatureTokenDefinitionReplacementTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Creature token definition", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "creature-token-definition.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "per-opponent-attacking-token-cards.json",
            fixtures / "additional-token-definition-cards.json",
            fixtures / "equipped-copy-recipe-cards.json",
            fixtures / "fixed-distributed-token-cards.json",
            fixtures / "controlled-aura-receiver-base-characteristics-cards.json",
            fixtures / "bound-effect-program-cards.json",
        ], path)
        cls.use_database(path)

    def tokens(self, game, seat="A"):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.is_token and card.controller == seat]

    def describe(self, game, seat="A"):
        return Counter(card.printed_name for card in self.tokens(game, seat))

    def scene_with_replacement(self, seed, *names):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL)
        cards = [self.add(game.engine, name, zone="hand") for name in names]
        self.assert_closed(game, source)
        for card in cards:
            self.assert_closed(game, card)
        return game, source, cards

    def submit(self, game, seat, command, *, valid=True):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(game.state))

    def test_mixed_creation_replaces_only_current_creatures_and_rebuilds_event_types(self):
        compiled = static_creature_token_definition_replacement_handler(self.db.lookup(ORIGINAL).oracle_text)
        handler = CreatureTokenDefinitionReplacementHandler()
        effect = handler.replacement_effect(compiled[1], TokenCreationReplacementContext(
            source_ref="source", source_controller="A", event_controller="A",
            created_types=("artifact", "creature"), created_subtypes=("construct", "treasure"),
        ))
        self.assertEqual(effect, ReplacementEffect.from_dict(effect.to_dict()))
        copied = {
            "name": "Original", "quantity": 2, "tapped": True, "attacking": "B",
            "attacking_group": "branch", "copy_of": "sample", "copy_snapshot": {"cards": "captured"},
            "temporary_keywords": ["Haste"], CREATION_TYPE_LINE: "Artifact Creature — Construct",
            "effect_entry_counters": [{"counter": "+1/+1", "amount": 1}],
        }
        treasure = {"name": "Treasure Token", "quantity": 1, "characteristics": {
            "type_line": "Token Artifact — Treasure",
        }}
        for controller in "AB":
            result = resolve_token_creation_replacements(
                event_id=f"event-{controller}", controller=controller, tokens=(copied, treasure),
                created_types=("artifact", "creature"), created_subtypes=("construct", "treasure"),
                effects=(effect,), apnap_order=tuple("ABCD"),
            )
            rewritten, unchanged = result.tokens
            self.assertEqual("Treasure Token", unchanged["name"])
            self.assertEqual(1, unchanged["quantity"])
            self.assertEqual((2, True, "B", "branch"), (rewritten["quantity"], rewritten["tapped"],
                                                        rewritten["attacking"], rewritten["attacking_group"]))
            self.assertEqual(("Haste",), tuple(rewritten["temporary_keywords"]))
            self.assertEqual(1, rewritten["effect_entry_counters"][0]["amount"])
            self.assertEqual(controller == "A", "copy_of" not in rewritten)
            self.assertEqual(("angel", "treasure") if controller == "A" else ("construct", "treasure"),
                             tuple(result.event.payload["created_subtypes"]))
            self.assertEqual(("artifact", "creature"), tuple(result.event.payload["created_types"]))
        for changes in ({"schema_version": True}, {"condition": {"event_controller": "opponent"}},
                        {"definition": {"name": "Treasure", "type_line": "Token Artifact — Treasure"}}):
            with self.assertRaises(SemanticNodeError):
                handler.validate({**compiled[1], **changes})

    def test_real_creating_effect_and_later_clue_keep_only_their_independent_instructions(self):
        game, _, cards = self.scene_with_replacement(61416001, "Hard Evidence", "Raise the Alarm")
        self.prepare(game, mana={"C": 1, "U": 1, "W": 1})
        self.checkpoint(game)
        for card in cards:
            self.cast(game, "A", card)
            self.assertIsNone(self.resolve(game))
        self.assertEqual(Counter({"Angel Token": 3, "Clue Token": 1}), self.describe(game))
        for card in self.tokens(game):
            if card.printed_name == "Angel Token":
                values = game.engine._copyable_characteristics(card)
                self.assertEqual((4, 4), (int(values["power"]), int(values["toughness"])))
                self.assertEqual({"Flying", "Vigilance"}, set(values["keywords"]))
                self.assertEqual(["W"], list(values["colors"]))
            else:
                self.assertTrue(any(ability.sacrifice_source for ability in game.engine._activated_abilities(card)))
        self.replay(game, load=True)

    def test_native_mixed_replacement_keeps_food_when_creatures_become_angels(self):
        game, _, cards = self.scene_with_replacement(61416006, "Raise the Alarm")
        source = self.add(game.engine, "Peregrin Took")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", cards[0])
        decision = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        food = next(row for row in options if source.ref in row["id"])
        self.submit(game, "A", {"action_id": "choose", "replacement": food["id"]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(Counter({"Angel Token": 2, "Food Token": 1}), self.describe(game))
        self.assertEqual(1, len({card.zone_timestamp for card in self.tokens(game)}))
        self.replay(game, load=True)

    def test_native_distributed_creation_changes_only_one_creators_members(self):
        game = self.session(61416007)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, ORIGINAL, seat="B")
        spell = self.add(game.engine, "Slaughter Specialist", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "B": 1})
        self.checkpoint(game)
        self.cast(game, "A", spell)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self.tokens(game))
        self.assertEqual(Counter({"Angel Token": 1}), self.describe(game, "B"))
        for seat in "CD":
            self.assertEqual(Counter({"Human Token": 1}), self.describe(game, seat))
        timestamps = {card.zone_timestamp for seat in "BCD" for card in self.tokens(game, seat)}
        self.assertEqual(1, len(timestamps))
        self.replay(game, load=True)

    def test_actual_copy_has_replacement_copiable_values_instead_of_inherited_abilities(self):
        game, _, cards = self.scene_with_replacement(61416002, "Cackling Counterpart")
        enforcer = self.add(game.engine, "Orzhov Enforcer")
        self.assert_closed(game, enforcer)
        self.prepare(game, mana={"C": 1, "U": 2})
        self.checkpoint(game)
        self.cast(game, "A", cards[0], [enforcer.ref])
        self.assertIsNone(self.resolve(game))
        angel, = self.tokens(game)
        self.assertEqual("Angel Token", angel.printed_name)
        self.assertEqual({"Flying", "Vigilance"}, set(game.engine._copyable_characteristics(angel)["keywords"]))
        self.assertEqual("Token Creature — Angel", game.engine._copyable_characteristics(angel)["type_line"])
        self.assertNotEqual(enforcer.oracle_id, angel.oracle_id)
        self.replay(game, load=True)

    def test_native_replacement_order_is_private_and_sees_the_current_members(self):
        for first in ("definition", "additional"):
            with self.subTest(first=first):
                game, _, cards = self.scene_with_replacement(61416003 + (first == "additional"), "Raise the Alarm")
                queen = self.add(game.engine, "Queen Allenal of Ruadach")
                self.assert_closed(game, queen)
                self.prepare(game, mana={"C": 1, "W": 1})
                self.checkpoint(game)
                self.cast(game, "A", cards[0])
                decision = self.resolve(game)
                self.assertEqual(("replacement.order", ["A"]), (decision.kind, decision.actors))
                for viewer in "BCD":
                    self.assertIsNone(game.packet("pilot:" + viewer, full=True)["decision"])
                options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
                order = next(row for row in options if first in row["id"])
                command = {"action_id": "choose", "replacement": order["id"]}
                self.submit(game, "C", command, valid=False)
                self.submit(game, "A", {"action_id": "choose", "replacement": "invalid-choice"}, valid=False)
                game = self.replay(game, load=True)
                self.assertFalse(self.tokens(game))
                self.submit(game, "A", command)
                self.assertIsNone(self.resolve(game))
                expected = Counter({"Angel Token": 2, "Soldier Token": 1} if first == "definition" else {"Angel Token": 3})
                self.assertEqual(expected, self.describe(game))
                self.assertEqual(1, len({card.zone_timestamp for card in self.tokens(game)}))
                self.replay(game, load=True)

    def test_stealing_the_replacement_changes_which_creators_receive_angels(self):
        game, source, cards = self.scene_with_replacement(61416005, "Raise the Alarm")
        theft = self.add(game.engine, "Steal Enchantment", seat="B", zone="hand")
        other_alarm = self.add(game.engine, "Raise the Alarm", seat="B", zone="hand", ref="definition-opponent-raise")
        self.assert_closed(game, theft)
        self.prepare(game, actor="B", active="B", mana={"U": 2, "C": 1, "W": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", theft, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("B", game.state.cards[source.object_id].controller)
        self.cast(game, "A", cards[0])
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", other_alarm)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(Counter({"Soldier Token": 2}), self.describe(game, "A"))
        self.assertEqual(Counter({"Angel Token": 2}), self.describe(game, "B"))
        self.replay(game, load=True)

    def test_verified_capability_withdrawal_and_adjacent_open_grammar_fail_closed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        record = self.db.lookup(ORIGINAL)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CREATURE_TOKEN_DEFINITION_CAPABILITY).update(
            status="blocked", blockers=["Controller token definition owner withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw,
                            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=registry,
                                                       capability_profile="commander_review").status)
        for text in (record.oracle_text.replace("under your control", "under an opponent's control"),
                     record.oracle_text.replace("that many", "twice that many")):
            self.assertNotEqual("exact", compile_oracle_card(replace(record, oracle_text=text),
                                                            capability_registry=self.registry,
                                                            capability_profile="commander_review").status)

    def test_omitting_the_live_definition_operation_fails_the_native_witness(self):
        with patch("quorune.replacement.application._apply_creature_token_definition"):
            with self.assertRaisesRegex(AssertionError, "Angel Token"):
                self.test_real_creating_effect_and_later_clue_keep_only_their_independent_instructions()
