from __future__ import annotations

import unittest

from quorune.rules.action_proposals import (
    ActionOffer,
    ActionProposalError,
    ActivationProposal,
    CastCostOption,
    CastProposal,
    FrozenObject,
    action_offer_signature_facts,
    freeze_json,
    thaw_json,
)
from quorune.rules.activation import (
    ActivationProposalError,
    ActivationProposalRequest,
)
from quorune.rules.casting import (
    CastProposalError,
    CastProposalRequest,
)


class ActionProposalModelTests(unittest.TestCase):
    def test_action_offer_deep_freezes_and_fingerprints_canonically(self) -> None:
        payload = {
            "card": "A12",
            "cost_options": [{"id": "normal", "requirements": {"G": 1}}],
        }
        offer = ActionOffer(
            action_id="cast:A12",
            action="cast",
            seat="A",
            label="Cast Elves",
            payload=payload,
        )
        payload["card"] = "B99"
        payload["cost_options"][0]["requirements"]["G"] = 9

        serialized = offer.to_dict()
        self.assertEqual("A12", serialized["card"])
        self.assertEqual(
            1, serialized["cost_options"][0]["requirements"]["G"]
        )
        equivalent = ActionOffer(
            action_id="cast:A12",
            action="cast",
            seat="A",
            label="Cast Elves",
            payload={
                "cost_options": [
                    {"requirements": {"G": 1}, "id": "normal"}
                ],
                "card": "A12",
            },
        )
        self.assertEqual(offer.fingerprint, equivalent.fingerprint)

    def test_action_offer_rejects_an_open_ended_action_name(self) -> None:
        with self.assertRaises(ActionProposalError):
            ActionOffer(
                action_id="arbitrary:A12",
                action="arbitrary",  # type: ignore[arg-type]
                seat="A",
                label="Arbitrary mutation",
            )

    def test_frozen_json_preserves_array_and_object_shapes(self) -> None:
        value = {"rows": [["id", 1]], "empty_array": [], "empty_object": {}}
        self.assertEqual(value, thaw_json(freeze_json(value)))

    def test_direct_frozen_object_construction_is_canonical_and_isolated(self) -> None:
        caller = {"nested": [1]}
        frozen = FrozenObject((("z", caller), ("a", 2)))
        caller["nested"].append(3)

        self.assertEqual(
            {"a": 2, "z": {"nested": [1]}},
            thaw_json(frozen),
        )
        with self.assertRaises(ActionProposalError):
            FrozenObject((("duplicate", 1), ("duplicate", 2)))

    def test_nonfinite_numbers_are_rejected_at_the_model_boundary(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaises(ActionProposalError):
                    freeze_json({"value": value})

    def test_meaningful_signature_facts_ignore_only_offer_freshness(self) -> None:
        offer = ActionOffer(
            action_id="cast:A12",
            action="cast",
            seat="A",
            label="Cast Elves",
            expiry_revision=7,
            payload={"card": "A12", "requirements": {"G": 1}},
        ).to_dict()
        changed_revision = {
            **offer,
            "expiry_revision": 8,
            "proposal_fingerprint": "f" * 64,
        }

        self.assertEqual(
            action_offer_signature_facts(offer),
            action_offer_signature_facts(changed_revision),
        )
        changed_revision["requirements"] = {"G": 2}
        self.assertNotEqual(
            action_offer_signature_facts(offer),
            action_offer_signature_facts(changed_revision),
        )

    def test_cast_proposal_isolated_from_caller_data(self) -> None:
        requirements = {"GENERIC": 1, "G": 1}
        details = {"choice_schema": {"x": {"minimum": 0, "maximum": 3}}}
        proposal = CastProposal(
            seat="A",
            card_ref="A21",
            object_id="object-a21",
            origin="hand",
            face=None,
            type_line="Creature — Elf",
            semantic_key="oracle:spell:front",
            cost_option_id="normal",
            requirements=requirements,
            details=details,
        )
        fingerprint = proposal.fingerprint
        requirements["G"] = 7
        details["choice_schema"]["x"]["maximum"] = 99

        self.assertEqual(1, proposal.to_dict()["requirements"]["G"])
        self.assertEqual(
            3,
            proposal.to_dict()["details"]["choice_schema"]["x"]["maximum"],
        )
        self.assertEqual(fingerprint, proposal.fingerprint)

    def test_proposal_object_fields_fail_closed(self) -> None:
        with self.assertRaises(ActionProposalError):
            CastProposal(
                seat="A",
                card_ref="A21",
                object_id="object-a21",
                origin="hand",
                face=None,
                type_line="Creature — Elf",
                semantic_key="oracle:spell:front",
                cost_option_id="normal",
                requirements=[],
            )

    def test_malformed_submission_shapes_raise_typed_errors(self) -> None:
        with self.assertRaises(CastProposalError):
            CastProposalRequest.from_submission(
                "A", {"card": "A21", "from": 7}
            )
        with self.assertRaises(CastProposalError):
            CastProposalRequest.from_submission(
                "A", {"card": "A21", "modes": "all"}
            )
        with self.assertRaises(ActivationProposalError):
            ActivationProposalRequest.from_submission(
                "A", {"source": "A03", "from": {"battlefield": True}}
            )

    def test_cast_cost_option_is_typed_immutable_and_closed(self) -> None:
        raw = {
            "id": "normal",
            "kind": "printed",
            "requirements": {"GENERIC": 1, "G": 1},
            "choice_schema": {"x": {"minimum": 0, "maximum": 4}},
        }
        option = CastCostOption.from_dict(raw)
        raw["requirements"]["G"] = 3
        self.assertEqual(1, option.to_dict()["requirements"]["G"])
        with self.assertRaises(ActionProposalError):
            CastCostOption.from_dict(
                {
                    "id": "bad",
                    "kind": "printed",
                    "requirements": {"SNOW": 1},
                }
            )

    def test_cast_cost_option_keeps_snow_outside_the_mana_vector(self) -> None:
        base = {"id": "normal", "kind": "mana", "requirements": {"GENERIC": 1}}
        snow = {**base, "snow": 1}
        option = CastCostOption.from_dict(snow)
        self.assertEqual(snow, option.to_dict())
        self.assertNotEqual(CastCostOption.from_dict(base).fingerprint, option.fingerprint)
        for amount in (None, True, 0, -1, 1.0, "1"):
            with self.subTest(amount=amount), self.assertRaisesRegex(ActionProposalError, "snow mana"):
                CastCostOption.from_dict({**base, "snow": amount})

    def test_activation_proposal_fingerprint_covers_payability_facts(self) -> None:
        base = dict(
            seat="A",
            source_ref="A03",
            source_object_id="object-a03",
            source_zone="battlefield",
            ability_id="ab1",
            semantic_key="oracle:ability:ab1",
            mana_ability=False,
        )
        one = ActivationProposal(requirements={"GENERIC": 1}, **base)
        two = ActivationProposal(requirements={"GENERIC": 2}, **base)
        self.assertNotEqual(one.fingerprint, two.fingerprint)


if __name__ == "__main__":
    unittest.main()
