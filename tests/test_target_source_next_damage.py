"""Pinned CR 615.8: life on resolution and one subsequent damage instance."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.prevention_component_shapes import (
    TARGET_SOURCE_NEXT_DAMAGE_CAPABILITY as CAPABILITY,
    TARGET_SOURCE_NEXT_DAMAGE_FIELD,
    target_source_next_damage_effects,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Dazzling Reflection"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"
NEXT_INSTANCE_BATCH_BLOCKER = "CR 615.8: source next-instance shields consume after the first recipient of one simultaneous damage batch"


class TargetSourceNextDamageTests(InheritedNativeFixture, unittest.TestCase):
    def test_versioned_source_batch_preserves_legacy_wire_and_proves_exact_aftermath(self):
        from types import SimpleNamespace
        from quorune.compiler.prevention_templates import fixed_prevention_effect_template
        from quorune.damage_modifier_state import (
            DamageModifierError, DamageModifierDuration, DamagePreventionShield, DamageSubject,
            GainLifePreventionAftermath, PreventionMode, SOURCE_NEXT_INSTANCE_BATCH_FIELD,
        )
        from quorune.damage_prevention import _shield_replacement_effect
        from quorune.damage_prevention_creation import pin_chosen_damage_source
        from quorune.object_predicate import ObjectQuerySpec
        from quorune.replacement import PreventAmount, PreventUsingShield
        from quorune.rules.prevention_component_shapes import target_source_next_damage_runtime_dependencies

        text = (
            "The next time target creature would deal damage this turn, prevent that damage. "
            "You gain life equal to the damage prevented this way."
        )
        template, effects, target_schema, _ = fixed_prevention_effect_template(text, card_name="Fixture Shield")
        effect, = effects
        self.assertEqual(1, effect[SOURCE_NEXT_INSTANCE_BATCH_FIELD])
        data = dict(event="resolve", active_zone="stack", target_schema=target_schema, cost_schema=None,
                    handlers=(), provenance={"template_id": template}, effects=effects, capability_dependencies=())
        owners = target_source_next_damage_runtime_dependencies(SimpleNamespace(**data))
        self.assertEqual({"damage.prevention.persistent_amount", "target.revalidate_resolution", "life.change.effect"}, set(owners))
        altered = (
            {key:value for key,value in effect.items() if key != SOURCE_NEXT_INSTANCE_BATCH_FIELD},
            {**effect, SOURCE_NEXT_INSTANCE_BATCH_FIELD: True},
            {**effect, "aftermath": [{**effect["aftermath"][0], "per_prevented": 2}]},
        )
        for changed in altered:
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                target_source_next_damage_runtime_dependencies(SimpleNamespace(**{**data, "effects": (changed,)}))
        with patch("quorune.compiler.prevention_templates._target_source_next_damage_life_aftermath", return_value=None):
            self.assertIsNone(fixed_prevention_effect_template(text, card_name="Fixture Shield"))

        game = self.session(615080104)
        demon = self.add(game.engine, "Pestilence Demon", seat="B")
        source = pin_chosen_damage_source(
            game.engine, source_ref=demon.ref, controller="A",
            predicate=ObjectQuerySpec(zones=("battlefield",), known_to_actor=True),
        )
        legacy = DamagePreventionShield(
            shield_id="legacy-next", source_id="fixture-shield", controller="A",
            subject=DamageSubject("*", "any", "A"), mode=PreventionMode.NEXT_INSTANCE, remaining=None,
            duration=DamageModifierDuration.UNTIL_END_OF_TURN, created_turn_sequence=game.state.turn_sequence,
            chosen_source=source, aftermath=(GainLifePreventionAftermath("A", per_prevented=1),),
        )
        self.assertNotIn(SOURCE_NEXT_INSTANCE_BATCH_FIELD, legacy.to_dict())
        self.assertEqual(legacy, DamagePreventionShield.from_dict(legacy.to_dict()))
        self.assertIsInstance(_shield_replacement_effect(legacy).operations[0], PreventUsingShield)
        marked = replace(legacy, source_next_instance_batch_version=1)
        encoded = marked.to_dict()
        self.assertEqual(marked, DamagePreventionShield.from_dict(encoded))
        self.assertIsInstance(_shield_replacement_effect(marked).operations[0], PreventAmount)
        for marker in (None, True, 0, 2):
            with self.subTest(marker=marker), self.assertRaises(DamageModifierError):
                DamagePreventionShield.from_dict({**encoded, SOURCE_NEXT_INSTANCE_BATCH_FIELD: marker})
        with self.assertRaises(DamageModifierError):
            replace(marked, mode=PreventionMode.ALL)

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        basic = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        basic["cards"] = [row for row in basic["cards"] if row["name"] == "Generic Bound Plains"]
        basic["rulings"] = []
        base = folder / "base.json"
        base.write_text(json.dumps(basic), encoding="utf-8")
        path = folder / "dazzling-reflection.sqlite3"
        build_fixture_database([base, FIXTURES / "target-source-next-damage-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Target source next damage", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def require_resolved_batch_rule(self):
        entry = self.registry.capability(CAPABILITY)
        if entry["status"] == "blocked" and NEXT_INSTANCE_BATCH_BLOCKER in entry["blockers"]:
            self.skipTest("Pending canonical multi-recipient next-instance prevention")

    def original(self):
        self.require_resolved_batch_rule()
        card = self.db.lookup(NAME, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id) if p.event == "resolve")
        return card, program

    def test_complete_original_seals_one_creature_for_ordered_life_and_prevention(self):
        card, program = self.original()
        self.assertEqual(1, program.target_schema["count"])
        self.assertEqual(("creature",), tuple(program.target_schema["types_any"]))
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual(target_source_next_damage_effects(), tuple(program.effects))
        for text in (
            card.oracle_text.replace("target creature's power", "target creature's toughness"),
            card.oracle_text.replace("would deal damage", "would be dealt damage"),
            card.oracle_text.replace("prevent that damage", "prevent the next 2 damage"),
            card.oracle_text + " Draw a card.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(card, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertFalse(any(CAPABILITY in n.capability_dependencies for f in ir.faces for n in f.nodes))

    def test_raw_program_requires_exact_order_source_mode_and_capability(self):
        _, program = self.original()
        life, shield = program.effects
        for effects in (
            (shield, life),
            (life, {**shield, "mode": "all"}),
            (life, {**shield, "chosen_source": "$source"}),
            (life, {**shield, "subject": "$controller"}),
            (life, {**shield, TARGET_SOURCE_NEXT_DAMAGE_FIELD: True}),
            (life, {field: value for field, value in shield.items() if field != TARGET_SOURCE_NEXT_DAMAGE_FIELD}),
        ):
            with self.subTest(effects=effects):
                result = bind_semantic_program_runtime(replace(program, effects=effects),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(result["strict"], result)
        dependencies = [cap for cap in program.capability_dependencies if cap != CAPABILITY]
        changed = replace(program, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def activate(self, game, source, *, seat="B", targets=()):
        self.priority(game, seat)
        current = game.state.cards[source.object_id]
        options = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in options if row["id"].startswith(f"activate:{current.ref}:")]
        result = game.act(f"pilot:{seat}", {"action_id": action["id"], "targets": list(targets), "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return self.resolve(game)

    def test_actual_dazzling_reflection_prevents_whole_next_source_damage_batch(self):
        self.require_resolved_batch_rule()
        game = self.session(615080100)
        spell = self.add(game.engine, NAME, zone="hand")
        demon = self.add(game.engine, "Pestilence Demon", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        private = self.add(game.engine, "Counterspell", seat="C", zone="hand", ref="reflection-private")
        for card in (spell, demon):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1})
        game.state.players["B"].mana_pool.update({"B": 2})
        self.checkpoint(game)
        action = self.action(game, "A", spell)
        command = {"action_id": action["id"], "targets": [demon.ref], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast_for(game, "A", spell, [demon.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([47, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.activate(game, demon))
        self.assertEqual([47, 40, 40, 40], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(0, game.state.cards[creature.object_id].marked_damage)
        self.assertEqual(0, game.state.cards[demon.object_id].marked_damage)
        game = self.replay(game, load=True)
        self.assertIsNone(self.activate(game, demon))
        self.assertEqual([46, 39, 39, 39], [game.state.players[seat].life for seat in "ABCD"])
        self.assertEqual(1, game.state.cards[creature.object_id].marked_damage)
        self.assertEqual(1, game.state.cards[demon.object_id].marked_damage)
        for viewer in ("pilot:A", "pilot:B", "pilot:D", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_two_reflections_prevent_two_separate_damage_instances(self):
        self.require_resolved_batch_rule()
        game = self.session(615080101)
        spells = {seat: self.add(game.engine, NAME, seat=seat, zone="hand", ref=f"reflection-{seat}")
                  for seat in "AC"}
        untap = {seat: self.add(game.engine, "Refocus", seat=seat, zone="hand", ref=f"refocus-{seat}")
                 for seat in "AC"}
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        pinger.acquired_control_turn_count = -1
        for card in (*spells.values(), *untap.values(), pinger):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "U": 1})
        game.state.players["C"].mana_pool.update({"C": 2, "W": 1, "U": 1})
        self.checkpoint(game)
        for seat in "AC":
            self.cast_for(game, seat, spells[seat], [pinger.ref])
            self.assertIsNone(self.resolve(game))
            self.assertEqual(41, game.state.players[seat].life)
        choice = self.activate(game, pinger, targets=["D"])
        self.assertIsNotNone(choice)
        self.assertEqual(("replacement.order", ["D"]), (choice.kind, list(choice.actors)))
        game = self.replay(game, load=True)
        ctx = game.packet("pilot:D", full=True)["decision"]["ctx"]
        command = {"action_id": "choose", "replacement": ctx["options"][0]["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:D", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(40, game.state.players["D"].life)
        for seat, life in (("A", 40), ("C", 39)):
            self.cast_for(game, seat, untap[seat], [game.state.cards[pinger.object_id].ref])
            self.assertIsNone(self.resolve(game))
            self.assertIsNone(self.activate(game, pinger, targets=["D"]))
            self.assertEqual(life, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_actual_blink_distinguishes_illegal_spell_target_and_new_damage_source(self):
        self.require_resolved_batch_rule()
        game = self.session(615080102)
        spell = self.add(game.engine, NAME, zone="hand")
        pinger = self.add(game.engine, "Prodigal Sorcerer", seat="B")
        blink = self.add(game.engine, "Cloudshift", seat="B", zone="hand")
        self.add(game.engine, "Fervor", seat="B")
        self.add(game.engine, "Plains", seat="B")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", spell, [pinger.ref])
        invalid = self.replay(game, load=True)
        self.cast_for(invalid, "B", blink, [pinger.ref])
        self.assertIsNone(self.resolve(invalid))
        self.assertEqual(40, invalid.state.players["A"].life)
        self.replay(invalid)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(41, game.state.players["A"].life)
        identity = game.state.cards[pinger.object_id].logical_object_id
        self.cast_for(game, "B", blink, [pinger.ref])
        self.assertIsNone(self.resolve(game))
        self.assertNotEqual(identity, game.state.cards[pinger.object_id].logical_object_id)
        self.assertIsNone(self.activate(game, pinger, targets=["D"]))
        self.assertEqual(39, game.state.players["D"].life)
        self.replay(game)

    def test_actual_second_damage_witness_kills_whole_turn_shield_mutation(self):
        self.require_resolved_batch_rule()
        from quorune.damage_modifier_state import PreventionMode
        from quorune.effect_runtime import damage_modifiers

        plan = damage_modifiers.plan_prevention_shield_creation

        def perpetual(host, request):
            return plan(host, replace(request, mode=PreventionMode.ALL))

        with patch.object(damage_modifiers, "plan_prevention_shield_creation", side_effect=perpetual):
            with self.assertRaises(AssertionError):
                self.test_actual_dazzling_reflection_prevents_whole_next_source_damage_batch()

    def test_default_admission_respects_the_explicit_next_instance_batch_block(self):
        entry = self.registry.capability(CAPABILITY)
        result, _ = register_card(self.db, self.registry, NAME)
        parked = entry["status"] == "blocked" and NEXT_INSTANCE_BATCH_BLOCKER in entry["blockers"]
        self.assertEqual(not parked, result["strict_capability_ready"], result["blockers"])

    def test_verified_withdrawal_retains_independent_life_and_prevention(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Target source next damage withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        for name in (NAME, "Soul's Grace", "Shielded Passage"):
            result, _ = register_card(self.db, registry, name)
            self.assertEqual(name != NAME, result["strict_capability_ready"], result["blockers"])
