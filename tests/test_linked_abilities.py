from __future__ import annotations

import json
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune.replacement.ordering import ReplacementChoiceRequired, replacement_choice_payload


def move_choosing_entry_name(test, engine, card, name):
    with test.assertRaises(ReplacementChoiceRequired) as suspended:
        engine.move_card(card.object_id, "battlefield", controller=card.controller, log=False)
    choice = suspended.exception
    effect_id, = replacement_choice_payload(choice.pending, choice.effects)["card_name_choices"]
    return engine.move_card(
        card.object_id,
        "battlefield",
        controller=card.controller,
        log=False,
        replacement_selections=({"effect_id": effect_id, "card_name": name},),
    )


class LinkedAbilityRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = "A"
        return session.engine

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner
            and card.is_card_object
            and card.printed_name == name
        )

    def test_contract_traces_every_cr_607_rule(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads(
            (
                root
                / "mechanics"
                / "contracts"
                / "linked-abilities.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            {
                "607",
                "607.1",
                "607.1a",
                "607.1b",
                "607.1c",
                "607.1d",
                "607.2",
                "607.2a",
                "607.2b",
                "607.2c",
                "607.2d",
                "607.2e",
                "607.2f",
                "607.2g",
                "607.2h",
                "607.2i",
                "607.2j",
                "607.2k",
                "607.2m",
                "607.2n",
                "607.2p",
                "607.2q",
                "607.3",
                "607.4",
                "607.5",
                "607.5a",
            },
            {
                rule_id
                for rule_id in contract["rule_references"]
                if str(rule_id).startswith("607")
            },
        )

    def test_undefined_chosen_name_has_no_effect(self):
        engine = self.make_engine(60701)
        needle = self.card(engine, "A", "Pithing Needle")
        top = self.card(engine, "A", "Sensei's Divining Top")
        move_choosing_entry_name(self, engine, needle, top.printed_name)
        self.assertEqual(top.printed_name, needle.annotations.pop("chosen_name"))
        engine.move_card(
            top.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        undefined_hints = engine._priority_action_hints("A")
        self.assertTrue(
            any(
                ability.get("s") == top.ref
                for ability in undefined_hints["abilities"]
            )
        )

        needle.annotations["chosen_name"] = (
            "Sensei's Divining Top"
        )
        defined_hints = engine._priority_action_hints("A")
        self.assertFalse(
            any(
                ability.get("s") == top.ref
                for ability in defined_hints["abilities"]
            )
        )

        needle.annotations.pop("chosen_name")
        restored_hints = engine._priority_action_hints("A")
        self.assertTrue(
            any(
                ability.get("s") == top.ref
                for ability in restored_hints["abilities"]
            )
        )


if __name__ == "__main__":
    unittest.main()
