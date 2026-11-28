from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from quorune.ability_fragments import GrantedActivatedAbilitySpec, ability_fragment_to_dict
from quorune.carddb import CardRecord
from quorune.effect_runtime.damage_life_and_turns import _apply_grant_ability_fragment
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.saga_chapter_grants import (
    SAGA_CHAPTER_GRANT_CAPABILITY,
    saga_chapter_grant_effect,
    saga_chapter_grant_runtime_dependencies,
)
from quorune.semantic_runtime.values import resolve_semantic_value


class SagaChapterGrantCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.record = CardRecord(
            oracle_id="00000000-0000-4000-8000-000006116001", name="Fixture Saga", mana_cost="{2}",
            mana_value=2, type_line="Enchantment — Saga", oracle_text='I — This Saga gains "{T}: Add {C}."',
            power=None, toughness=None, loyalty=None, defense=None, colors=(), color_identity=(), keywords=(),
            produced_mana=(), layout="normal", released_at="2026-01-01", legalities={"commander": "legal"},
            faces=(), raw={},
        )

    def compile(self, record):
        return compile_oracle_card(record, capability_registry=self.capabilities, capability_profile="commander_review")

    def test_exact_quoted_source_span_and_independent_mana_child(self):
        chapter, child = self.compile(self.record).faces[0].nodes
        self.assertEqual("saga.chapter.1", chapter.event)
        self.assertEqual("granted_mana_ability", child.kind)
        self.assertTrue(child.exact)
        self.assertEqual("{T}: Add {C}.", self.record.oracle_text[child.span.start:child.span.end])
        self.assertEqual("$source.zone_object", chapter.effects[0]["source"])
        self.assertIn(SAGA_CHAPTER_GRANT_CAPABILITY, chapter.capability_dependencies)
        self.assertEqual([{"C": 1}], chapter.effects[0]["fragment"]["value"]["fixed_mana_outputs"])

    def test_source_grant_route_omission_restores_material_residual(self):
        with patch("quorune.compiler.saga_chapter_grants.saga_chapter_grant", return_value=None):
            ir = self.compile(self.record)
        self.assertEqual("unresolved", ir.faces[0].nodes[0].event)
        self.assertEqual(("saga_chapter",), tuple(row.kind for row in ir.material_residuals))

    def test_missing_grant_capability_blocks_chapter_with_exact_child(self):
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8"))
        raw["capabilities"] = [row for row in raw["capabilities"] if row["id"] != SAGA_CHAPTER_GRANT_CAPABILITY]
        raw["aggregates"] = [
            {**row, "capabilities": [key for key in row["capabilities"] if key != SAGA_CHAPTER_GRANT_CAPABILITY]}
            for row in raw["aggregates"] if set(row["capabilities"]) != {SAGA_CHAPTER_GRANT_CAPABILITY}
        ]
        capabilities = CapabilityRegistry(raw)
        capabilities.mark_evidence_verified(self.capabilities.evidence_fingerprint)
        ir = compile_oracle_card(self.record, capability_registry=capabilities, capability_profile="commander_review")
        chapter, child = ir.faces[0].nodes
        self.assertFalse(chapter.exact)
        self.assertTrue(child.exact)
        self.assertIn(
            f"capability:missing:{SAGA_CHAPTER_GRANT_CAPABILITY}",
            ir.material_residuals[0].blockers,
        )

    def test_missing_source_and_outside_duration_do_not_lower(self):
        for record in (
            replace(self.record, type_line="Enchantment"),
            replace(self.record, oracle_text=self.record.oracle_text + " until end of turn."),
        ):
            with self.subTest(record=record):
                ir = self.compile(record)
                self.assertTrue(ir.material_residuals)
                self.assertFalse(any(node.effects for node in ir.faces[0].nodes))


