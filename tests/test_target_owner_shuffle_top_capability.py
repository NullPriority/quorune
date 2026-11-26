from __future__ import annotations

from functools import partial
import json
from unittest import TestCase
from unittest.mock import patch

from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    load_default_capability_registry,
)
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.semantics import SemanticRegistry
from quorune.target_permanent_shuffle_reveal_model import WARP_CAPABILITY, WARP_OPERATION
from scripts.update_capability_evidence import DECLARATIONS_PATH, ROOT, build_index, discover_tests
from tests import test_target_owner_shuffle_top as native
from tests.test_target_permanent_shuffle_reveal_compilation import WARP, spell


PROFILE = "commander_review"
TEMPLATE = "target-permanent-shuffle-reveal-v1"
TYPED = ("shuffle_into_library", WARP_OPERATION)
LEGACY = (
    {"op": "shuffle_into_library", "card": "$target.0"},
    {"op": "reveal_top_permanent", "player": "$target.owner.0"},
)


def compile_warp(registry):
    return compile_oracle_card(
        spell(WARP), capability_registry=registry, capability_profile=PROFILE,
    )


def owns_compiler_result(result):
    return result.status == "exact" and any(
        node.template_id == TEMPLATE
        and tuple(effect["op"] for effect in node.effects) == TYPED
        for face in result.faces for node in face.nodes
    )


def withdraw(authority, declarations, discovered_test_ids):
    value = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
    entry = next(row for row in value["capabilities"] if row["id"] == authority)
    entry.update(status="blocked", blockers=["Test withdraws Warp authority"])
    registry = CapabilityRegistry(value)
    index = build_index(
        registry_value=value,
        declaration_source=declarations,
        discovered_test_ids=discovered_test_ids,
    )
    registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
    return registry


class TargetOwnerShuffleTopCapabilityTests(TestCase):
    def test_target_owner_shuffle_top_producer_omission_is_killed(self):
        registry = load_default_capability_registry()
        self.assertTrue(owns_compiler_result(compile_warp(registry)))
        with patch(
            "quorune.compiler.target_permanent_shuffle_reveal_templates.target_permanent_shuffle_reveal_template",
            return_value=None,
        ) as omission:
            changed = compile_warp(registry)
        self.assertTrue(omission.called)
        self.assertFalse(owns_compiler_result(changed))

    @staticmethod
    def _offer(native_case, registry, *, legacy):
        fixture = native_case()
        fixture.registry = registry
        semantics = partial(SemanticRegistry, include_builtin_packs=legacy)
        with patch("quorune.session.load_default_capability_registry", return_value=registry):
            with patch("quorune.session.SemanticRegistry", side_effect=semantics):
                game, source, _, _, _ = fixture.scene(native.PERMANENT_SEED)
        key = f"{source.oracle_id}:spell:front"
        program = game.engine.semantics.get(key)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return program, any(action.get("card") == source.ref for action in actions)

    def test_target_owner_shuffle_top_generated_authority_withdrawal_fails_closed(self):
        accepted = load_default_capability_registry()
        self.assertTrue(owns_compiler_result(compile_warp(accepted)))
        declarations = read_evidence_document(DECLARATIONS_PATH)
        discovered = set(discover_tests(ROOT).values())
        dependencies = accepted.capability(WARP_CAPABILITY)["dependencies"]
        withdrawn = {}
        for authority in (WARP_CAPABILITY, *dependencies):
            with self.subTest(authority=authority):
                registry = withdraw(authority, declarations, discovered)
                withdrawn[authority] = registry
                changed = compile_warp(registry)
                self.assertFalse(owns_compiler_result(changed))
                blockers = [item for residual in changed.material_residuals for item in residual.blockers]
                self.assertTrue(any(f"status:{authority}:blocked" in item for item in blockers), blockers)
                self.assertFalse(any("evidence_index:unverified" in item for item in blockers), blockers)

        case = native.TargetOwnerShuffleTopNativeTests
        case.setUpClass()
        self.addCleanup(case.doClassCleanups)
        program, offered = self._offer(case, accepted, legacy=False)
        self.assertTrue(offered)
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual(TYPED, tuple(effect["op"] for effect in program.effects))

        program, offered = self._offer(case, withdrawn[WARP_CAPABILITY], legacy=False)
        self.assertFalse(offered)
        if program is not None:
            self.assertNotEqual("trusted", program.trust_level)

        program, offered = self._offer(case, accepted, legacy=True)
        self.assertTrue(offered)
        self.assertEqual(TYPED, tuple(effect["op"] for effect in program.effects))

        program, offered = self._offer(case, withdrawn[WARP_CAPABILITY], legacy=True)
        self.assertTrue(offered)
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual(LEGACY, tuple(dict(effect) for effect in program.effects))
        self.assertNotIn(WARP_CAPABILITY, program.capability_dependencies or ())
        self.assertEqual(accepted.fingerprint, load_default_capability_registry().fingerprint)
