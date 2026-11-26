"""Trusted casting, public disclosure, source-bound results, and replay."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.casting import reveal_costs
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.paid_revealed_creature_results import creature_reveal_cost_schema
from quorune.scalar_effect_amount_model import (
    PAID_REVEALED_CREATURE_CAPABILITY, PAID_REVEALED_CREATURE_CONTEXT, PaidRevealedCreatureSnapshot,
)
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests
import test_fixed_reveal_additional_costs as reveal_fixture


ORIGINALS = ("Induce Despair", "Disaster Radius", "Charge of the Forever-Beast", "Living Destiny")


class PaidRevealedCreatureTests(InheritedNativeFixture, unittest.TestCase):
    fund = reveal_fixture.FixedRevealCostRuntimeTests.fund
    offered = reveal_fixture.FixedRevealCostRuntimeTests.offered
    reject = reveal_fixture.FixedRevealCostRuntimeTests.reject

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "paid-revealed-creature.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/paid-revealed-creature-cards.json"], path)
        cls.use_database(path)

    @classmethod
    def use_database(cls, path):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Revealed creature",
                                  [DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30)], [commander])

    def reveal_cast(self, game, source, revealed, *, target=None, wrong=None):
        self.assert_closed(game, source)
        offered = self.offered(game, source)
        option, = offered["cost_options"]
        legal = option["choice_schema"]["reveal_cards"]["legal_refs"]
        self.assertIn(revealed.ref, legal)
        command = {"action_id": offered["id"], "cost_option": option["id"],
                   "pay": "auto", "reveal_cards": [revealed.ref]}
        if target is not None:
            command["targets"] = [target.ref]
        self.reject(game, **{**command, "reveal_cards": []})
        if wrong is not None:
            self.assertNotIn(wrong.ref, legal)
            self.reject(game, **{**command, "reveal_cards": [wrong.ref]})
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        item = game.state.stack[-1]
        paid = PaidRevealedCreatureSnapshot.from_dict(item.context[PAID_REVEALED_CREATURE_CONTEXT])
        self.assertEqual(revealed.logical_object_id, paid.logical_object_id)
        self.assertEqual(item.ref, paid.stack_ref)
        self.assertEqual("hand", game.state.cards[revealed.object_id].zone)
        return item, paid

    def test_the_four_printed_spells_keep_their_own_paid_numeric_results(self):
        for index, name in enumerate(ORIGINALS):
            with self.subTest(name=name):
                game = self.session(601200 + index)
                spell = self.add(game.engine, name, zone="hand")
                revealed = self.add(game.engine, "Giant Spider" if name == "Disaster Radius" else "Savannah Lions", zone="hand")
                revealed.revealed_to = []
                target = self.add(game.engine, "Grizzly Bears", seat="B")
                friend = self.add(game.engine, "Grizzly Bears", ref="friendly-bears")
                other = self.add(game.engine, "Grizzly Bears", seat="C", ref="third-seat-bears")
                protected = self.add(game.engine, "Silver Knight", seat="D")
                hidden = self.add(game.engine, "Plains", zone="hand", ref="unrevealed-cost-control")
                self.fund(game, {"C": 8, "B": 1, "G": 1, "R": 2})
                _, paid = self.reveal_cast(game, spell, revealed,
                     target=target if name in {"Induce Despair", "Charge of the Forever-Beast"} else None,
                     wrong=hidden)
                self.assertEqual((4, 2) if name == "Disaster Radius" else (1, 2), (paid.mana_value, paid.power))
                preview = game.packet("pilot:B", full=True)
                self.assertIn(revealed.ref, str(preview))
                self.assertNotIn(hidden.ref, str(preview))
                self.assertIsNone(self.resolve(game))
                card = game.state.cards[target.object_id]
                if name == "Induce Despair":
                    self.assertEqual("battlefield", card.zone)
                    self.assertEqual((1, 1), tuple(game.engine._numeric_stat(card.object_id, key)
                                                   for key in ("power", "toughness")))
                elif name == "Disaster Radius":
                    self.assertEqual("graveyard", card.zone)
                    self.assertEqual("graveyard", game.state.cards[other.object_id].zone)
                    self.assertEqual("battlefield", game.state.cards[protected.object_id].zone)
                    self.assertEqual(0, game.state.cards[friend.object_id].marked_damage)
                elif name == "Charge of the Forever-Beast":
                    self.assertEqual("graveyard", card.zone)
                else:
                    self.assertEqual(41, game.state.players["A"].life)
                self.assertFalse(game.state.cards[revealed.object_id].revealed_to)
                self.replay(game, load=True)

    def test_a_copied_spell_keeps_the_sealed_payment_and_the_new_controller(self):
        game = self.session(601210)
        original = self.add(game.engine, "Living Destiny", zone="hand")
        revealed = self.add(game.engine, "Giant Spider", zone="hand")
        revealed.revealed_to = []
        duplicate = self.add(game.engine, "Reverberate", seat="B", zone="hand")
        game.state.players["B"].mana_pool["R"] = 2
        self.fund(game, {"C": 3, "G": 1})
        item, _ = self.reveal_cast(game, original, revealed)
        self.cast_for(game, "B", duplicate, [item.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((44, 44), (game.state.players["A"].life, game.state.players["B"].life))
        self.assertFalse(game.state.cards[revealed.object_id].revealed_to)
        self.replay(game, load=True)

    def test_compiler_requires_the_exact_mandatory_price_and_its_bound_result(self):
        card = self.db.lookup("Living Destiny")
        current = compile_oracle_card(card, capability_registry=self.registry)
        self.assertEqual("exact", current.status, current.material_residuals)
        self.assertEqual(creature_reveal_cost_schema(), current.faces[0].nodes[0].cost)
        for source in (card.oracle_text.replace("reveal a creature", "you may reveal a creature"),
                       card.oracle_text.replace("a creature card", "a permanent card"),
                       card.oracle_text.replace("mana value", "toughness"),
                       card.oracle_text + " Draw a card."):
            with self.subTest(oracle=source):
                ir = compile_oracle_card(replace(card, oracle_text=source), capability_registry=self.registry)
                self.assertNotEqual("exact", ir.status)

    def test_withholding_only_paid_reveal_preserves_unrelated_stock_life_gain(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == PAID_REVEALED_CREATURE_CAPABILITY)
        row.update(status="blocked", blockers=["Paid reveal evidence withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        card = self.db.lookup("Living Destiny")
        blocked = compile_oracle_card(card, capability_registry=registry)
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(any(PAID_REVEALED_CREATURE_CAPABILITY in blocker
                            for residual in blocked.material_residuals for blocker in residual.blockers))
        self.assertEqual("exact", compile_oracle_card(
            replace(card, oracle_text="You gain 1 life."), capability_registry=registry,
        ).status)

    def test_receipt_wire_and_stat_swap_mutation_are_checked(self):
        valid = PaidRevealedCreatureSnapshot("S1", "hand-object@0", 4, 3)
        self.assertEqual(valid, PaidRevealedCreatureSnapshot.from_dict(valid.to_dict()))
        for edit in ({"mana_value": True}, {"power": True}, {"stack_ref": ""}, {"logical_object_id": None}):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                PaidRevealedCreatureSnapshot.from_dict({**valid.to_dict(), **edit})
        self.test_a_copied_spell_keeps_the_sealed_payment_and_the_new_controller()
        capture = reveal_costs.PaidRevealedCreatureSnapshot

        def swap(**fields):
            return capture(**{**fields, "mana_value": fields["power"]})

        with patch.object(reveal_costs, "PaidRevealedCreatureSnapshot", side_effect=swap):
            with self.assertRaises(AssertionError):
                self.test_a_copied_spell_keeps_the_sealed_payment_and_the_new_controller()