class SagaChapterGrantTests(unittest.TestCase):
    def setUp(self):
        self.ability = ability_fragment_to_dict(GrantedActivatedAbilitySpec(
            ability_id="ability:granted:front:n2", semantic_key="fixture:ability:granted:front:n2",
            cost_text="{T}", effect_text="Add {C}.", tap_source=True, mana_ability=True,
            fixed_mana_outputs=((("C", 1),),),
        ))
        self.effect = saga_chapter_grant_effect(self.ability)
        self.source = SimpleNamespace(
            object_id="saga", logical_object_id="saga@1", ref="saga-ref",
            zone="battlefield", phased_out=False, controller="B", printed_name="Fixture Saga", annotations={},
        )
        self.item = SimpleNamespace(
            source_object_id=self.source.object_id, context={"source_logical_object_id": "saga@1"},
        )
        self.host = SimpleNamespace(
            state=SimpleNamespace(cards={self.source.object_id: self.source}),
            _resolve_object=Mock(return_value=self.source), _log=Mock(),
        )

    def resolve(self, effect):
        return _apply_grant_ability_fragment(
            self.host, effect, actor="A", operation="grant_ability_fragment", reason="chapter",
        )

    def test_chapter_grant_resolves_on_same_source_after_control_changes(self):
        effect = resolve_semantic_value(self.host, self.effect, self.item)
        effect["_runtime_source"] = {
            "object_id": self.source.object_id, "logical_object_id": self.source.logical_object_id,
            "card_ref": self.source.ref, "stack_ref": "chapter-stack",
        }
        self.resolve(effect)
        self.host._resolve_object.assert_called_once_with("A", "saga-ref", zones={"battlefield"})
        self.assertEqual([self.ability], self.source.annotations["granted_ability_fragments"])
        self.assertEqual("B", self.source.controller)

    def test_departed_or_phased_source_does_not_receive_grant(self):
        for attributes in (
            {"logical_object_id": "saga@2", "zone": "battlefield", "phased_out": False},
            {"logical_object_id": "saga@1", "zone": "graveyard", "phased_out": False},
            {"logical_object_id": "saga@1", "zone": "battlefield", "phased_out": True},
        ):
            with self.subTest(**attributes):
                vars(self.source).update(attributes)
                resolved = resolve_semantic_value(self.host, self.effect, self.item)
                self.assertIsNone(resolved["source"])
                self.assertIsNone(self.resolve(resolved))
                self.assertEqual({}, self.source.annotations)
        self.host._resolve_object.assert_not_called()
        self.host._log.assert_not_called()

    def test_malformed_grant_is_rejected_even_if_source_departed(self):
        with self.assertRaises(GameRuleError):
            self.resolve({**self.effect, "source": None, "fragment": {"kind": "invalid", "value": {}}})
        self.assertEqual({}, self.source.annotations)
        self.host._log.assert_not_called()

    def test_new_capability_requires_an_original_untargeted_chapter(self):
        fields = dict(effects=(self.effect,), event="saga.chapter.1", active_zone="battlefield",
                      target_schema=None, cost_schema=None, handlers=(), event_condition=None)
        self.assertIn(SAGA_CHAPTER_GRANT_CAPABILITY, saga_chapter_grant_runtime_dependencies(SimpleNamespace(**fields)))
        contradictory = (
            {"event": "resolve"},
            {"target_schema": {"zones": ["battlefield"]}},
            {"effects": ({**self.effect, "source": "$source"},)},
            {"effects": ({"op": "sequence", "effects": [self.effect]},)},
        )
        for change in contradictory:
            with self.subTest(change=change), self.assertRaises(ValueError):
                saga_chapter_grant_runtime_dependencies(SimpleNamespace(**{**fields, **change}))

    def test_legacy_opcode_preserves_its_existing_behavior(self):
        legacy = {"op": "grant_ability_fragment", "source": "saga-ref", "fragment": self.ability}
        self.resolve(legacy)
        self.host._resolve_object.assert_called_once_with("A", "saga-ref", zones={"battlefield"}, controlled_only=True)
        self.assertEqual([self.ability], self.source.annotations["granted_ability_fragments"])
