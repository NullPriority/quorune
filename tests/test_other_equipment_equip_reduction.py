"""Native evidence for the distinct Equipment relation on a fixed equip reduction."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.activation_cost_modifiers import (
    ACTIVATION_GENERIC_COST_CAPABILITY, ACTIVATION_GENERIC_COST_HANDLER,
    OTHER_EQUIPMENT_ACTIVATION_PREDICATE, OTHER_EQUIPMENT_ACTIVATION_SUBJECT,
    FixedActivationCostModifierSpec,
)
from quorune.carddb import CardDatabase
from quorune.compiler import activation_cost_modifier_templates as templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


WHIP = "Bladehold War-Whip"
AXE = "Bonesplitter"
BEAR = "Grizzly Bears"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
CLAUSE = "Equip abilities you activate of other Equipment cost {1} less to activate."


class OtherEquipmentEquipReductionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "reconfigure-cards.json": {AXE, BEAR, "Lizard Blades", "Naturalize"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "other-equipment-equip-reduction-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert names == {row["name"] for row in document["cards"]}, name
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(document), encoding="utf-8")
            parts.append(part)
        path = folder / "other-equipment-equip-reduction.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Distinct Equipment price", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(WHIP, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, WHIP)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        return card, tuple(semantics.programs_for_oracle(card.oracle_id))

    def _actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def _activation_offer(self, game, equipment, seat="A"):
        row, = [action for action in self._actions(game, seat)
                if action["id"].startswith(f"activate:{equipment.ref}:")]
        return row

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _submit(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    @staticmethod
    def _payment(offer, creature, **mana):
        return {"action_id": offer["id"], "targets": [creature.ref], "pay": "manual", "payment": mana}

    def test_exact_relation_wire_rejects_other_ability_actor_and_cost_scopes(self):
        _, descriptor, cap = templates.static_activation_cost_modifier_handler(CLAUSE)
        self.assertEqual((ACTIVATION_GENERIC_COST_HANDLER, ACTIVATION_GENERIC_COST_CAPABILITY),
                         (descriptor["handler_id"], cap))
        raw = descriptor["modifier"]
        spec = FixedActivationCostModifierSpec.from_dict(raw)
        self.assertEqual(raw, spec.to_dict())
        self.assertEqual(OTHER_EQUIPMENT_ACTIVATION_SUBJECT, spec.subject_relation)
        self.assertEqual(OTHER_EQUIPMENT_ACTIVATION_PREDICATE, spec.predicate)
        self.assertEqual((-1, "equip", "source_controller"),
                         (spec.generic_adjustment, spec.ability_scope, spec.controller_relation))
        for key, value in (("ability_scope", "all"), ("controller_relation", "any"),
                           ("generic_adjustment", 1), ("minimum_mana", 1)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                FixedActivationCostModifierSpec.from_dict(dict(raw, **{key: value}))
        for material in (
            CLAUSE.replace("Equip", "Reconfigure"), CLAUSE.replace("other Equipment", "other artifacts"),
            CLAUSE.replace("less", "more"), CLAUSE.replace("you activate", "opponents activate"),
        ):
            with self.subTest(material=material):
                self.assertIsNone(templates.static_activation_cost_modifier_handler(material))

    def test_complete_original_keeps_entry_attached_keyword_and_ordinary_equip_siblings(self):
        card, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        modifier, = [p for p in programs if ACTIVATION_GENERIC_COST_CAPABILITY in p.capability_dependencies]
        handler, = modifier.handlers
        self.assertEqual("activation.mana_cost.modify", modifier.event)
        self.assertEqual(OTHER_EQUIPMENT_ACTIVATION_SUBJECT, handler["modifier"]["subject_relation"])
        self.assertTrue({"trigger.keyword.for_mirrodin", "combat.damage.participation.strike_steps",
                         "attachment.equip.fixed_mana"}.issubset(
            capability for program in programs for capability in program.capability_dependencies))

    def test_actual_whip_cast_retains_its_own_full_price_and_subsidizes_only_other_equipment(self):
        game = self.session(61692010)
        source = self.add(game.engine, WHIP, zone="hand")
        axe = self.add(game.engine, AXE)
        ally = self.add(game.engine, BEAR)
        remove = self.add(game.engine, "Naturalize", seat="B", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="whip-private")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 4, "R": 2, "W": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)
        cast, = [row for row in self._actions(game, "A") if row["id"] == f"cast:{source.ref}"]
        self._submit(game, "A", {"action_id": cast["id"], "pay": "manual", "payment": {"C": 1, "R": 1, "W": 1}})
        self.assertIsNone(self.resolve(game))
        rebel = game.state.cards[game.state.cards[source.object_id].attached_to]
        self.assertTrue(rebel.is_token)
        self.assertIn("rebel", game.engine._type_parts(game.engine._effective_card_data(rebel)["type_line"])[1])
        self.assertIn("double strike", game.engine._combat_keywords(rebel))

        own = self._activation_offer(game, source)
        self._reject(game, "A", self._payment(own, ally, C=2, R=1, W=1))
        self._submit(game, "A", self._payment(own, ally, C=3, R=1, W=1))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(ally.object_id, game.state.cards[source.object_id].attached_to)
        self.assertIn("double strike", game.engine._combat_keywords(ally))
        self.assertNotIn("double strike", game.engine._combat_keywords(rebel))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))

        other = self._activation_offer(game, axe)
        command = self._payment(other, ally)
        self._reject(game, "D", command)
        self._submit(game, "A", command)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(ally.object_id, game.state.cards[axe.object_id].attached_to)

        self.cast(game, "B", remove, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertNotIn(other["id"], {row["id"] for row in self._actions(game, "A")})
        self._reject(game, "A", self._payment(other, rebel))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def test_actual_distinct_printed_copies_reduce_each_other_and_leave_reconfigure_unchanged(self):
        game = self.session(61692020)
        first = self.add(game.engine, WHIP, ref="distinct-whip-one")
        second = self.add(game.engine, WHIP, ref="distinct-whip-two")
        blades = self.add(game.engine, "Lizard Blades")
        ally = self.add(game.engine, BEAR)
        self.prepare(game, mana={"C": 4, "R": 1, "W": 1})
        self.checkpoint(game)
        self.assertEqual(first.oracle_id, second.oracle_id)
        self.assertIsNone(game.state.cards[second.object_id].attached_to)
        quote = self._activation_offer(game, first)
        self._reject(game, "A", self._payment(quote, ally, C=1, R=1, W=1))
        self._reject(game, "A", self._payment(quote, ally, C=2, R=1))
        self._submit(game, "A", self._payment(quote, ally, C=2, R=1, W=1))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(ally.object_id, game.state.cards[first.object_id].attached_to)
        self.assertEqual(2, game.state.players["A"].mana_pool["C"])
        reconfigure = self._activation_offer(game, blades)
        self._reject(game, "A", self._payment(reconfigure, ally))
        self._submit(game, "A", self._payment(reconfigure, ally, C=2))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(ally.object_id, game.state.cards[blades.object_id].attached_to)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_actual_opposing_controller_cannot_use_the_discount(self):
        game = self.session(61692030)
        self.add(game.engine, WHIP)
        axe = self.add(game.engine, AXE, seat="B")
        ally = self.add(game.engine, BEAR, seat="B")
        self.prepare(game, actor="B", active="B", mana={"C": 1})
        self.checkpoint(game)
        offer = self._activation_offer(game, axe, "B")
        self._reject(game, "B", self._payment(offer, ally))
        self._submit(game, "B", self._payment(offer, ally, C=1))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(ally.object_id, game.state.cards[axe.object_id].attached_to)
        self.replay(game, load=True)

    def test_omitting_the_other_equipment_subject_restores_the_material_static_residual(self):
        existing = templates._modifier_subject

        def omit_other(head):
            if head.casefold() == "equip abilities you activate of other equipment":
                return None
            return existing(head)

        with patch.object(templates, "_modifier_subject", side_effect=omit_other):
            card = self.db.lookup(WHIP, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            residual, = ir.material_residuals
            self.assertEqual(CLAUSE, residual.text)
            report, _ = register_card(self.db, self.registry, WHIP)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, AXE)
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_static_equip_price_withdrawal_denies_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        value = deepcopy(raw)
        next(row for row in value["capabilities"] if row["id"] == ACTIVATION_GENERIC_COST_CAPABILITY).update(
            status="blocked", blockers=["Scoped static equip price authority withdrawal"])
        registry = CapabilityRegistry(value)
        index = build_index(registry_value=value, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((ACTIVATION_GENERIC_COST_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + ACTIVATION_GENERIC_COST_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, WHIP)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, AXE)
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
