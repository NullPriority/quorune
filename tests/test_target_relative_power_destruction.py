"""Actual target-relative destruction and ordinary casting and resolution legality."""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import destruction_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.destruction_regeneration_capability_shapes import mass_destruction_node_capabilities
from quorune.rules.qualified_destruction import (
    TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY, TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES,
    RelativePowerDestructionScope, RelativePowerDestructionSetSpec,
    relative_power_destruction_instruction, select_relative_power_destruction_rows,
)
from quorune.semantic_choices.destruction_intent_identity import (
    destruction_intent_identity, validate_destruction_intent_identity,
)
from quorune.semantic_runtime.intents import DestroyPermanentSetIntent
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FELL = "Fell the Mighty"
MANDATE = "Mandate of Abaddon"
ORIGINALS = {
    FELL: RelativePowerDestructionScope.GREATER,
    MANDATE: RelativePowerDestructionScope.LESS_THAN_CONTROLLED,
}
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TargetRelativePowerDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "attached-destruction-set-cards.json": {
                "Grizzly Bears", "Gladecover Scout", "Darksteel Myr", "Doomed Traveler", "Murder", "Sol Ring",
            },
            "activation-public-state-cards.json": {"Disfigure"},
            "damage-death-exile-cards.json": {"Wall of Omens"},
            "destruction-controller-tokens-cards.json": {"Fumigate"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Generic Bound Growth"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        parts = [FIXTURES / "target-relative-power-destruction-cards.json"]
        for name, names in support.items():
            document = json.loads((FIXTURES / name).read_bytes())
            document["cards"] = [card for card in document["cards"] if card["name"] in names]
            assert names == {card["name"] for card in document["cards"]}, name
            ids = {card["oracle_id"] for card in document["cards"]}
            document["rulings"] = [ruling for ruling in document.get("rulings", ()) if ruling["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(document), encoding="utf-8")
            parts.append(path)
        path = folder / "target-relative-power-destruction.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target-relative power destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_versioned_scope_uses_one_target_and_preserves_its_resolved_identity(self):
        for scope in RelativePowerDestructionScope:
            spec = RelativePowerDestructionSetSpec(scope, "$target.0")
            payload = {"op": "destroy_all", "source": "$source", "set": spec.to_dict()}
            mechanics = ("destroy", "destroy-fixed-set", "cr-115-targets")
            self.assertEqual(spec, relative_power_destruction_instruction(payload))
            self.assertEqual(TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES,
                             typed_result_amount_runtime_dependencies(payload))
            supported = mass_destruction_node_capabilities(
                effects=(payload,), target_schema=spec.target_schema(), mechanic_ids=mechanics,
            )
            self.assertEqual(TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES, supported)
            for bad in (None, {**spec.target_schema(), "count": True},
                        {**spec.target_schema(), "controller_relation": "opponent"}):
                self.assertEqual((), mass_destruction_node_capabilities(
                    effects=(payload,), target_schema=bad, mechanic_ids=mechanics,
                ))
            for bad in (
                dict(payload, regeneration_prohibited=True),
                dict(payload, set={**payload["set"], "schema_version": True}),
                dict(payload, set={**payload["set"], "target_ref": "$target.1"}),
            ):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    relative_power_destruction_instruction(bad)
            resolved = relative_power_destruction_instruction(
                dict(payload, source="spell", set={**payload["set"], "target_ref": "permanent"}), resolved=True,
            )
            kind, wire = destruction_intent_identity(DestroyPermanentSetIntent("A", resolved, "relative destruction"))
            self.assertEqual(wire, validate_destruction_intent_identity(kind, wire))
            self.assertEqual("permanent", wire["spec"]["target_ref"])

    def test_signed_thresholds_are_strict_and_mandate_checks_current_control(self):
        def row(ref, seat, power):
            return ObjectQueryResult(
                ref, ref, ref, seat, seat, "battlefield", types=("creature",),
                logical_object_id=ref + "-incarnation", effective_power=power,
            )

        rows = (row("anchor", "A", -2), row("same", "B", -2), row("above", "C", -1), row("below", "D", -3))
        greater = RelativePowerDestructionSetSpec(RelativePowerDestructionScope.GREATER, "anchor")
        less = RelativePowerDestructionSetSpec(RelativePowerDestructionScope.LESS_THAN_CONTROLLED, "anchor")
        self.assertEqual(["above"], [r.ref for r in select_relative_power_destruction_rows(rows, greater, actor="A")])
        self.assertEqual(["below"], [r.ref for r in select_relative_power_destruction_rows(rows, less, actor="A")])
        with self.assertRaises(ValueError):
            select_relative_power_destruction_rows(rows, less, actor="B")
        with self.assertRaises(ValueError):
            select_relative_power_destruction_rows(rows[1:], greater, actor="A")

    def test_complete_originals_bind_distinct_target_schemas_and_retain_ordinary_destruction(self):
        for name, scope in ORIGINALS.items():
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = semantics.programs_for_oracle(card.oracle_id)
                effect, = program.effects
                expected = RelativePowerDestructionSetSpec(scope, "$target.0")
                self.assertEqual(expected, relative_power_destruction_instruction(effect))
                self.assertEqual(expected.target_schema(), program.target_schema)
                self.assertLessEqual(set(TARGET_RELATIVE_POWER_DESTRUCTION_DEPENDENCIES), set(program.capability_dependencies))
        ordinary, _ = register_card(self.db, self.registry, "Fumigate")
        self.assertTrue(ordinary["strict_capability_ready"], ordinary["blockers"])

    def test_paid_fell_uses_the_current_negative_threshold_and_untargeted_destruction(self):
        game = self.session(6497120)
        spell = self.add(game.engine, FELL, zone="hand")
        anchor = self.add(game.engine, "Wall of Omens", seat="C")
        zero = self.add(game.engine, "Wall of Omens", seat="D", ref="greater-power-zero")
        bear = self.add(game.engine, "Grizzly Bears", seat="A")
        scout = self.add(game.engine, "Gladecover Scout", seat="B")
        knight = self.add(game.engine, "Black Knight", seat="B")
        indestructible = self.add(game.engine, "Darksteel Myr", seat="C")
        artifact = self.add(game.engine, "Sol Ring", seat="D")
        reduction = self.add(game.engine, "Disfigure", seat="D", zone="hand")
        private = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="relative-power-private")
        for card in (spell, scout, knight, indestructible, reduction):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4, "W": 1})
        game.state.players["D"].mana_pool["B"] = 1
        self.checkpoint(game)

        self.cast(game, "A", spell, [anchor.ref])
        self.assertEqual([anchor.ref], game.state.stack[-1].targets)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "D", reduction, [anchor.ref])
        self.resolve_top(game)
        self.assertEqual(-2, game.engine._numeric_stat(anchor.object_id, "power"))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (zero, bear, scout, knight):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for card in (anchor, indestructible, artifact):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        result, = [event for event in game.state.events if event.code == "effect.permanent.destroy_set"]
        self.assertEqual((5, 4), (result.details["affected_count"], result.details["destroyed_count"]))
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_paid_mandate_requires_your_creature_and_preserves_other_tied_creatures(self):
        game = self.session(6497130)
        spell = self.add(game.engine, MANDATE, zone="hand")
        anchor = self.add(game.engine, "Grizzly Bears", seat="A")
        tied = self.add(game.engine, "Grizzly Bears", seat="B", ref="below-power-tie")
        traveler = self.add(game.engine, "Doomed Traveler", seat="A")
        scout = self.add(game.engine, "Gladecover Scout", seat="C")
        zero = self.add(game.engine, "Wall of Omens", seat="D")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 3, "B": 1})
        self.checkpoint(game)

        action = {"action_id": "cast:" + spell.ref, "targets": [tied.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", action).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [anchor.ref])
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        for card in (traveler, scout, zero):
            self.assertEqual("graveyard", game.state.cards[card.object_id].zone)
        for card in (anchor, tied):
            self.assertEqual("battlefield", game.state.cards[card.object_id].zone)
        result, = [event for event in game.state.events if event.code == "effect.permanent.destroy_set"]
        self.assertEqual((3, 3), (result.details["affected_count"], result.details["destroyed_count"]))
        new, = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual("A", new.controller)
        self.assertIn("spirit", new.printed_name.casefold())
        self.replay(game, load=True)

    def test_no_offer_without_your_creature_and_illegal_anchor_prevents_any_destruction(self):
        empty = self.session(6497140)
        uncastable = self.add(empty.engine, MANDATE, zone="hand")
        foreign = self.add(empty.engine, "Grizzly Bears", seat="B")
        self.prepare(empty, mana={"C": 3, "B": 1})
        self.checkpoint(empty)
        actions = empty.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertNotIn("cast:" + uncastable.ref, {action["id"] for action in actions})
        before = authoritative_state_hash(empty.state)
        forbidden = empty.act("pilot:A", {"action_id": "cast:" + uncastable.ref, "targets": [foreign.ref], "pay": "auto"})
        self.assertFalse(forbidden.ok)
        self.assertEqual(before, authoritative_state_hash(empty.state))

        for index, (name, mana) in enumerate(((FELL, {"C": 4, "W": 1}), (MANDATE, {"C": 3, "B": 1}))):
            with self.subTest(name=name):
                game = self.session(6497150 + index)
                spell = self.add(game.engine, name, zone="hand")
                anchor = self.add(game.engine, "Grizzly Bears")
                zero = self.add(game.engine, "Wall of Omens", seat="C")
                positive = self.add(game.engine, "Black Knight", seat="D")
                removal = self.add(game.engine, "Murder", seat="B", zone="hand")
                self.prepare(game, mana=mana)
                game.state.players["B"].mana_pool.update({"C": 1, "B": 2})
                self.checkpoint(game)

                self.cast(game, "A", spell, [anchor.ref])
                self.cast(game, "B", removal, [anchor.ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[anchor.object_id].zone)
                self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[zero.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[positive.object_id].zone)
                self.assertFalse(any(event.code == "effect.permanent.destroy_set" for event in game.state.events))
                self.replay(game, load=True)

    def test_compiler_omission_restores_both_material_target_relative_clauses(self):
        with patch.object(destruction_templates, "_relative_power_destruction_template", return_value=None):
            for name in ORIGINALS:
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                residual, = ir.material_residuals
                self.assertEqual("spell_effect", residual.kind)
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_relative_power_authority_withdrawal_retains_fumigate(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        document = deepcopy(raw)
        next(row for row in document["capabilities"] if row["id"] == TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY).update(
            status="blocked", blockers=["Scoped target-relative destruction authority withdrawal"])
        registry = CapabilityRegistry(document)
        index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + TARGET_RELATIVE_POWER_DESTRUCTION_CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in ORIGINALS:
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        legacy, _ = register_card(self.db, registry, "Fumigate")
        self.assertTrue(legacy["strict_capability_ready"], legacy["blockers"])


if __name__ == "__main__":
    unittest.main()
