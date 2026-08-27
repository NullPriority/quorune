from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.attachment_references import (
    AttachmentReferenceKind,
    SourceAttachmentSnapshot,
)
from quorune.carddb import CardDatabase
from quorune.compiler.direct_target import DirectPermanentTargetSpec
from quorune.compiler.tap_state_templates import (
    AttachedTapStateEffectTemplate,
    OptionalTapStateEffectTemplate,
    SourceTapStateEffectTemplate,
    TapStateAction,
    TargetedTapStateEffectTemplate,
    targeted_tap_state_effect_template,
)
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.deck import DeckLoader
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "targeted-tap-state.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "targeted-tap-state-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class TargetedTapStateTemplateTests(unittest.TestCase):
    def test_targeted_tap_state_template_is_immutable_and_copy_isolated(self):
        template = TargetedTapStateEffectTemplate(
            action=TapStateAction.TAP,
            target_spec=DirectPermanentTargetSpec(
                types_any=("artifact", "creature")
            ),
        )

        self.assertEqual(
            "tap-target-artifact-or-creature-v3",
            template.template_id,
        )
        self.assertEqual(
            ({"op": "tap", "card": "$target.0"},),
            template.effects,
        )
        schema = template.target_schema
        schema["types_any"].append("land")
        effects = template.effects
        effects[0]["op"] = "untap"
        self.assertEqual(
            ["artifact", "creature"], template.target_schema["types_any"]
        )
        self.assertEqual("tap", template.effects[0]["op"])
        with self.assertRaisesRegex(ValueError, "action"):
            TargetedTapStateEffectTemplate(  # type: ignore[arg-type]
                action="tap",
                target_spec=DirectPermanentTargetSpec(
                    types_any=("creature",)
                ),
            )
        with self.assertRaisesRegex(ValueError, "target"):
            TargetedTapStateEffectTemplate(  # type: ignore[arg-type]
                action=TapStateAction.TAP,
                target_spec="creature",
            )

    def test_whole_clause_parser_accepts_closed_single_object_forms(self):
        direct = targeted_tap_state_effect_template(
            "Tap another target artifact or creature an opponent controls."
        )
        self.assertIsInstance(direct, TargetedTapStateEffectTemplate)
        assert isinstance(direct, TargetedTapStateEffectTemplate)
        self.assertEqual(
            {
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "count": 1,
                "types_any": ["artifact", "creature"],
                "controller_relation": "opponent",
                "source_exclusion": True,
            },
            direct.target_schema,
        )

        source = targeted_tap_state_effect_template(
            "Untap this artifact.",
            source_is_permanent=True,
            source_card_types=("artifact",),
        )
        self.assertIsInstance(source, SourceTapStateEffectTemplate)
        attached = targeted_tap_state_effect_template(
            "Tap enchanted creature.",
            source_is_permanent=True,
            source_card_types=("enchantment",),
            source_attachment_relation=AttachmentReferenceKind.ENCHANTED,
        )
        self.assertIsInstance(attached, AttachedTapStateEffectTemplate)
        optional = targeted_tap_state_effect_template(
            "You may tap or untap target creature."
        )
        self.assertIsInstance(optional, OptionalTapStateEffectTemplate)
        assert isinstance(optional, OptionalTapStateEffectTemplate)
        self.assertEqual(
            ["tap", "untap", "decline"],
            [row["id"] for row in optional.effects[0]["options"]],
        )

        for text in (
            "Tap up to two target creatures.",
            "You may tap target creature.",
            "Tap target creature. Scry 1.",
            "Untap all creatures you control.",
            "Tap equipped creature.",
            "Tap enchanted player.",
            "Tap target player.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(targeted_tap_state_effect_template(text))
        self.assertIsNone(
            targeted_tap_state_effect_template(
                "Untap this creature.",
                source_is_permanent=True,
                source_card_types=("artifact",),
            )
        )
        self.assertIsNone(
            targeted_tap_state_effect_template(
                "Untap this Vehicle.",
                source_is_permanent=True,
                source_card_types=("artifact",),
            )
        )


class TargetedTapStateCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Lightning Greaves")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, oracle_text: str, *, type_line: str = "Instant"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=oracle_text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_single_object_tap_state_forms_compile_through_typed_owners(self):
        contexts = (
            (
                "Tap target creature.",
                "Instant",
                "spell_ability",
                "tap-target-creature-v3",
                {"permanent.tap.effect", "target.revalidate_resolution"},
            ),
            (
                "When this creature enters, tap target creature.",
                "Creature — Test",
                "triggered_ability",
                "tap-target-creature-v3",
                {
                    "permanent.tap.effect",
                    "target.revalidate_resolution",
                    "trigger.event.normalized_zone_change",
                    "trigger.placement.apnap",
                },
            ),
            (
                "{1}, {T}: Tap target artifact, creature, or land.",
                "Artifact",
                "activated_ability",
                "tap-target-artifact-or-creature-or-land-v3",
                {
                    "permanent.tap.effect",
                    "target.permanent.characteristic_predicate",
                    "target.revalidate_resolution",
                },
            ),
            (
                "{T}: Untap target Forest.",
                "Creature — Elf Druid",
                "activated_ability",
                "untap-target-forest-v3",
                {
                    "permanent.untap.effect",
                    "target.revalidate_resolution",
                },
            ),
            (
                "{T}: Untap target Gate.",
                "Creature — Elf Druid",
                "activated_ability",
                "untap-target-gate-v3",
                {
                    "permanent.untap.effect",
                    "target.revalidate_resolution",
                },
            ),
            (
                "{4}: Untap this artifact.",
                "Artifact",
                "activated_ability",
                "untap-this-artifact-v1",
                {"permanent.untap.effect"},
            ),
            (
                "{U}: Tap enchanted creature.",
                "Enchantment — Aura",
                "activated_ability",
                "tap-enchanted-creature-v1",
                {
                    "attachment.reference.current_or_lki",
                    "permanent.tap.effect",
                },
            ),
            (
                "{U}, {T}: You may tap or untap target creature.",
                "Creature — Spirit",
                "activated_ability",
                "choose-tap-or-untap-target-creature-v1",
                {
                    "permanent.tap.effect",
                    "permanent.tap_state.optional_choice",
                    "permanent.untap.effect",
                    "target.revalidate_resolution",
                },
            ),
        )
        for text, type_line, kind, template_id, capabilities in contexts:
            with self.subTest(kind=kind, text=text):
                ir = self.compile(text, type_line=type_line)
                node = ir.faces[0].nodes[0]
                self.assertEqual("exact", ir.status)
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertEqual(template_id, node.template_id)
                self.assertEqual(capabilities, set(node.capability_dependencies))
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_unsupported_tap_state_variants_remain_material_residuals(self):
        for text in (
            "Untap all creatures you control.",
            "Tap all creatures target player controls.",
            "Untap each other Elf you control.",
            "Tap equipped creature.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_single_object_tap_state_shape_mutants_fail_closed(self):
        template = TargetedTapStateEffectTemplate(
            action=TapStateAction.UNTAP,
            target_spec=DirectPermanentTargetSpec(
                types_any=("artifact", "creature")
            ),
        )
        expected = {
            "permanent.untap.effect",
            "target.permanent.characteristic_predicate",
            "target.revalidate_resolution",
        }
        self.assertEqual(
            expected,
            set(
                capability_dependencies_for_node(
                    effects=template.effects,
                    target_schema=template.target_schema,
                    mechanic_ids=template.mechanics,
                )
            ),
        )
        malformed_effects = (
            ({"op": "untap", "card": "$target.1"},),
            ({"op": "untap", "card": "$target.0", "reason": "open"},),
            ({"op": "untap", "card": "$source"},),
            ({"op": "untap_all_creatures"},),
        )
        for effects in malformed_effects:
            with self.subTest(effects=effects):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=effects,
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        malformed_schemas = (
            {**template.target_schema, "zones": ["hand"]},
            {**template.target_schema, "count": 2},
            {**template.target_schema, "types_any": ["artifact", "spell"]},
        )
        for schema in malformed_schemas:
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=template.mechanics,
                    )
                )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=("tap-and-untap",),
            )
        )

        source = SourceTapStateEffectTemplate(
            TapStateAction.UNTAP,
            "artifact",
        )
        self.assertEqual(
            ("permanent.untap.effect",),
            capability_dependencies_for_node(
                effects=source.effects,
                target_schema=None,
                mechanic_ids=source.mechanics,
            ),
        )
        self.assertFalse(
            capability_dependencies_for_node(
                effects=({"op": "untap", "card": "$source"},),
                target_schema=None,
                mechanic_ids=source.mechanics,
            )
        )
        attached = AttachedTapStateEffectTemplate(
            TapStateAction.TAP,
            "creature",
        )
        self.assertEqual(
            {
                "attachment.reference.current_or_lki",
                "permanent.tap.effect",
            },
            set(
                capability_dependencies_for_node(
                    effects=attached.effects,
                    target_schema=None,
                    mechanic_ids=attached.mechanics,
                )
            ),
        )
        malformed_attachment = dict(attached.effects[0])
        malformed_attachment["card"] = {
            **dict(malformed_attachment["card"]),
            "unknown": True,
        }
        self.assertFalse(
            capability_dependencies_for_node(
                effects=(malformed_attachment,),
                target_schema=None,
                mechanic_ids=attached.mechanics,
            )
        )

        optional = OptionalTapStateEffectTemplate(
            DirectPermanentTargetSpec(types_any=("creature",))
        )
        self.assertEqual(
            {
                "permanent.tap.effect",
                "permanent.tap_state.optional_choice",
                "permanent.untap.effect",
                "target.revalidate_resolution",
            },
            set(
                capability_dependencies_for_node(
                    effects=optional.effects,
                    target_schema=optional.target_schema,
                    mechanic_ids=optional.mechanics,
                )
            ),
        )
        for mutation in (
            {**optional.effects[0], "player": "$active"},
            {
                **optional.effects[0],
                "then_by_choice": {
                    **optional.effects[0]["then_by_choice"],
                    "decline": [{"op": "tap", "card": "$target.0"}],
                },
            },
            {
                **optional.effects[0],
                "options": optional.effects[0]["options"][:2],
            },
        ):
            with self.subTest(mutation=mutation):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(mutation,),
                        target_schema=optional.target_schema,
                        mechanic_ids=optional.mechanics,
                    )
                )

    def test_tap_state_compiler_and_dependency_mutants_are_killed(self):
        text = "{U}, {T}: You may tap or untap target creature."

        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile(text, type_line="Creature — Spirit").status,
            )

        exact()
        with patch(
            "quorune.oracle_ir.targeted_tap_state_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()

        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json").read_text(
                encoding="utf-8"
            )
        )
        dependency = next(
            row
            for row in registry_value["capabilities"]
            if row["id"] == "permanent.untap.effect"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line="Creature — Spirit",
                keywords=(),
                faces=(),
            ),
            capability_registry=CapabilityRegistry(registry_value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_generated_direct_target_programs_are_capability_closed(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (self.db.lookup("Rathi Trapper"), self.db.lookup("Voltaic Key")),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_effect_programs=True,
        )
        programs = [program for program in registry.programs() if program.effects]
        self.assertEqual(2, result["exact_effect_programs_promoted"])
        self.assertEqual({"trusted"}, {program.trust_level for program in programs})
        self.assertEqual(
            {
                "permanent.tap.effect",
                "permanent.untap.effect",
            },
            {
                dependency
                for program in programs
                for dependency in program.capability_dependencies
                if dependency.startswith("permanent.")
            },
        )


class TargetedTapStateRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(
            ROOT / "examples" / "mishra-eminent-one.txt",
            commander="Mishra, Eminent One",
            deck_name="Mishra",
        )
        cls.zimone = loader.load(
            ROOT / "examples" / "zimone-and-dina.txt",
            commander="Zimone and Dina",
            deck_name="Zimone",
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session_with_card(self, card_name: str, *, players: int, seed: int):
        deck = copy.deepcopy(self.mishra)
        next(entry for entry in deck.entries if entry.board == "mainboard").name = (
            card_name
        )
        session = make_session(
            self.db,
            deck,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def pass_stack(session):
        while session.state.stack:
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Stack resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)

    @staticmethod
    def pass_until_choice(session, *, limit: int = 24):
        for _ in range(limit):
            decision = session.state.pending_decision
            payload = (
                decision.payload_by_actor.get("A", {})
                if decision is not None
                else {}
            )
            if any(
                row.get("id") == "choose"
                for row in payload.get("legal_actions", ())
            ):
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Choice resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Resolution did not reach the semantic choice")

    def assert_replays(self, session, label: str):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    @staticmethod
    def stage_activation(
        session,
        source,
        *,
        mana: dict[str, int],
        tapped: bool = False,
        aura_target_ref: str | None = None,
    ):
        engine = session.engine
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            tapped=tapped,
            aura_target_ref=aura_target_ref,
            log=False,
        )
        engine.state.players["A"].turns_begun = 1
        source.acquired_control_turn_count = 0
        engine.state.players["A"].mana_pool.update(mana)
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def activation_action(session, source, ability_id: str):
        packet = session.packet("pilot:A", full=True)
        return next(
            row
            for row in packet["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == f"activate:{source.ref}:{ability_id}"
        )

    def test_compiled_tap_activation_is_multiplayer_public_and_replays(self):
        session = self.session_with_card(
            "Rathi Trapper",
            players=4,
            seed=7012608,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Rathi Trapper"
        )
        target_ref = engine.create_token(
            "C",
            name="Public Tap Target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            reason="targeted tap-state fixture",
        )[0]
        target = engine._resolve_object("A", target_ref, zones={"battlefield"})
        self.stage_activation(session, source, mana={"B": 1})
        packet = session.packet("pilot:A", full=True)
        action = next(
            row
            for row in packet["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == f"activate:{source.ref}:ab1"
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        self.assertFalse(
            {"A", "B", "C", "D"}.intersection(
                action["target_schema"]["legal_refs"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": ["C"]},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        source = engine.state.cards[source.object_id]
        target = engine.state.cards[target.object_id]

        accepted = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [target.ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(source.tapped)
        self.pass_stack(session)
        self.assertTrue(target.tapped)
        self.assert_replays(session, "targeted-tap-record")

    def test_compiled_untap_activation_consumes_stun_and_replays(self):
        session = self.session_with_card(
            "Voltaic Key",
            players=2,
            seed=7012609,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Voltaic Key"
        )
        target_ref = engine.create_token(
            "B",
            name="Stunned Artifact",
            tapped=True,
            characteristics={"type_line": "Token Artifact"},
            reason="targeted untap fixture",
        )[0]
        target = engine._resolve_object("A", target_ref, zones={"battlefield"})
        target.counters["stun"] = 1
        self.stage_activation(session, source, mana={"C": 1})
        packet = session.packet("pilot:A", full=True)
        action = next(
            row
            for row in packet["decision"]["ctx"]["legal"]["actions"]
            if row["id"] == f"activate:{source.ref}:ab1"
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [target.ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertTrue(target.tapped)
        self.assertNotIn("stun", target.counters)
        self.assertIn(
            "permanent.untap.replaced",
            [event.code for event in engine.state.events],
        )
        self.assert_replays(session, "targeted-untap-record")

    def test_shared_direct_predicate_targets_land_subtypes_and_replays(self):
        session = self.session_with_card(
            "Arbor Elf",
            players=2,
            seed=7012610,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Arbor Elf"
        )
        forest_ref = engine.create_token(
            "B",
            name="Tapped Forest",
            tapped=True,
            characteristics={"type_line": "Token Land — Forest"},
            reason="land-subtype tap-state fixture",
        )[0]
        other_ref = engine.create_token(
            "B",
            name="Tapped Nonforest",
            tapped=True,
            characteristics={"type_line": "Token Land — Gate"},
            reason="land-subtype negative fixture",
        )[0]
        forest = engine._resolve_object("A", forest_ref, zones={"battlefield"})
        self.stage_activation(session, source, mana={})
        action = self.activation_action(session, source, "ab1")
        legal_refs = action["target_schema"]["legal_refs"]
        self.assertIn(forest_ref, legal_refs)
        self.assertNotIn(other_ref, legal_refs)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [forest_ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertFalse(forest.tapped)
        self.assert_replays(session, "forest-untap-record")

    def test_source_self_activation_is_incarnation_pinned_and_replays(self):
        session = self.session_with_card(
            "Grim Monolith",
            players=2,
            seed=7012611,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Grim Monolith"
        )
        self.stage_activation(
            session,
            source,
            mana={"C": 4},
            tapped=True,
        )
        action = self.activation_action(session, source, "ab3")
        self.assertNotIn("target_schema", action)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_stack(session)
        self.assertFalse(source.tapped)
        self.assert_replays(session, "source-self-untap-record")

        stale = self.session_with_card(
            "Grim Monolith",
            players=2,
            seed=7012612,
        )
        stale_engine = stale.engine
        stale_source = next(
            card
            for card in stale_engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Grim Monolith"
        )
        self.stage_activation(
            stale,
            stale_source,
            mana={"C": 4},
            tapped=True,
        )
        stale_action = self.activation_action(stale, stale_source, "ab3")
        accepted = stale.act(
            "pilot:A",
            {"action_id": stale_action["id"]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        pinned_identity = stale_engine.state.stack[-1].context[
            "source_logical_object_id"
        ]
        stale_engine.move_card(stale_source.object_id, "graveyard", log=False)
        stale_engine.move_card(
            stale_source.object_id,
            "battlefield",
            controller="A",
            tapped=True,
            log=False,
        )
        self.assertNotEqual(pinned_identity, stale_source.logical_object_id)
        stale_engine.permissions.invalidate_current()
        stale_engine.state.pending_decision = None
        stale_engine._grant_priority("A")
        stale_engine.pump()
        stale.initial_checkpoint = checkpoint_envelope(stale_engine.state)
        stale.commands.clear()
        stale.decisions.clear()

        self.pass_stack(stale)
        self.assertTrue(stale_source.tapped)
        self.assert_replays(stale, "stale-source-self-record")

    def test_enchanted_creature_uses_pinned_attachment_and_replays(self):
        session = self.session_with_card(
            "Freed from the Real",
            players=2,
            seed=7012613,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Freed from the Real"
        )
        target_ref = engine.create_token(
            "B",
            name="Enchanted Target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            reason="attachment tap-state fixture",
        )[0]
        target = engine._resolve_object("A", target_ref, zones={"battlefield"})
        self.stage_activation(
            session,
            source,
            mana={"U": 1},
            aura_target_ref=target_ref,
        )
        action = self.activation_action(session, source, "ab2")
        self.assertNotIn("target_schema", action)
        accepted = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        snapshot = SourceAttachmentSnapshot.from_dict(
            engine.state.stack[-1].context["source_attachment_snapshot"]
        )
        assert snapshot.attached_object is not None
        self.assertEqual(
            target.logical_object_id,
            snapshot.attached_object.logical_object_id,
        )
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.pass_stack(session)
        self.assertTrue(target.tapped)
        self.assert_replays(session, "attached-tap-state-record")

    def test_attached_tap_trigger_and_static_characteristics_compose_and_replay(
        self,
    ):
        session = self.session_with_card(
            "Colossification",
            players=4,
            seed=7012615,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Colossification"
        )
        target_ref = engine.create_token(
            "C",
            name="Colossified Target",
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            reason="attachment tap-state composition fixture",
        )[0]
        target = engine._resolve_object("A", target_ref, zones={"battlefield"})
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=target.ref,
            semantic_events=True,
            log=False,
        )
        engine._stabilize()
        trigger = next(
            item
            for item in engine.state.stack
            if item.source_object_id == source.object_id
            and item.semantic_key
            and "front:n2" in item.semantic_key
        )
        snapshot = SourceAttachmentSnapshot.from_dict(
            trigger.context["source_attachment_snapshot"]
        )
        assert snapshot.attached_object is not None
        self.assertEqual(
            target.logical_object_id,
            snapshot.attached_object.logical_object_id,
        )
        characteristics = engine._effective_card_data(target)
        self.assertEqual("21", characteristics["power"])
        self.assertEqual("21", characteristics["toughness"])

        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.pass_stack(session)
        self.assertTrue(target.tapped)
        characteristics = engine._effective_card_data(target)
        self.assertEqual("21", characteristics["power"])
        self.assertEqual("21", characteristics["toughness"])
        self.assert_replays(session, "attached-tap-static-composition-record")

    def test_optional_tap_state_choice_is_seat_scoped_atomic_and_replays(self):
        session = self.session_with_card(
            "Niblis of the Breath",
            players=4,
            seed=7012614,
        )
        engine = session.engine
        source = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.printed_name == "Niblis of the Breath"
        )
        target_ref = engine.create_token(
            "C",
            name="Optional Tap Target",
            tapped=True,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": "1",
                "toughness": "1",
            },
            reason="optional tap-state fixture",
        )[0]
        target = engine._resolve_object("A", target_ref, zones={"battlefield"})
        target.counters["stun"] = 1
        self.stage_activation(session, source, mana={"U": 1})
        action = self.activation_action(session, source, "ab2")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {"action_id": action["id"], "targets": [target_ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_choice(session)
        decision = session.state.pending_decision
        assert decision is not None
        choice = decision.payload_by_actor["A"]["legal_actions"][0]
        self.assertEqual(
            ["tap", "untap", "decline"],
            choice["choice_schema"]["legal_values"],
        )
        observer = json.dumps(session.packet("pilot:B", full=True))
        self.assertNotIn('"legal_values": ["tap", "untap", "decline"]', observer)
        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "invalid"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        selected = session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "untap"},
        )
        self.assertTrue(selected.ok, selected.summary)
        if engine.state.stack:
            self.pass_stack(session)
        target = engine.state.cards[target.object_id]
        source = engine.state.cards[source.object_id]
        self.assertTrue(target.tapped)
        self.assertNotIn("stun", target.counters)
        self.assertIn(
            "semantic.option.chosen",
            [event.code for event in engine.state.events],
        )
        self.assert_replays(session, "optional-untap-state-record")

        source.tapped = False
        engine.state.players["A"].mana_pool["U"] += 1
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine._grant_priority("A")
        engine.pump()
        decline_action = self.activation_action(session, source, "ab2")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {"action_id": decline_action["id"], "targets": [target_ref]},
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_choice(session)
        tap_events = sum(
            event.code == "permanent.tap" for event in engine.state.events
        )
        declined = session.act(
            "pilot:A",
            {"action_id": "choose", "choice": "decline"},
        )
        self.assertTrue(declined.ok, declined.summary)
        if engine.state.stack:
            self.pass_stack(session)
        target = engine.state.cards[target.object_id]
        self.assertTrue(target.tapped)
        self.assertEqual(
            tap_events,
            sum(
                event.code == "permanent.tap"
                for event in engine.state.events
            ),
        )
        self.assert_replays(session, "optional-decline-state-record")


if __name__ == "__main__":
    unittest.main()
