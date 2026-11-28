"""CR 700.2, 115.7a, 509.1b: one chosen mode owns its targets and result."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import PropertyMock, patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.temporary_declaration_templates import GroupTemporaryDeclarationRestrictionTemplate
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.node_capability_shapes import temporary_declaration_restriction_node_capabilities
from quorune.stack_target_change_model import SingleStackTargetChangeSpec
from scripts.build_test_database import build_fixture_database


PROFILE = "commander_review"
NAME = "Untimely Malfunction"
ORACLE_ID = "2c49d24a-98a4-43c2-bb59-1ef13d4214c2"
BLOCK_RULE = "continuous.resolution.declaration_rules_until_end_of_turn"
MODES = (
    "Destroy target artifact.",
    "Change the target of target spell or ability with a single target.",
    "One or two target creatures can't block this turn.",
)
ORACLE_TEXT = "Choose one —\n" + "\n".join("• " + line for line in MODES)


class UntimelyMalfunctionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text):
        card = CardRecord(
            oracle_id=ORACLE_ID, name=NAME, mana_cost="{1}{R}", mana_value=2,
            oracle_text=text, type_line="Instant", power=None, toughness=None,
            loyalty=None, defense=None, colors=("R",), color_identity=("R",),
            keywords=(), produced_mana=(), layout="normal", released_at="2024-09-27",
            legalities={"commander": "legal"}, faces=(), raw={},
        )
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)

    def test_exact_modal_program_retains_the_closed_targets_and_modes(self):
        ir = self.compile(ORACLE_TEXT)
        self.assertEqual((), ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual(ORACLE_TEXT, node.text)
        self.assertEqual(1, node.target_schema["mode_count"])
        first, second, third = node.target_schema["modes"].values()
        self.assertEqual((1, ["artifact"]), (first["count"], first["types_any"]))
        self.assertEqual([{"op": "destroy", "card": "$target.0"}], first["effects"])
        redirect = SingleStackTargetChangeSpec(True)
        self.assertEqual(redirect.target_schema, {
            key: value for key, value in second.items() if key not in {"effects", "mechanics"}
        })
        self.assertEqual(list(redirect.effects), second["effects"])
        self.assertEqual((1, 2, ["creature"]), (third["min"], third["max"], third["types_any"]))
        self.assertEqual([{
            "op": "grant_declaration_restriction_until_end_of_turn",
            "cards": "$targets", "restriction": "cant_block",
        }], third["effects"])
        self.assertIn(BLOCK_RULE, node.capability_dependencies)

    def test_group_count_authority_rejects_mixed_and_malformed_bounds(self):
        node, = self.compile(MODES[2]).faces[0].nodes
        schema = node.target_schema
        for fields in ({"count": 2}, {"up_to": 2}, {"minimum": 0}, {"min": True}, {"max": 3}):
            with self.subTest(fields=fields):
                self.assertEqual((), temporary_declaration_restriction_node_capabilities(
                    effects=node.effects, target_schema={**schema, **fields}, mechanic_ids=node.mechanics,
                ))
        for text in (
            "Up to one or two target creatures can't block this turn.",
            "One or three target creatures can't block this turn.",
            "One or two target creatures can't block next turn.",
            "One or two target lands can't block this turn.",
        ):
            with self.subTest(text=text):
                self.assertTrue(self.compile(text).material_residuals)

    def test_zero_minimum_mutation_is_detected(self):
        template = GroupTemporaryDeclarationRestrictionTemplate
        node, = self.compile(MODES[2]).faces[0].nodes
        optional = {key: value for key, value in node.target_schema.items() if key not in {"min", "max"}}
        optional.update(count=2, up_to=2)
        self.test_exact_modal_program_retains_the_closed_targets_and_modes()
        with patch.object(template, "target_schema", new_callable=PropertyMock, return_value=optional):
            with self.assertRaises((AssertionError, KeyError)):
                self.test_exact_modal_program_retains_the_closed_targets_and_modes()


class UntimelyMalfunctionNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "untimely-malfunction.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "untimely-malfunction-cards.json",
            fixtures / "bound-effect-program-cards.json",
            fixtures / "activation-result-views-cards.json",
            fixtures / "ability-source-counter-cards.json",
            fixtures / "cast-creature-targets.json",
            fixtures / "designation-prevention-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Untimely Malfunction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def add_malfunction(self, game, seat="A"):
        source = self.add(game.engine, NAME, seat=seat, zone="hand")
        self.assert_closed(game, source)
        self.assertEqual(ORACLE_ID, source.oracle_id)
        return source

    def add_lands(self, game, seat, *names):
        for index, name in enumerate(names):
            self.add(game.engine, name, seat=seat, ref=f"malfunction-{seat}-land-{index}")

    def offer(self, game, actor, card):
        self.priority(game, actor)
        return self.action(game, actor, game.state.cards[card.object_id])

    def stack_item(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def commit_mode(self, game, actor, source, mode, targets, invalid=()):
        offer = self.offer(game, actor, source)
        command = {"action_id": offer["id"], "modes": [mode], "targets": list(targets), "pay": "auto"}
        self.reject(game, "D", command)
        for changes in invalid:
            self.reject(game, actor, {**command, **changes})
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)
        item = self.stack_item(game, source)
        self.assertEqual(([mode], list(targets)), (item.modes, item.targets))
        selected, = item.context["target_groups"].values()
        self.assertEqual(list(targets), list(selected))
        return item

    def test_destroy_mode_only_destroys_the_public_artifact(self):
        game = self.session(9760501)
        source = self.add_malfunction(game)
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        creature = self.add(game.engine, "Grizzly Bears", seat="C")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)

        schema = self.offer(game, "A", source)["target_schema"]
        self.assertEqual(["mode_1", "mode_3"], schema["legal_modes"])
        first, = schema["mode_schemas"]["mode_1"]["groups"]
        self.assertEqual([artifact.ref], first["legal_refs"])
        self.commit_mode(game, "A", source, "mode_1", [artifact.ref], (
            {"targets": [creature.ref]}, {"modes": ["mode_2"]},
            {"modes": ["mode_1", "mode_3"], "targets": [artifact.ref, creature.ref]},
        ))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[artifact.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertFalse(game.state.continuous_effects)
        self.replay(game, load=True)

    def test_change_spell_mode_uses_the_live_original_slot_and_only_the_caster_chooses(self):
        game = self.session(9760502)
        shock = self.add(game.engine, "Shock", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        source = self.add_malfunction(game, "C")
        coast = self.add(game.engine, "Grizzly Bears", seat="D", zone="hand", ref="malfunction-private")
        self.add_lands(game, "B", "Island", "Island")
        self.add_lands(game, "C", "Mountain", "Forest")
        self.prepare(game, mana={"R": 1})
        self.checkpoint(game)
        self.cast(game, "A", shock, ["D"])
        original = self.stack_item(game, shock).ref
        self.cast(game, "B", counter, [original])
        subject = self.stack_item(game, counter).ref

        schema = self.offer(game, "C", source)["target_schema"]
        self.assertEqual(["mode_2"], schema["legal_modes"])
        group, = schema["mode_schemas"]["mode_2"]["groups"]
        self.assertEqual({original, subject}, set(group["legal_refs"]))
        resolving = self.commit_mode(game, "C", source, "mode_2", [subject], (
            {"targets": ["D"]}, {"targets": [subject, original]},
        )).ref
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (decision.kind, decision.actors))
        choice = decision.payload_by_actor["C"]["legal_actions"][0]["choice_schema"]
        self.assertFalse(choice["optional"])
        self.assertEqual([resolving], list(choice["target_schema"]["legal_refs"]))
        for actor in "ABD":
            self.assertIsNone(game.packet("pilot:" + actor, full=True)["decision"])
        self.assertNotIn(coast.ref, json.dumps(game.packet("pilot:C", full=True)))
        command = {"action_id": "choose", "targets": [resolving]}
        self.reject(game, "A", command)
        self.reject(game, "C", {**command, "targets": [original]})
        self.reject(game, "C", {**command, "targets": [subject]})

        game = self.replay(game, load=True)
        result = game.act("pilot:C", command)
        self.assertTrue(result.ok, result.summary)
        item = self.stack_item(game, counter)
        self.assertEqual(("B", [resolving]), (item.controller, item.targets))
        self.reject(game, "C", command)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(38, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_change_ability_mode_excludes_two_announced_target_slots(self):
        game = self.session(9760503)
        caster = self.add(game.engine, "Prodigal Sorcerer", ref="malfunction-sorcerer")
        caster.acquired_control_turn_count = -1
        bond = self.add(game.engine, "Common Bond", zone="hand")
        bears = self.add(game.engine, "Grizzly Bears", seat="B")
        source = self.add_malfunction(game, "C")
        self.add_lands(game, "C", "Mountain", "Forest")
        self.prepare(game, mana={"C": 1, "G": 1, "W": 1})
        self.checkpoint(game)
        action = self.offer(game, "A", bond)
        result = game.act("pilot:A", {
            "action_id": action["id"], "targets": [bears.ref, bears.ref],
            "pay": "manual", "payment": {"C": 1, "G": 1, "W": 1},
        })
        self.assertTrue(result.ok, result.summary)
        double = self.stack_item(game, bond).ref
        self.assertEqual([bears.ref, bears.ref], self.stack_item(game, bond).targets)
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        ability, = (row for row in actions if row["id"].startswith("activate:" + caster.ref + ":"))
        result = game.act("pilot:A", {"action_id": ability["id"], "targets": ["D"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        subject, = (item.ref for item in game.state.stack if item.kind == "activated_ability")
        groups, = self.offer(game, "C", source)["target_schema"]["mode_schemas"]["mode_2"]["groups"]
        self.assertEqual([subject], groups["legal_refs"])
        self.commit_mode(game, "C", source, "mode_2", [subject], ({"targets": [double]},))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:C", {"action_id": "choose", "targets": ["B"]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((39, 40), (game.state.players["B"].life, game.state.players["D"].life))
        self.assertEqual(2, game.state.cards[bears.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_two_creatures_are_selected_and_only_the_survivor_cannot_block(self):
        game = self.session(9760504)
        source = self.add_malfunction(game)
        attacker = self.add(game.engine, "Grizzly Bears", ref="malfunction-attacker")
        attacker.acquired_control_turn_count = -1
        allowed = self.add(game.engine, "Grizzly Bears", seat="B", ref="malfunction-allowed")
        left = self.add(game.engine, "Grizzly Bears", seat="B", ref="malfunction-left")
        survivor = self.add(game.engine, "Grizzly Bears", seat="B", ref="malfunction-survivor")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add_lands(game, "B", "Island")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        group, = self.offer(game, "A", source)["target_schema"]["mode_schemas"]["mode_3"]["groups"]
        self.assertEqual((1, 2), (group["min"], group["max"]))
        self.assertEqual({card.ref for card in (attacker, allowed, left, survivor)}, set(group["legal_refs"]))
        self.commit_mode(game, "A", source, "mode_3", [left.ref, survivor.ref], (
            {"targets": []}, {"targets": [survivor.ref, survivor.ref]},
            {"targets": [left.ref, survivor.ref, allowed.ref]}, {"targets": [artifact.ref]},
        ))
        self.cast_for(game, "B", bounce, [left.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[left.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[artifact.object_id].zone)
        rule, = game.state.continuous_effects
        self.assertEqual([survivor.logical_object_id], [identity.logical_object_id for identity in rule.locked_objects])
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        self.reject(game, "B", {"action_id": "block", "blocks": {survivor.ref: attacker.ref}})
        result = game.act("pilot:B", {"action_id": "block", "blocks": {allowed.ref: attacker.ref}})
        self.assertTrue(result.ok, result.summary)
        self.replay(game, load=True)

    def test_one_creature_is_accepted_and_does_not_undo_its_existing_block(self):
        game = self.session(9760505)
        source = self.add_malfunction(game)
        attacker = self.add(game.engine, "Grizzly Bears", ref="malfunction-attacker")
        attacker.acquired_control_turn_count = -1
        blocker = self.add(game.engine, "Grizzly Bears", seat="B", ref="malfunction-blocker")
        self.add_lands(game, "A", "Mountain", "Forest")
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers")
        result = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(result.ok, result.summary)
        declared = deepcopy(game.state.combat.blockers)
        self.commit_mode(game, "A", source, "mode_3", [blocker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual({attacker.object_id: [blocker.object_id]}, declared)
        self.assertEqual(declared, game.state.combat.blockers)
        self.until(game, lambda s: s.state.active_player == "B")
        self.assertFalse(game.state.continuous_effects)
        self.replay(game, load=True)

    def test_two_current_targets_both_receive_the_same_rule(self):
        game = self.session(9760507)
        source = self.add_malfunction(game)
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="malfunction-first-blocker")
        second = self.add(game.engine, "Grizzly Bears", seat="C", ref="malfunction-second-blocker")
        self.prepare(game, mana={"C": 1, "R": 1})
        self.checkpoint(game)
        self.commit_mode(game, "A", source, "mode_3", [first.ref, second.ref])
        self.assertIsNone(self.resolve(game))
        rule, = game.state.continuous_effects
        self.assertEqual(
            {first.logical_object_id, second.logical_object_id},
            {identity.logical_object_id for identity in rule.locked_objects},
        )
        self.replay(game, load=True)

    def test_runtime_requires_the_independent_blocking_mode_authority(self):
        game = self.session(9760506)
        source = self.add_malfunction(game)
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        deps = tuple(cap for cap in program.capability_dependencies if cap != BLOCK_RULE)
        result = bind_semantic_program_runtime(
            replace(program, capability_dependencies=deps,
                    capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict()),
            capability_registry=self.registry, profile=PROFILE,
        )
        self.assertFalse(result["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + BLOCK_RULE, result["blockers"])
        schema = thaw_value(program.target_schema)
        schema["modes"]["mode_3"]["min"] = 0
        result = bind_semantic_program_runtime(
            replace(program, target_schema=schema), capability_registry=self.registry, profile=PROFILE,
        )
        self.assertFalse(result["strict"])
