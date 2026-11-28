from __future__ import annotations

"""CR 608.2b-c/h, 611.2c, 614.15: choose one current-target result."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs.binding import bind_card_program_runtime
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
import test_attachment_followup_bindings as attachment_controls
import test_bound_effect_programs as witnesses


ADMITTED = ("Flare of Faith", "Regent's Authority", "Will of the All-Hunter",
            "Ray of Enfeeblement", "Olivia's Midnight Ambush", "Might of Old Krosa", "Ram Through")
EXCLUDED = ("Joint Assault", "Bleed Dry", "Divine Smite")


class CurrentTargetReplacementCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, registry=None):
        return compile_oracle_card(replace(witnesses.record(text), name="Current Trait Witness"),
            capability_registry=registry or self.registry, capability_profile="commander_review")

    def test_exact_alternatives_keep_one_unqualified_creature_target(self):
        for condition, result in (
            ("it's a Human", "instead it gets +3/+3 and gains indestructible until end of turn"),
            ("it's an enchantment creature or legendary creature", "instead put a +1/+1 counter on it and it gets +1/+1 until end of turn"),
            ("it's blocking", "instead put two +1/+1 counters on it"),
            ("that creature is white", "it gets -4/-4 until end of turn instead"),
            ("it's night", "that creature gets -13/-13 until end of turn instead"),
        ):
            text = f"Target creature gets +2/+2 until end of turn. If {condition}, {result}."
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                effect, = node.effects
                self.assertEqual(17, effect["schema_version"])
                self.assertEqual("$target.0", effect["condition_target"])
                self.assertEqual(node.target_schema, effect["target_binding"])
                self.assertEqual(["creature"], node.target_schema["types_any"])
                self.assertNotIn("colors_any", node.target_schema)
                self.assertNotIn("subtypes_any", node.target_schema)
                self.assertEqual(1, len(effect["otherwise_effects"]))
                self.assertEqual(2, effect["otherwise_effects"][0]["power"])
                self.assertEqual(text[node.span.start:node.span.end], node.text)

    def test_different_referents_unowned_conditions_and_new_targets_are_rejected(self):
        from quorune.compiler.target_replacements import current_target_replacement_template
        from quorune.oracle_ir import _reviewed_atomic_effect_template
        for tail in (
            "If it's paired with a creature, that creature gets +3/+3 until end of turn instead.",
            "If you cast this spell during your main phase, it gets +4/+4 until end of turn instead.",
            "If it's a Human, target creature gets +3/+3 until end of turn instead.",
            "If it's a Human, you may put a +1/+1 counter on it instead.",
            "If it's a Human, double its power instead.",
        ):
            text = "Target creature gets +2/+2 until end of turn. " + tail
            with self.subTest(text=text):
                self.assertIsNone(current_target_replacement_template(text,
                    compile_component=lambda body: _reviewed_atomic_effect_template(body, card_name="Witness")))

    def test_instruction_validation_requires_exact_binding_and_both_owned_branches(self):
        from copy import deepcopy
        from quorune.resolution_conditions import validate_resolution_condition_instruction
        from quorune.rules.target_replacements import current_target_replacement_capabilities
        text = "Target creature gets +2/+2 until end of turn. If it's a Human, instead it gets +3/+3 and gains indestructible until end of turn."
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        for field, value in (("condition_target", "$target.1"), ("prefix_mechanic_ids", ["unowned"]),
                             ("otherwise_effects", []), ("player", 4)):
            bad = deepcopy(node.effects[0]);bad[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_resolution_condition_instruction(bad)
        altered = deepcopy(node.target_schema);altered["controller_relation"] = "you"
        self.assertFalse(current_target_replacement_capabilities(effects=node.effects,
            target_schema=altered, mechanic_ids=node.mechanics))
        bad = deepcopy(node.effects[0]);bad["effects"][0]["op"] = "unowned"
        with self.assertRaises(ValueError):validate_resolution_condition_instruction(bad)

    def test_compiler_and_owner_omission_are_detected(self):
        text = "Target creature gets +2/+2 until end of turn. If it's a Human, instead it gets +3/+3 and gains indestructible until end of turn."
        self.assertEqual("exact", self.compile(text).status)
        with patch("quorune.compiler.target_replacements.current_target_replacement_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)
        with patch("quorune.rules.target_replacements.current_target_replacement_capabilities", return_value=()):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_capability_withdrawals_preserve_fail_closed_admission(self):
        from quorune.rules.capabilities import CapabilityRegistry
        text = "Target creature gets +2/+2 until end of turn. If it's a Human, instead it gets +3/+3 and gains indestructible until end of turn."
        self.assertEqual("exact", self.compile(text).status)
        for missing in ("resolution.effect.current_target_self_replacement", "resolution.effect.public_condition",
                        "target.revalidate_resolution", "continuous.resolution.fixed_characteristics_until_end_of_turn"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            next(row for row in raw["capabilities"] if row["id"] == missing).update(status="blocked", blockers=["owner withdrawn"])
            registry = CapabilityRegistry(raw);registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            with self.subTest(missing=missing):self.assertNotEqual("exact", self.compile(text, registry).status)


class CurrentTargetReplacementRegistrationTests(unittest.TestCase):
    def test_complete_original_sources_and_excluded_neighbors(self):
        registry = load_default_capability_registry();semantics = SemanticRegistry()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "complete-source.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/current-target-replacement-cards.json"], path)
            with CardDatabase(path) as db:
                for name in (*ADMITTED, *EXCLUDED):
                    with self.subTest(name=name):
                        card = db.lookup(name, fuzzy=False)
                        register_generated_programs(db, semantics, (card,), trust_level="provisional",
                            capability_registry=registry, capability_profile="commander_review",
                            promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True,
                            promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
                        program = compile_best_available_card_program(db, card, semantic_registry=semantics,
                            capability_registry=registry, capability_profile="commander_review")
                        bound = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                        self.assertEqual(name in ADMITTED, bool(bound["strict_capability_ready"]
                            and bound["trust_basis"] == "capability_closed"), bound["blockers"])


class CurrentTargetReplacementRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = attachment_controls.AttachmentFollowupRuntimeTests.session
    add = attachment_controls.AttachmentFollowupRuntimeTests.add
    prepare = attachment_controls.AttachmentFollowupRuntimeTests.prepare
    closed = attachment_controls.AttachmentFollowupRuntimeTests.closed
    actions = attachment_controls.AttachmentFollowupRuntimeTests.actions
    cast_offered = attachment_controls.AttachmentFollowupRuntimeTests.cast_offered
    checkpoint = attachment_controls.AttachmentFollowupRuntimeTests.checkpoint
    resolve = attachment_controls.AttachmentFollowupRuntimeTests.resolve
    replay = attachment_controls.AttachmentFollowupRuntimeTests.replay
    advance = attachment_controls.AttachmentFollowupRuntimeTests.advance
    until = attachment_controls.AttachmentFollowupRuntimeTests.until

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-replacements.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/current-target-replacement-cards.json"], path)
        cls.db = CardDatabase(path);cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current replacement witnesses", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close();cls.temporary.cleanup()

    def setup_spell(self, seed, name, recipient, *, controls=(), response=None, night=None):
        session = self.session(seed)
        spell = self.add(session.engine, name, zone="hand")
        target = self.add(session.engine, recipient, ref="replacement-target")
        others = [self.add(session.engine, control, ref=f"replacement-control-{i}")
                  for i, control in enumerate(controls)]
        reply = self.add(session.engine, response, zone="hand") if response else None
        session.state.day_night = night
        self.prepare(session, spell, {"C": 12, "W": 8, "U": 8, "B": 8, "G": 8})
        self.checkpoint(session)
        return session, spell, target, others, reply

    def test_original_alternatives_preserve_true_false_and_single_result(self):
        cases = (
            ("Flare of Faith", "Generic Conditional Human", 5, 7, 0, True),
            ("Flare of Faith", "Generic Conditional Construct", 4, 6, 0, False),
            ("Regent's Authority", "Generic Replacement Legendary Enchantment", 4, 6, 1, False),
            ("Regent's Authority", "Generic Replacement Legendary", 4, 6, 1, False),
            ("Regent's Authority", "Generic Replacement Enchantment", 4, 6, 1, False),
            ("Regent's Authority", "Generic Conditional Human", 4, 6, 0, False),
            ("Will of the All-Hunter", "Generic Conditional Human", 4, 6, 0, False),
            ("Ray of Enfeeblement", "Generic Replacement White", 2, 4, 0, False),
            ("Ray of Enfeeblement", "Generic Conditional Human", -2, 3, 0, False),
        )
        for index, (name, recipient, power, toughness, counters, indestructible) in enumerate(cases):
            with self.subTest(name=name, recipient=recipient):
                session, spell, target, _, _ = self.setup_spell(294100 + index, name, recipient)
                action = next(row for row in self.actions(session) if row["id"] == "cast:" + spell.ref)
                self.assertIn(target.ref, str(action["target_schema"]))
                before = authoritative_state_hash(session.state)
                wrong = session.act("pilot:B", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
                self.assertFalse(wrong.ok);self.assertEqual(before, authoritative_state_hash(session.state))
                self.cast_offered(session, spell, targets=(target.ref,))
                session = self.replay(session, load=True)
                self.assertIsNone(self.resolve(session))
                current = session.state.cards[target.object_id]
                self.assertEqual(power, session.engine._numeric_stat(current.object_id, "power"))
                self.assertEqual(toughness, session.engine._numeric_stat(current.object_id, "toughness"))
                self.assertEqual(counters, current.counters.get("+1/+1", 0))
                self.assertEqual(indestructible, "indestructible" in session.engine._combat_keywords(current))
                self.replay(session, load=True)

    def change_type(self, session, source, target, chosen):
        self.closed(session, source)
        action = next(row for row in self.actions(session) if row["id"].startswith("activate:" + source.ref + ":"))
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [target.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        result = session.act("pilot:A", {"action_id": "choose", "creature_type": chosen})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        return session

    def test_current_traits_are_read_after_real_type_change_and_then_locked(self):
        session, spell, target, controls, _ = self.setup_spell(294120, "Flare of Faith", "Generic Conditional Construct",
            controls=("Imagecrafter", "Imagecrafter"))
        self.cast_offered(session, spell, targets=(target.ref,))
        session = self.change_type(session, controls[0], target, "human")
        self.assertEqual(5, session.engine._numeric_stat(target.object_id, "power"))
        self.assertIn("indestructible", session.engine._combat_keywords(session.state.cards[target.object_id]))
        session = self.change_type(session, controls[1], target, "elf")
        self.assertEqual(5, session.engine._numeric_stat(target.object_id, "power"))
        self.assertIn("indestructible", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.advance(session, lambda current: current.state.active_player != "A")
        self.assertEqual(2, session.engine._numeric_stat(target.object_id, "power"))
        self.assertNotIn("indestructible", session.engine._combat_keywords(session.state.cards[target.object_id]))
        self.replay(session, load=True)

    def test_regent_counter_precedes_stat_and_survives_expiry_with_prohibition(self):
        for index, prohibited in enumerate((False, True)):
            with self.subTest(prohibited=prohibited):
                session, spell, target, _, _ = self.setup_spell(294130 + index, "Regent's Authority",
                    "Generic Replacement Legendary Enchantment", controls=("Solemnity",) if prohibited else ())
                self.cast_offered(session, spell, targets=(target.ref,))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(0 if prohibited else 1, session.state.cards[target.object_id].counters.get("+1/+1", 0))
                self.assertEqual(3 if prohibited else 4, session.engine._numeric_stat(target.object_id, "power"))
                self.advance(session, lambda current: current.state.active_player != "A")
                self.assertEqual(2 if prohibited else 3, session.engine._numeric_stat(target.object_id, "power"))
                self.replay(session, load=True)

    def test_night_designation_selects_one_result_without_changing_designation(self):
        for index, designation in enumerate((None, "day", "night")):
            with self.subTest(designation=designation):
                session, spell, target, _, _ = self.setup_spell(294140 + index, "Olivia's Midnight Ambush",
                    "Generic Conditional Human", night=designation)
                self.cast_offered(session, spell, targets=(target.ref,))
                self.assertIsNone(self.resolve(session))
                self.assertEqual(designation, session.state.day_night)
                current = session.state.cards[target.object_id]
                if designation == "night":self.assertEqual("graveyard", current.zone)
                else:
                    self.assertEqual("battlefield", current.zone)
                    self.assertEqual(0, session.engine._numeric_stat(target.object_id, "power"))
                    self.assertEqual(2, session.engine._numeric_stat(target.object_id, "toughness"))
                self.replay(session, load=True)

    def test_departed_target_executes_neither_branch_and_replays(self):
        session, spell, target, _, blink = self.setup_spell(294150, "Flare of Faith", "Generic Conditional Human",
            response="Cloudshift")
        original_incarnation = target.logical_object_id
        self.cast_offered(session, spell, targets=(target.ref,))
        self.cast_offered(session, blink, targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        current = session.state.cards[target.object_id]
        self.assertNotEqual(original_incarnation, current.logical_object_id)
        self.assertEqual(2, session.engine._numeric_stat(current.object_id, "power"))
        self.assertNotIn("indestructible", session.engine._combat_keywords(current))
        self.replay(session, load=True)

    def test_runtime_branch_omission_mutant_is_killed(self):
        session, spell, target, _, _ = self.setup_spell(294160, "Flare of Faith", "Generic Conditional Construct")
        with patch("quorune.semantic_choices.target_replacements.current_target_replacement_matches", return_value=True):
            self.cast_offered(session, spell, targets=(target.ref,))
            self.assertIsNone(self.resolve(session))
            with self.assertRaises(AssertionError):
                self.assertEqual(4, session.engine._numeric_stat(target.object_id, "power"))

    def test_blocking_uses_actual_declarations_after_attacker_leaves_and_cycling_remains(self):
        session = self.session(294170)
        spell = self.add(session.engine, "Will of the All-Hunter", zone="hand")
        cycling = self.add(session.engine, "Will of the All-Hunter", zone="hand", ref="cycling-sibling")
        bounce = self.add(session.engine, "Unsummon", zone="hand")
        attacker = self.add(session.engine, "Universal Automaton", ref="replacement-attacker")
        blocker = self.add(session.engine, "Generic Conditional Human", seat="B", ref="replacement-blocker")
        for index in range(4):
            self.add(session.engine, "Generic Bound Plains", ref=f"replacement-plains-{index}")
            self.add(session.engine, "Island", ref=f"replacement-island-{index}")
        self.prepare(session, spell, {})
        self.closed(session, bounce)
        self.checkpoint(session)
        self.advance(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attacks": {attacker.ref: "B"}})
        self.assertTrue(result.ok, result.summary)
        self.advance(session, lambda current: current.state.pending_decision is not None
            and current.state.pending_decision.kind == "combat.blockers")
        result = session.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(result.ok, result.summary)
        self.cast_offered(session, bounce, targets=(attacker.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[attacker.object_id].zone)
        self.cast_offered(session, spell, targets=(blocker.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.cards[blocker.object_id].counters.get("+1/+1", 0))
        self.assertEqual(4, session.engine._numeric_stat(blocker.object_id, "power"))
        before = len(session.state.players["A"].draw_history)
        action = next(row for row in self.actions(session) if row["id"].startswith("activate:" + cycling.ref + ":"))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[cycling.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before + 1, len(session.state.players["A"].draw_history))
        self.advance(session, lambda current: current.state.phase == "postcombat_main")
        self.assertEqual(2, session.state.cards[blocker.object_id].counters.get("+1/+1", 0))
        self.replay(session, load=True)

    def test_payload_minimum_rejects_missing_owner_without_coverage(self):
        from dataclasses import replace
        from quorune.card_programs.binding import bind_semantic_program_runtime
        session, spell, _, _, _ = self.setup_spell(294180, "Flare of Faith", "Generic Conditional Human")
        program = next(p for p in session.engine.semantics.programs_for_oracle(spell.oracle_id)
                       if "resolution.effect.current_target_self_replacement" in p.capability_dependencies)
        for missing in ("resolution.effect.current_target_self_replacement", "target.revalidate_resolution"):
            dependencies = tuple(c for c in program.capability_dependencies if c != missing)
            altered = replace(program, coverage=(), capability_dependencies=dependencies,
                capability_closure=self.registry.closure(dependencies, profile="commander_review").to_dict())
            bound = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile="commander_review")
            self.assertFalse(bound["strict"])
            self.assertIn("capability:undeclared_runtime_dependency:" + missing, bound["blockers"])

    def test_public_trait_branch_does_not_expose_opponent_hidden_cards(self):
        session = self.session(294190)
        spell = self.add(session.engine, "Flare of Faith", zone="hand")
        target = self.add(session.engine, "Generic Conditional Human")
        private = self.add(session.engine, "Generic Conditional Construct", seat="B", zone="hand", ref="hidden-replacement-control")
        self.prepare(session, spell, {"W": 2})
        self.checkpoint(session)
        for principal in ("pilot:A", "pilot:C", "pilot:D"):
            packet = str(session.packet(principal, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.cast_offered(session, spell, targets=(target.ref,))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, session.engine._numeric_stat(target.object_id, "power"))
        for principal in ("pilot:A", "pilot:C", "pilot:D"):
            self.assertNotIn(private.ref, str(session.packet(principal, full=True)))
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
