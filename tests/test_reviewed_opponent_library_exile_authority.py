"""Production-registry withdrawal and focused mutations for opponent-library exile."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune import mana_restrictions, opponent_library_exile_model, resolving_library_exile
from quorune.carddb import CardDatabase
from quorune.compiler import opposing_owner_entry_life
from quorune.mana_spending_model import ManaSpendingMode, ManaSpendingPolicy
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
from test_controller_creature_keyword_absence import register_card
import test_resolving_library_exile as observation_fixture


PROFILE = "commander_review"
CAPABILITY_WITNESSES = (
    ("library.exile.each_opponent_top", "The Madcap Jester"),
    ("trigger.event.resolving_opponent_library_exile", "The Madcap Jester"),
    ("permission.exile.controlled_opponent_library_batch", "The Madcap Jester"),
    ("permission.exile.opponent_library_incarnation", "Brainstealer Dragon"),
    ("life.loss.opposing_owner_entry_mana_value", "Brainstealer Dragon"),
)


class OpponentLibraryExileAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        folder = cls.enterClassContext(TemporaryDirectory())
        path = Path(folder) / "reviewed-exile-authority.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "madcap-jester-cards.json",
                                fixtures / "opposing-exile-incarnation-cards.json"], path)
        cls.db = cls.enterClassContext(CardDatabase(path))
        cls.registry = load_default_capability_registry()

    def test_withdrawing_each_scoped_authority_closes_its_original_card(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        for card in dict.fromkeys(card for _, card in CAPABILITY_WITNESSES):
            bound, _ = register_card(self.db, self.registry, card)
            self.assertTrue(bound["strict_capability_ready"], bound)
        for capability, card in CAPABILITY_WITNESSES:
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                target = next(row for row in changed["capabilities"] if row["id"] == capability)
                target.update(status="blocked", blockers=["Scoped dependency withdrawn for assurance"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertIn(f"status:{capability}:blocked", registry.closure((capability,), profile=PROFILE).blockers)
                bound, _ = register_card(self.db, registry, card)
                self.assertFalse(bound["strict_capability_ready"], bound)

    def test_each_opponent_instruction_rejects_a_forged_version_under_mutation(self):
        effect = opponent_library_exile_model.each_opponent_top_exile_effect()
        invalid = {**effect, "schema_version": True}

        def witness():
            with self.assertRaises(ValueError):
                opponent_library_exile_model.validate_each_opponent_top_exile(invalid)

        witness()
        with patch.object(opponent_library_exile_model, "validate_each_opponent_top_exile", return_value=False):
            with self.assertRaisesRegex(AssertionError,"ValueError not raised"):
                witness()

    def test_omitting_committed_exile_publication_breaks_the_grouping_witness(self):
        witness = observation_fixture.ResolvingLibraryExileObservationTests(
            "test_committed_moves_group_by_opponent_library_under_resolving_controller",
        )
        witness.setUp()
        witness.test_committed_moves_group_by_opponent_library_under_resolving_controller()
        witness.setUp()
        with patch.object(resolving_library_exile, "_publish_opponent_library_exiles", return_value=None):
            with self.assertRaises(AssertionError):
                witness.test_committed_moves_group_by_opponent_library_under_resolving_controller()

    def test_grant_validation_rejects_a_forged_play_field_under_mutation(self):
        effect = opponent_library_exile_model.opponent_library_exile_play_effect()
        invalid = {**effect, "allow_arbitrary_card": True}

        def witness():
            with self.assertRaises(ValueError):
                opponent_library_exile_model.validate_opponent_library_exile_play(invalid)

        witness()
        with patch.object(opponent_library_exile_model, "validate_opponent_library_exile_play", return_value=None):
            with self.assertRaisesRegex(AssertionError,"ValueError not raised"):
                witness()

    def test_promoting_color_to_type_breaks_the_true_colorless_witness(self):
        context = mana_restrictions.exile_play_mana_spend_context(
            mana_restrictions.spell_mana_spend_context("Instant"), "A", color_only=True,
        )

        def witness():
            mode = mana_restrictions.exile_play_mana_mode(context, "A")
            self.assertIsNone(ManaSpendingPolicy(frozenset({mode})).allocate({"W": 1}, {"C": 1}))

        witness()
        with patch.object(mana_restrictions, "exile_play_mana_mode", return_value=ManaSpendingMode.ANY_TYPE):
            with self.assertRaises(AssertionError):
                witness()

    def test_omitting_opposing_entry_life_breaks_the_original_lowering(self):
        card = self.db.lookup("Brainstealer Dragon", fuzzy=False)

        def witness():
            actual = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual("exact", actual.status, actual.material_residuals)
            self.assertTrue(any("life.loss.opposing_owner_entry_mana_value" in node.capability_dependencies
                                for face in actual.faces for node in face.nodes))

        witness()
        with patch.object(opposing_owner_entry_life, "opposing_owner_entry_result", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
