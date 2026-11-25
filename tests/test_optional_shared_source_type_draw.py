"""The printed self-entry and attack triggers draw for current shared creature types."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import (
    SOURCE_SHARED_TYPE_AMOUNT_CAPABILITY as CAPABILITY, PublicQueryAmountSpec, source_shared_type_amount_spec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.optional_draw_discard import validate_source_shared_type_draw_discard
from quorune.target_source import TARGETING_SOURCE_CONTEXT, TargetingSourceBinding
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


TITAN = "Titan of Littjara"
KOI = "Skyswimmer Koi"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class OptionalSharedSourceTypeDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "material-sibling-admission-cards.json": {TITAN},
            "attached-destruction-set-cards.json": {"Fervor", "Grizzly Bears", "Island", "Unsummon"},
            "creature-subtype-addition-cards.json": {"Boggart Shenanigans", "Goblin Piker", "Isamaru, Hound of Konda"},
            "characteristic-query-union-cards.json": {"Universal Automaton"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "optional-draw-discard-sequences.json": {KOI},
            "entry-designation-cards.json": {"Adaptive Automaton"},
            "draw-rules-cards.json": {"Spirit of the Labyrinth"},
            "fixed-direct-phasing-cards.json": {"Reality Ripple"},
            "conditional-target-characteristic-cards.json": {"Imagecrafter"},
        }
        parts = []
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, source
            ids = {card["oracle_id"] for card in doc["cards"]}
            doc["rulings"] = [r for r in doc.get("rulings", ()) if r["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps(doc), encoding="utf-8")
            parts.append(part)
        path = folder / "optional-shared-source-type-draw.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Shared source type draw", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_has_only_the_exact_versioned_trigger_result(self):
        card = self.db.lookup(TITAN, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, semantics = register_card(self.db, self.registry, TITAN)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies)
        node, = (node for node in ir.faces[0].nodes if CAPABILITY in node.capability_dependencies)
        self.assertEqual("permanent.enter", node.event)
        self.assertIsNone(program.target_schema)
        effect, = node.effects
        self.assertEqual(source_shared_type_amount_spec(), PublicQueryAmountSpec.from_dict(effect["effects"][0]["count"]))
        self.assertIn(CAPABILITY, typed_result_amount_runtime_dependencies(effect))
        self.assertIn("choice.affected_player.fixed_controller_discard", validate_source_shared_type_draw_discard(effect))
        for updates in (
            {"coefficient": 2}, {"schema_version": 1}, {"binding_id": "later"},
        ):
            changed = deepcopy(effect)
            changed["effects"][0]["count"].update(updates)
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_source_shared_type_draw_discard(changed)
        for before, after in (
            ("each other creature", "each creature"),
            ("creature you control", "creature an opponent controls"),
            ("enters or attacks", "attacks"),
            ("discard a card.", "discard a card at random."),
            ("discard a card.", "discard a card. You gain 1 life."),
        ):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            with self.subTest(after=after):
                ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _start(self, game, titan):
        self.assert_closed(game, titan)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 4, "U": 2})
        self.checkpoint(game)

    def _choose(self, game, command):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:D", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _enter(self, game, titan):
        self.cast(game, "A", titan)
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        goblin, = (row["id"] for row in options if row["id"].endswith(":goblin"))
        self._choose(game, {"action_id": "choose", "replacement": goblin})
        current = game.state.cards[titan.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual({"goblin", "illusion"}, game.engine._effective_type_parts(current)[1])
        self.assertEqual(1, self.trigger_count(game, titan))

    def _assert_optional_offer(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
        action, = (a for a in pending.payload_by_actor["A"]["legal_actions"] if a["id"] == "choose")
        self.assertEqual(["apply", "decline"], list(action["choice_schema"]["legal_values"]))

    def _apply_and_discard(self, game, *, wanted_draws):
        hand = set(game.state.players["A"].zones["hand"])
        library = game.state.players["A"].zones["library"]
        top = set(library[-wanted_draws:]) if wanted_draws else set()
        self._choose(game, {"action_id": "choose", "choice": "apply"})
        current = set(game.state.players["A"].zones["hand"])
        self.assertEqual(hand | top, current)
        self.assertEqual(("choice.apnap", ["A"]), (game.state.pending_decision.kind, game.state.pending_decision.actors))
        chosen = game.state.cards[next(iter(sorted(top or hand)))]
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)
            self.assertIsNone(packet["decision"])
            self.assertNotIn(chosen.ref, json.dumps(packet))
        game = self.replay(game, load=True)
        self._choose(game, {"action_id": "choose", "cards": [chosen.ref]})
        self.assertEqual("graveyard", game.state.cards[chosen.object_id].zone)
        self.assertEqual(current - {chosen.object_id}, set(game.state.players["A"].zones["hand"]))
        self.replay(game, load=True)

    def test_actual_entry_draws_each_other_controlled_creature_once_then_discards_privately(self):
        game = self.session(61562100)
        titan = self.add(game.engine, TITAN, zone="hand")
        self.add(game.engine, "Goblin Piker")
        self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Grizzly Bears")
        self.add(game.engine, "Boggart Shenanigans")
        self.add(game.engine, "Universal Automaton", seat="C", ref="foreign-titan-shared")
        self._start(game, titan)
        self._enter(game, titan)
        self._assert_optional_offer(game)
        game = self.replay(game, load=True)
        self._apply_and_discard(game, wanted_draws=2)

    def test_actual_attack_uses_departed_source_types_and_current_controlled_creatures(self):
        game = self.session(61562101)
        titan = self.add(game.engine, TITAN, zone="hand")
        self.add(game.engine, "Goblin Piker")
        changeling = self.add(game.engine, "Universal Automaton")
        self.add(game.engine, "Fervor")
        first = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="bounce-titan")
        second = self.add(game.engine, "Unsummon", seat="B", zone="hand", ref="bounce-peer")
        self.add(game.engine, "Island", seat="B", ref="titan-bounce-island")
        self.add(game.engine, "Island", seat="B", ref="titan-peer-island")
        self._start(game, titan)
        self._enter(game, titan)
        self._assert_optional_offer(game)
        before = tuple(game.state.players["A"].zones["hand"])
        self._choose(game, {"action_id": "choose", "choice": "decline"})
        self.assertEqual(before, tuple(game.state.players["A"].zones["hand"]))
        self.assertEqual("priority", game.state.pending_decision.kind)

        self.attack(game, [(titan, "C")])
        self.assertEqual(1, self.trigger_count(game, titan))
        self.cast(game, "B", first, [titan.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[titan.object_id].zone)
        self.cast(game, "B", second, [changeling.ref])
        self.resolve_top(game)
        game = self.replay(game, load=True)
        self._assert_optional_offer(game)
        self._apply_and_discard(game, wanted_draws=1)

    def test_actual_phase_out_pins_the_types_immediately_before_the_status_change(self):
        game = self.session(61562104)
        titan = self.add(game.engine, TITAN, zone="hand")
        self.add(game.engine, "Goblin Piker")
        self.add(game.engine, "Grizzly Bears", ref="titan-bear-first")
        self.add(game.engine, "Grizzly Bears", ref="titan-bear-second")
        crafter = self.add(game.engine, "Imagecrafter")
        crafter.acquired_control_turn_count = -1
        ripple = self.add(game.engine, "Reality Ripple", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="titan-phase-island-first")
        self.add(game.engine, "Island", seat="B", ref="titan-phase-island-second")
        self.assert_closed(game, crafter)
        self.assert_closed(game, ripple)
        self._start(game, titan)
        self._enter(game, titan)

        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = (a for a in actions if a["id"].startswith("activate:" + crafter.ref + ":"))
        self._choose(game, {"action_id": activation["id"], "targets": [titan.ref], "pay": "auto"})
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._choose(game, {"action_id": "choose", "creature_type": "bear"})
        self.assertEqual({"bear"}, game.engine._effective_type_parts(game.state.cards[titan.object_id])[1])
        trigger, = game.state.stack
        before = TargetingSourceBinding.from_dict(trigger.context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual({"illusion", "goblin"}, set(before.facts().subtypes))

        self.cast(game, "B", ripple, [titan.ref])
        self.resolve_top(game)
        current = game.state.cards[titan.object_id]
        self.assertTrue(current.phased_out)
        self.assertEqual(before.logical_object_id, current.logical_object_id)
        trigger, = game.state.stack
        retained = TargetingSourceBinding.from_dict(trigger.context[TARGETING_SOURCE_CONTEXT])
        self.assertEqual({"bear"}, set(retained.facts().subtypes))
        game = self.replay(game, load=True)
        self._assert_optional_offer(game)
        self._apply_and_discard(game, wanted_draws=2)

    def test_actual_zero_draw_acceptance_still_discards_and_prohibited_draw_has_no_offer(self):
        game = self.session(61562102)
        titan = self.add(game.engine, TITAN, zone="hand")
        self.add(game.engine, "Goblin Piker", seat="B")
        self._start(game, titan)
        self._enter(game, titan)
        self._assert_optional_offer(game)
        self._apply_and_discard(game, wanted_draws=0)

        game = self.session(61562103)
        titan = self.add(game.engine, TITAN, zone="hand")
        self.add(game.engine, "Goblin Piker")
        self.add(game.engine, "Universal Automaton")
        spirit = self.add(game.engine, "Spirit of the Labyrinth", seat="B")
        self.assert_closed(game, spirit)
        self._start(game, titan)
        self._enter(game, titan)
        hand = tuple(game.state.players["A"].zones["hand"])
        library = tuple(game.state.players["A"].zones["library"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand, tuple(game.state.players["A"].zones["hand"]))
        self.assertEqual(library, tuple(game.state.players["A"].zones["library"]))
        self.replay(game, load=True)

    def test_missing_exact_compiler_branch_keeps_the_material_sibling(self):
        with patch("quorune.compiler.optional_effect_templates.optional_shared_source_type_trigger_result", return_value=None):
            card = self.db.lookup(TITAN, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(any("shares a creature type with it" in r.text for r in ir.material_residuals))
            report, _ = register_card(self.db, self.registry, TITAN)
            self.assertFalse(report["strict_capability_ready"])

    def test_genuine_quantity_withdrawal_retains_fixed_optional_draw_and_linked_type(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_doc = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in registry_doc["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped optional shared source quantity withdrawal"],
        )
        registry = CapabilityRegistry(registry_doc)
        index = build_index(registry_value=registry_doc, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        blockers = registry.closure((CAPABILITY,), profile=PROFILE).blockers
        self.assertIn("status:" + CAPABILITY + ":blocked", blockers)
        self.assertFalse(any("unverified" in blocker for blocker in blockers))
        report, _ = register_card(self.db, registry, TITAN)
        self.assertFalse(report["strict_capability_ready"], report)
        for control in (KOI, "Adaptive Automaton"):
            report, _ = register_card(self.db, registry, control)
            self.assertTrue(report["strict_capability_ready"], (control, report["blockers"]))


if __name__ == "__main__":
    unittest.main()
