from __future__ import annotations

import json
from unittest import TestCase
from unittest.mock import patch

from quorune.oracle_ir import compile_oracle_card
from quorune.public_library_piles_model import PILE_CAPABILITY
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    load_default_capability_registry,
)
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.update_capability_evidence import (
    DECLARATIONS_PATH,
    ROOT,
    build_index,
    discover_tests,
)
from tests.test_public_library_pile_compilation import FACT, STEAM, spell


class PublicLibraryPilesCapabilityTests(TestCase):
    def test_public_library_piles_producer_omission_is_killed(self):
        registry = load_default_capability_registry()
        for text in (FACT, STEAM):
            with self.subTest(text=text):
                witness = spell("Pile witness", "pile-omission-witness", "{3}{U}", text)
                compile_kwargs = {
                    "capability_registry": registry, "capability_profile": "commander_review",
                }
                intact = compile_oracle_card(witness, **compile_kwargs)
                self.assertEqual("exact", intact.status)
                self.assertTrue(any(
                    node.template_id == "public-library-piles-v1"
                    for face in intact.faces for node in face.nodes
                ))
                with patch(
                    "quorune.compiler.public_library_pile_templates.public_library_piles_template",
                    return_value=None,
                ) as omission:
                    mutant = compile_oracle_card(witness, **compile_kwargs)
                self.assertTrue(omission.called)
                self.assertNotEqual("exact", mutant.status)
                self.assertFalse(any(
                    node.template_id == "public-library-piles-v1"
                    for face in mutant.faces for node in face.nodes
                ))

    def test_public_library_piles_authority_withdrawal_fails_closed(self):
        accepted = load_default_capability_registry()
        witness = spell("Pile witness", "pile-authority-witness", "{3}{U}", FACT)
        profile = "commander_review"
        self.assertEqual("exact", compile_oracle_card(
            witness, capability_registry=accepted, capability_profile=profile,
        ).status)
        declarations = read_evidence_document(DECLARATIONS_PATH)
        discovered_test_ids = set(discover_tests(ROOT).values())
        for authority in (
            PILE_CAPABILITY, "zone.change.destination_replacement", "zone.graveyard.ordered_simultaneous_arrival",
        ):
            with self.subTest(authority=authority):
                document = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
                entry = next(row for row in document["capabilities"] if row["id"] == authority)
                entry.update(status="blocked", blockers=["Test withdraws reviewed authority"])
                registry = CapabilityRegistry(document)
                index = build_index(
                    registry_value=document,
                    declaration_source=declarations,
                    discovered_test_ids=discovered_test_ids,
                )
                registry.mark_evidence_verified(
                    validate_capability_evidence_index(index, registry=registry)
                )
                refused = compile_oracle_card(
                    witness, capability_registry=registry, capability_profile=profile,
                )
                self.assertNotEqual("exact", refused.status)
                blockers = [
                    blocker for residual in refused.material_residuals for blocker in residual.blockers
                ]
                expected = f"status:{authority}:blocked"
                self.assertTrue(any(expected in blocker for blocker in blockers), blockers)
                self.assertFalse(any("evidence_index:unverified" in blocker
                                     for blocker in blockers), blockers)
