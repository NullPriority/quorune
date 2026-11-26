from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.granted_activation_model import (
    GRANTING_ATTACHMENT_PAYMENT_FIELD,
    GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY,
    GRANTING_ATTACHMENT_SACRIFICE_REF,
    GrantingAttachmentPayment,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


class GrantingEquipmentReflexiveTests(InheritedNativeFixture, unittest.TestCase):
    equipment_name = "Ember Rig"
    amount = 3

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "granting-equipment-reflexive.sqlite"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-restriction-material-cards.json",
            fixtures / "granting-equipment-reflexive-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Granting Equipment witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def attack_with_grant(self, *, owner="A", response=None):
        game = self.session(60312901)
        attacker = self.add(game.engine, "Generic Bound Body")
        equipment = self.add(game.engine, self.equipment_name, seat=owner)
        other = self.add(game.engine, "Generic Bound Body", ref="unrelated-attacker")
        secret = self.add(game.engine, "Generic Bound Growth", seat="C", zone="hand",
                          ref="private-granting-equipment-witness")
        answer = self.add(game.engine, response, seat="B", zone="hand") if response else None
        if response:
            for index, land in enumerate(("Swamp", "Swamp", "Plains", "Plains")):
                self.add(game.engine, land, seat="B", ref=f"response-land-{index}")
        attach_objects(game.state.cards, equipment, attacker, source_timestamp=equipment.zone_timestamp,
                       players=game.state.players)
        for card in (attacker, equipment, other):
            self.assert_closed(game, card)
        if answer is not None:
            self.assert_closed(game, answer)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(attacker, "B"), (other, "B")])
        parent, = game.state.stack
        self.assertEqual(("A", attacker.object_id, []),
                         (parent.controller, parent.source_object_id, parent.targets))
        bound = GrantingAttachmentPayment.from_dict(parent.context[GRANTING_ATTACHMENT_PAYMENT_FIELD])
        self.assertEqual((equipment.object_id, equipment.logical_object_id),
                         (bound.granting_object.object_id, bound.granting_object.logical_object_id))
        return game, attacker, equipment, other, secret, answer, parent

    def accept(self, game, seat="A", **fields):
        accepted = game.act("pilot:" + seat, {"action_id": "choose", **fields})
        self.assertTrue(accepted.ok, accepted.summary)

    def test_exact_granter_sacrifice_precedes_its_separate_target_and_replays(self):
        game, attacker, gear, other, secret, _, parent = self.attack_with_grant()
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:D", full=True)))
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        for seat, ref in (("B", gear.ref), ("A", attacker.ref), ("A", other.ref)):
            result = game.act("pilot:" + seat, {"action_id": "choose", "cards": [ref]})
            self.assertFalse(result.ok, "only the granting Equipment can pay")
            self.assertEqual(before, authoritative_state_hash(game.state))
        self.accept(game, cards=[gear.ref])
        child, = game.state.stack
        self.assertEqual(parent.ref, child.context["reflexive_parent"])
        self.assertEqual(attacker.object_id, child.source_object_id)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        self.assertEqual("graveyard", game.state.cards[gear.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[attacker.object_id].zone)

        game = self.replay(game, load=True)
        self.target(game, "A", ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40 - self.amount, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_departed_attacker_leaves_the_same_payment_and_its_damage_source(self):
        game, attacker, gear, _, _, answer, parent = self.attack_with_grant(response="Murder")
        self.cast(game, "B", answer, [attacker.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[attacker.object_id].zone)
        self.assertIsNone(game.state.cards[gear.object_id].attached_to)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.accept(game, cards=[gear.ref])
        child, = game.state.stack
        self.assertEqual((parent.ref, attacker.object_id),
                         (child.context["reflexive_parent"], child.source_object_id))
        self.target(game, "A", ["C"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40 - self.amount, game.state.players["C"].life)
        self.replay(game, load=True)

    def test_foreign_or_departed_granter_cannot_pay(self):
        for mode in ("foreign", "departed"):
            with self.subTest(mode=mode):
                game, _, gear, _, _, answer, _ = self.attack_with_grant(
                    owner="D" if mode == "foreign" else "A",
                    response="Disenchant" if mode == "departed" else None,
                )
                if answer is not None:
                    self.cast(game, "B", answer, [gear.ref])
                    self.resolve_top(game)
                    self.assertEqual("graveyard", game.state.cards[gear.object_id].zone)
                decision = self.resolve(game)
                if decision is not None:
                    self.assertEqual("semantic.choice", decision.kind)
                    before = authoritative_state_hash(game.state)
                    result = game.act("pilot:A", {"action_id": "choose", "cards": [gear.ref]})
                    self.assertFalse(result.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    self.accept(game, cards=[])
                self.assertFalse(game.state.stack)
                self.assertEqual(40, game.state.players["C"].life)
                expected = "battlefield" if mode == "foreign" else "graveyard"
                self.assertEqual(expected, game.state.cards[gear.object_id].zone)
                self.replay(game, load=True)

    def test_grant_and_child_independently_require_the_granting_payment_capability(self):
        report, semantics = register_card(self.db, self.registry, self.equipment_name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        card = self.db.lookup(self.equipment_name, fuzzy=False)
        programs = tuple(p for p in semantics.programs_for_oracle(card.oracle_id)
                         if GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY in p.capability_dependencies)
        self.assertEqual({"characteristics.evaluate", "creature.attacks"}, {p.event for p in programs})
        for program in programs:
            dependencies = tuple(cap for cap in program.capability_dependencies
                                 if cap != GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY)
            closure = self.registry.closure(dependencies, profile="commander_review").to_dict()
            weakened = replace(program, capability_dependencies=dependencies, capability_closure=closure)
            checked = bind_semantic_program_runtime(
                weakened, capability_registry=self.registry, profile="commander_review",
            )
            self.assertFalse(checked["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + GRANTING_ATTACHMENT_REFLEXIVE_CAPABILITY,
                          checked["blockers"])
            if program.event == "creature.attacks":
                self.assertEqual(GRANTING_ATTACHMENT_SACRIFICE_REF, program.effects[0]["payment"]["source_ref"])

    def test_mutant_cannot_substitute_the_attacker_for_its_granter(self):
        from quorune.semantic_runtime import values

        test = type(self)("test_exact_granter_sacrifice_precedes_its_separate_target_and_replays")
        result = unittest.TestResult()
        with patch.object(values, "_resolve_granting_attachment_sacrifice",
                          side_effect=lambda host, item: host.state.cards[item.source_object_id].ref):
            test.run(result)
        self.assertFalse(result.errors)
        self.assertEqual(1, len(result.failures))
        self.assertIn("only the granting Equipment can pay", result.failures[0][1])


if __name__ == "__main__":
    unittest.main()
