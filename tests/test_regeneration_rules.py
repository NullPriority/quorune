from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.attachment_references import AttachmentReferenceKind
from quorune.carddb import CardDatabase
from quorune import damage_results, tap_state
from quorune.compiler.regeneration_templates import (
    fixed_regeneration_effect_template,
    self_regeneration_effect_template,
)
from quorune.compiler.affected_player_discard_templates import (
    fixed_affected_player_discard_effect_template,
)
from quorune.compiler.destruction_templates import (
    mass_destruction_effect_template,
    targeted_destruction_effect_template,
)
from quorune.deck import DeckLoader
from quorune.destruction import (
    commit_destruction_plan,
    DestructionCause,
    DestructionDisposition,
    DestructionError,
    destroy_permanent_refs,
    prepare_destructions,
    request_for_card,
)
from quorune.model import CardInstance, StackItem
from quorune.projection import StateProjector
from quorune.oracle_ir import (
    compile_oracle_card,
    generated_programs,
    register_generated_programs,
)
from quorune.permanent_exile import exile_permanent
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.regeneration import create_regeneration_shield
from quorune.rules.capabilities import (
    capability_dependencies_for_node,
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.node_capability_shapes import (
    self_regeneration_node_capabilities,
)
from quorune.semantic_runtime.context import (
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    SemanticNodeError,
    SemanticSourceContext,
)
from quorune.semantic_runtime.destruction_handlers import (
    DestroyPermanentHandler,
    DestroyPermanentSetHandler,
)
from quorune.semantic_runtime.intents import (
    CreateRegenerationShieldIntent,
    DestroyPermanentIntent,
)
from quorune.semantic_runtime.regeneration_handlers import (
    CreateRegenerationShieldHandler,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "regeneration-rules.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "regeneration-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class RegenerationCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Drudge Skeletons")
        cls.fixed_records = {
            name: cls.db.lookup(name)
            for name in (
                "Death Ward",
                "Metallurgeon",
                "Gaea's Embrace",
                "Terminate",
                "Wrath of God",
            )
        }

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_exact_self_regeneration_compiles_and_harvests_frontier(self):
        ir = compile_oracle_card(
            self.record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        node = next(
            node
            for face in ir.faces
            for node in face.nodes
            if node.template_id == "regenerate-this-creature-v1"
        )
        self.assertEqual("exact", ir.status)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual(1, node.span.line)
        self.assertEqual(
            node.text,
            self.record.oracle_text[node.span.start : node.span.end],
        )
        self.assertEqual(
            ({"op": "regenerate", "card": "$source.zone_object"},),
            node.effects,
        )
        self.assertEqual(
            ("permanent.regeneration.self_activation",),
            node.capability_dependencies,
        )
        self.assertEqual(1, node.cost["mana"]["B"])
        program = next(
            program
            for program in generated_programs(
                self.db,
                self.record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "regenerate-this-creature-v1"
        )
        self.assertTrue(program.capability_closure["trusted"])
        self.assertFalse(program.requires_arbiter)

    def test_exact_fixed_regeneration_and_prohibition_grammar_compiles(self):
        expectations = {
            "Death Ward": (
                "regenerate-target-creature-v1",
                "permanent.regeneration.fixed_effect",
            ),
            "Metallurgeon": (
                "regenerate-target-artifact-v1",
                "permanent.regeneration.fixed_effect",
            ),
            "Gaea's Embrace": (
                "regenerate-attached-enchanted-creature-v1",
                "permanent.regeneration.fixed_effect",
            ),
            "Terminate": (
                "destroy-target-creature-regeneration-prohibited-v1",
                "permanent.destroy.regeneration_prohibition",
            ),
            "Wrath of God": (
                "destroy-fixed-set-",
                "permanent.destroy.regeneration_prohibition",
            ),
        }
        for name, (template_fragment, capability) in expectations.items():
            with self.subTest(name=name):
                ir = compile_oracle_card(
                    self.fixed_records[name],
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                matching = tuple(
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if template_fragment in node.template_id
                )
                self.assertEqual(1, len(matching))
                self.assertIn(capability, matching[0].capability_dependencies)

        for text, type_line, kind in (
            (
                "When this creature enters, regenerate target creature.",
                "Creature — Test",
                "triggered_ability",
            ),
            (
                "Choose one —\n"
                "• Regenerate target creature.\n"
                "• Destroy target creature.",
                "Instant",
                "spell_ability",
            ),
        ):
            with self.subTest(context=kind):
                ir = compile_oracle_card(
                    replace(
                        self.fixed_records["Death Ward"],
                        name="Regeneration Context Fixture",
                        oracle_text=text,
                        type_line=type_line,
                        keywords=(),
                        faces=(),
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(kind, node.kind)
                self.assertIn(
                    "permanent.regeneration.fixed_effect",
                    node.capability_dependencies,
                )

    def test_fixed_regeneration_handler_resolves_source_and_other_object_identity(self):
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="regeneration fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
            source=SemanticSourceContext(
                stack_ref="S-regenerate",
                object_id="fixture:regenerator",
                logical_object_id="logical:regenerator:1",
                card_ref="A-regenerator",
            ),
        )
        handler = CreateRegenerationShieldHandler()
        plan = handler.lower(
            {"op": "regenerate", "card": "A-regenerator"},
            context,
        )
        self.assertEqual(2, handler.schema_version)
        self.assertEqual("generic.create-regeneration-shield.v2", plan.handler_id)
        self.assertEqual(
            (
                CreateRegenerationShieldIntent(
                    actor="A",
                    object_ref="A-regenerator",
                    logical_object_id="logical:regenerator:1",
                    reason="regeneration fixture",
                ),
            ),
            plan.intents,
        )
        with self.assertRaises(FrozenInstanceError):
            plan.intents[0].reason = "mutated"  # type: ignore[misc]
        for malformed in (
            {"op": "regenerate", "card": ""},
            {"op": "regenerate", "card": "A-regenerator", "reason": 4},
            {"op": "regenerate", "card": "A-regenerator", "future": True},
            {
                "op": "regenerate",
                "card": "A-regenerator",
                "_replacement_selections": ["replacement-a"],
            },
        ):
            with self.subTest(effect=malformed):
                with self.assertRaises(SemanticNodeError):
                    handler.lower(malformed, context)
        mismatched = replace(
            context,
            source=replace(context.source, card_ref="A-other"),
        )
        target_plan = handler.lower(
            {"op": "regenerate", "card": "A-regenerator"},
            mismatched,
        )
        self.assertIsNone(target_plan.intents[0].logical_object_id)

    def test_unsupported_regeneration_grammar_remains_residual(self):
        variants = (
            "{B}: Regenerate this creature",
            "{B}: Regenerate this permanent.",
            "{B}: Regenerate another creature.",
            "{B}: Regenerate target Elf.",
            "{B}: Regenerate two target creatures.",
            "{B}: Regenerate this creature twice.",
            "{B}: Regenerate this creature only if it attacked this turn.",
        )
        for text in variants:
            with self.subTest(text=text):
                effect = text.split(":", 1)[-1].strip()
                self.assertIsNone(self_regeneration_effect_template(effect))
                self.assertIsNone(
                    fixed_regeneration_effect_template(
                        effect,
                        card_name=self.record.name,
                        source_is_permanent=True,
                    )
                )
                ir = compile_oracle_card(
                    replace(self.record, oracle_text=text),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        static_ir = compile_oracle_card(
            replace(self.record, oracle_text="Regenerate this creature."),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", static_ir.status)
        self.assertTrue(static_ir.material_residuals)

    def test_regeneration_shape_and_dependency_mutants_fail_closed(self):
        effect = ({"op": "regenerate", "card": "$source.zone_object"},)
        self.assertEqual(
            ("permanent.regeneration.self_activation",),
            self_regeneration_node_capabilities(
                effects=effect,
                target_schema=None,
                mechanic_ids=("regenerate",),
            ),
        )
        fixed = fixed_regeneration_effect_template(
            "Regenerate target creature.",
            card_name="Death Ward",
            source_is_permanent=False,
        )
        self.assertIsNotNone(fixed)
        assert fixed is not None
        self.assertEqual(
            {
                "permanent.regeneration.fixed_effect",
                "target.revalidate_resolution",
            },
            set(
                capability_dependencies_for_node(
                    effects=fixed.effects,
                    target_schema=fixed.target_schema,
                    mechanic_ids=fixed.mechanics,
                )
            ),
        )
        attached = fixed_regeneration_effect_template(
            "Regenerate enchanted creature.",
            card_name="Gaea's Embrace",
            source_is_permanent=True,
            source_attachment_relation=AttachmentReferenceKind.ENCHANTED,
        )
        self.assertIsNotNone(attached)
        assert attached is not None
        self.assertEqual(
            {
                "permanent.regeneration.fixed_effect",
                "attachment.reference.current_or_lki",
            },
            set(
                capability_dependencies_for_node(
                    effects=attached.effects,
                    target_schema=None,
                    mechanic_ids=attached.mechanics,
                )
            ),
        )
        for effects, target_schema, mechanics in (
            (({**effect[0], "future": True},), None, ("regenerate",)),
            (({"op": "regenerate", "card": "$source"},), None, ("regenerate",)),
            (effect, {"count": 1}, ("regenerate",)),
            (effect, None, ("regenerate", "destroy")),
        ):
            with self.subTest(effects=effects):
                self.assertEqual(
                    (),
                    self_regeneration_node_capabilities(
                        effects=effects,
                        target_schema=target_schema,
                        mechanic_ids=mechanics,
                    ),
                )

        def assert_exact(registry):
            result = compile_oracle_card(
                self.record,
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertEqual("exact", result.status)

        assert_exact(self.capabilities)
        with patch(
            "quorune.oracle_ir.fixed_regeneration_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact(self.capabilities)

        value = json.loads(json.dumps(self.registry_value))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "permanent.destroy.effect"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        blocked = compile_oracle_card(
            self.record,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(
            any(
                "permanent.destroy.effect" in blocker
                for residual in blocked.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_regeneration_prohibition_shape_and_intent_mutants_fail_closed(self):
        direct = targeted_destruction_effect_template(
            "Destroy target creature. It can't be regenerated."
        )
        mass = mass_destruction_effect_template(
            "Destroy all creatures. They can't be regenerated."
        )
        self.assertIsNotNone(direct)
        self.assertIsNotNone(mass)
        assert direct is not None and mass is not None
        for template, expected_owner in (
            (direct, "permanent.destroy.effect"),
            (mass, "permanent.destroy.fixed_set"),
        ):
            with self.subTest(owner=expected_owner):
                self.assertTrue(
                    {
                        expected_owner,
                        "permanent.destroy.regeneration_prohibition",
                    }.issubset(
                        capability_dependencies_for_node(
                            effects=template.effects,
                            target_schema=template.target_schema,
                            mechanic_ids=template.mechanics,
                        )
                    )
                )
                malformed = dict(template.effects[0])
                malformed["regeneration_prohibited"] = False
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(malformed,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )

        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="prohibition fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B"),
                active_seats=("A", "B"),
                apnap_order=("A", "B"),
            ),
        )
        direct_plan = DestroyPermanentHandler().lower(
            {**direct.effects[0], "card": "B-target"},
            context,
        )
        self.assertTrue(direct_plan.intents[0].regeneration_prohibited)
        for handler, effect in (
            (
                DestroyPermanentHandler(),
                {**direct.effects[0], "card": "B-target"},
            ),
            (DestroyPermanentSetHandler(), mass.effects[0]),
        ):
            for mutant in (False, 1, "true"):
                with self.subTest(handler=handler.handler_id, mutant=mutant):
                    with self.assertRaises(SemanticNodeError):
                        handler.lower(
                            {**effect, "regeneration_prohibited": mutant},
                            context,
                        )
        with self.assertRaises(ValueError):
            DestroyPermanentIntent(
                actor="A",
                object_ref="B-target",
                reason="prohibition fixture",
                regeneration_prohibited=1,  # type: ignore[arg-type]
            )


class RegenerationRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
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

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            copy.deepcopy(self.mishra),
            copy.deepcopy(self.zimone),
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

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        owner: str = "A",
        zone: str = "battlefield",
    ):
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=owner,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=(list(engine.seats) if zone == "battlefield" else [owner]),
            revealed_to=(list(engine.seats) if zone == "battlefield" else []),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[owner].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        return card

    def add_skeleton(
        self,
        session,
        *,
        ref: str = "A-regenerator",
        owner: str = "A",
    ):
        return self.add_card(
            session,
            name="Drudge Skeletons",
            ref=ref,
            owner=owner,
        )

    @staticmethod
    def prepare_priority(session):
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def pass_until_resolved(session):
        for _ in range(24):
            if not session.state.stack:
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(
                    f"{result.summary}; principal={principals[0]!r}; "
                    f"pending={session.pending_principals()!r}; "
                    f"decision={session.packet(principals[0], full=True).get('decision')!r}"
                )
        raise AssertionError("Regeneration activation did not resolve")

    def assert_replays(self, session):
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "regeneration-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_activation_creates_public_shield_and_replays(self):
        unpayable = self.session(7011900, players=4)
        unpayable_source = self.add_skeleton(unpayable)
        self.prepare_priority(unpayable)
        unpayable_action = f"activate:{unpayable_source.ref}:ab1"
        unpayable_offers = {
            action["id"]
            for action in unpayable.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
        }
        self.assertNotIn(unpayable_action, unpayable_offers)
        before_unpayable = authoritative_state_hash(unpayable.state)
        rejected = unpayable.act(
            "pilot:A",
            {"action_id": unpayable_action},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(
            before_unpayable,
            authoritative_state_hash(unpayable.state),
        )

        session = self.session(7011901, players=4)
        source = self.add_skeleton(session)
        session.state.players["A"].mana_pool["B"] = 1
        self.prepare_priority(session)
        action_id = f"activate:{source.ref}:ab1"
        offered = {
            action["id"]
            for action in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
        }
        self.assertIn(action_id, offered)
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        self.assertEqual(source.object_id, session.state.stack[-1].source_object_id)
        self.pass_until_resolved(session)

        self.assertEqual(1, source.regeneration_shields)
        self.assertEqual(0, session.state.players["A"].mana_pool["B"])
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            projected = session.packet(principal, full=True)["state"]
            public = next(
                card
                for card in projected["players"]["A"]["bf"]
                if card["id"] == source.ref
            )
            self.assertEqual(1, public["regen"])
        self.assert_replays(session)

    def test_targeted_regeneration_creates_shield_on_the_chosen_permanent(self):
        session = self.session(7011910, players=4)
        engine = session.engine
        source = self.add_card(
            session,
            name="Death Ward",
            ref="A-death-ward",
            zone="hand",
        )
        target = self.add_skeleton(
            session,
            ref="C-regeneration-target",
            owner="C",
        )
        engine.state.players["A"].mana_pool["W"] = 1
        self.prepare_priority(session)
        action_id = f"cast:{source.ref}"
        action = next(
            row
            for row in session.packet("pilot:A", full=True)["decision"]["ctx"][
                "legal"
            ]["actions"]
            if row["id"] == action_id
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"W": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_resolved(session)

        self.assertEqual("graveyard", source.zone)
        self.assertEqual("battlefield", target.zone)
        self.assertEqual(1, target.regeneration_shields)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            projected = session.packet(principal, full=True)["state"]
            public = next(
                card
                for card in projected["players"]["C"]["bf"]
                if card["id"] == target.ref
            )
            self.assertEqual(1, public["regen"])
        self.assert_replays(session)

    def test_attached_regeneration_composes_with_destination_replacement_and_replay(
        self,
    ):
        session = self.session(7011913, players=4)
        engine = session.engine
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool["B"] = 1
        target = self.add_skeleton(
            session,
            ref="A-strands-target",
            owner="A",
        )
        strands = self.add_card(
            session,
            name="Strands of Undeath",
            ref="A-strands",
            zone="hand",
        )
        engine.move_card(
            strands.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=target.ref,
            reason="Strands interaction witness",
            log=False,
        )
        self.add_card(
            session,
            name="Dauthi Voidwalker",
            ref="A-voidwalker",
            owner="A",
        )
        discarded = [
            engine.state.cards[object_id]
            for object_id in engine.state.players["B"].zones["hand"][:2]
        ]
        template = fixed_affected_player_discard_effect_template(
            "Target player discards two cards."
        )
        self.assertIsNotNone(template)
        assert template is not None
        stack_ref = engine._next_ref("S")
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", stack_ref),
            ref=stack_ref,
            kind="triggered_ability",
            controller="A",
            label="Strands of Undeath discard witness",
            targets=["B"],
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            template.effects,
            None,
            note="Strands of Undeath discard witness",
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        choice = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "cards": [card.ref for card in discarded],
            },
        )
        self.assertTrue(choice.ok, choice.summary)
        while (
            engine.state.pending_decision is not None
            and engine.state.pending_decision.kind == "replacement.order"
        ):
            principal = session.pending_principals()[0]
            projected = StateProjector(self.db, engine.state)._decision(
                principal
            )
            self.assertIsNotNone(projected)
            assert projected is not None
            selected = projected["ctx"]["options"][0]["id"]
            replacement = session.act(
                principal,
                {
                    "action_id": "choose",
                    "replacement": selected,
                    "plan": "ORDER_REPLACEMENTS",
                    "reason": "Choose the discard destination replacement.",
                },
            )
            self.assertTrue(replacement.ok, replacement.summary)

        self.assertTrue(all(card.zone == "exile" for card in discarded))
        self.assertTrue(all(card.counters["void"] == 1 for card in discarded))
        self.assertEqual(target.object_id, strands.attached_to)
        offered = [
            action
            for action in session.packet("pilot:A", full=True)["decision"][
                "ctx"
            ]["legal"]["actions"]
            if action["id"].startswith(f"activate:{strands.ref}:")
        ]
        self.assertEqual(1, len(offered))
        action_id = offered[0]["id"]
        activated = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(activated.ok, activated.summary)
        self.pass_until_resolved(session)

        self.assertEqual(1, target.regeneration_shields)
        self.assertEqual(0, engine.state.players["A"].mana_pool["B"])
        self.assertEqual(target.object_id, strands.attached_to)
        self.assert_replays(session)

    def test_regeneration_prohibition_preserves_other_destruction_protections(self):
        session = self.session(7011911, players=4)
        engine = session.engine
        shielded = self.add_skeleton(
            session,
            ref="B-shielded-regenerator",
            owner="B",
        )
        indestructible = self.add_skeleton(
            session,
            ref="C-indestructible-regenerator",
            owner="C",
        )
        ordinary = self.add_skeleton(
            session,
            ref="D-ordinary-regenerator",
            owner="D",
        )
        for card in (shielded, indestructible, ordinary):
            create_regeneration_shield(
                engine,
                card.ref,
                actor=card.controller,
                reason="prohibition interaction witness",
                logical_object_id=card.logical_object_id,
            )
        shielded.counters["shield"] = 1
        indestructible.temporary_keywords.append("indestructible")

        result = destroy_permanent_refs(
            engine,
            (shielded.ref, indestructible.ref, ordinary.ref),
            actor="A",
            reason="Terminate and Wrath of God witness",
            regeneration_prohibited=True,
        )

        self.assertEqual((ordinary.object_id,), result.destroyed_object_ids)
        self.assertEqual((shielded.object_id,), result.shielded_object_ids)
        self.assertEqual(
            (indestructible.object_id,),
            result.indestructible_object_ids,
        )
        self.assertEqual((), result.regenerated_object_ids)
        self.assertEqual(1, shielded.regeneration_shields)
        self.assertNotIn("shield", shielded.counters)
        self.assertEqual(1, indestructible.regeneration_shields)
        self.assertEqual("graveyard", ordinary.zone)

    def test_regeneration_prohibited_mass_destruction_is_atomic_and_replays(self):
        session = self.session(7011912, players=4)
        engine = session.engine
        wrath = self.add_card(
            session,
            name="Wrath of God",
            ref="A-wrath-of-god",
            zone="hand",
        )
        shielded = self.add_skeleton(
            session,
            ref="A-shielded-creature",
            owner="A",
        )
        ordinary = self.add_skeleton(
            session,
            ref="B-ordinary-creature",
            owner="B",
        )
        regenerated = self.add_skeleton(
            session,
            ref="C-regenerated-creature",
            owner="C",
        )
        indestructible = self.add_skeleton(
            session,
            ref="D-indestructible-creature",
            owner="D",
        )
        for card in (shielded, regenerated, indestructible):
            create_regeneration_shield(
                engine,
                card.ref,
                actor=card.controller,
                reason="Wrath of God witness",
                logical_object_id=card.logical_object_id,
            )
        shielded.counters["shield"] = 1
        indestructible.temporary_keywords.append("indestructible")
        engine.state.players["A"].mana_pool.update({"C": 2, "W": 2})
        self.prepare_priority(session)
        action_id = f"cast:{wrath.ref}"
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        accepted = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "pay": "manual",
                "payment": {"C": 2, "W": 2},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.pass_until_resolved(session)

        self.assertEqual("graveyard", wrath.zone)
        self.assertEqual("battlefield", shielded.zone)
        self.assertEqual(1, shielded.regeneration_shields)
        self.assertNotIn("shield", shielded.counters)
        self.assertEqual("graveyard", ordinary.zone)
        self.assertEqual("graveyard", regenerated.zone)
        self.assertEqual("battlefield", indestructible.zone)
        self.assertEqual(1, indestructible.regeneration_shields)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            projected = session.packet(principal, full=True)["state"]["players"]
            self.assertIn(
                shielded.ref,
                {card["id"] for card in projected["A"]["bf"]},
            )
            self.assertIn(
                regenerated.ref,
                {card["id"] for card in projected["C"]["gy"]},
            )
        self.assert_replays(session)

    def test_regeneration_replaces_effect_and_state_based_destruction(self):
        session = self.session(7011902, players=3)
        engine = session.engine
        source = self.add_skeleton(session)
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="effect witness",
            logical_object_id=source.logical_object_id,
        )
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="second effect witness",
            logical_object_id=source.logical_object_id,
        )
        engine.change_control(
            source.object_id,
            "C",
            reason="regeneration control-change witness",
        )

        effect = destroy_permanent_refs(
            engine,
            (source.ref,),
            actor="B",
            reason="effect destruction witness",
        )
        self.assertEqual((source.object_id,), effect.regenerated_object_ids)
        self.assertEqual("battlefield", source.zone)
        self.assertEqual("C", source.controller)
        self.assertEqual(1, source.regeneration_shields)

        source.marked_damage = 1
        source.deathtouch_damage = True
        plan = prepare_destructions(
            engine,
            (request_for_card(source),),
            cause=DestructionCause.STATE_BASED_ACTION,
            actor=None,
            reason="lethal damage state-based action",
        )
        self.assertEqual(
            DestructionDisposition.REGENERATION,
            plan.entries[0].disposition,
        )
        state_based = commit_destruction_plan(engine, plan)
        self.assertEqual(
            (source.object_id,), state_based.regenerated_object_ids
        )
        self.assertEqual("battlefield", source.zone)
        self.assertEqual(0, source.marked_damage)
        self.assertFalse(source.deathtouch_damage)

    def test_regeneration_taps_clears_damage_and_removes_from_combat(self):
        session = self.session(7011903, players=4)
        engine = session.engine
        source = self.add_skeleton(session)
        source.marked_damage = 8
        source.deathtouch_damage = True
        source.attacking = "C"
        engine.state.combat.attackers[source.object_id] = "C"
        engine.state.combat.attack_target_context[source.object_id] = {
            "defender": "C",
            "target_kind": "player",
        }
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="combat witness",
            logical_object_id=source.logical_object_id,
        )

        with (
            patch(
                "quorune.regeneration.tap_state.set_permanent_tapped",
                wraps=tap_state.set_permanent_tapped,
            ) as tap_owner,
            patch(
                "quorune.regeneration.damage_results.clear_permanent_damage",
                wraps=damage_results.clear_permanent_damage,
            ) as damage_owner,
            patch.object(
                engine,
                "_remove_object_from_combat",
                wraps=engine._remove_object_from_combat,
            ) as combat_owner,
        ):
            result = destroy_permanent_refs(
                engine,
                (source.ref,),
                actor="D",
                reason="combat destruction witness",
            )

        tap_owner.assert_called_once()
        damage_owner.assert_called_once()
        combat_owner.assert_called_once()

        self.assertEqual((source.object_id,), result.regenerated_object_ids)
        self.assertTrue(source.tapped)
        self.assertEqual(0, source.marked_damage)
        self.assertFalse(source.deathtouch_damage)
        self.assertIsNone(source.attacking)
        self.assertNotIn(source.object_id, engine.state.combat.attackers)
        self.assertIn(
            "combat.remove", [event.code for event in engine.state.events]
        )

    def test_regeneration_shields_expire_at_cleanup_and_zone_change(self):
        session = self.session(7011904)
        engine = session.engine
        source = self.add_skeleton(session)
        for _ in range(2):
            create_regeneration_shield(
                engine,
                source.ref,
                actor="A",
                reason="cleanup witness",
                logical_object_id=source.logical_object_id,
            )
        self.assertEqual(2, source.regeneration_shields)

        engine._finish_cleanup()
        self.assertEqual(0, source.regeneration_shields)

        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="zone-change witness",
            logical_object_id=source.logical_object_id,
        )
        engine.move_card(source.object_id, "graveyard", log=False)
        self.assertEqual(0, source.regeneration_shields)
        self.assertNotIn("regeneration_shields", source.to_dict())
        prior_incarnation = source.logical_object_id
        engine.move_card(source.object_id, "battlefield", log=False)
        self.assertNotEqual(prior_incarnation, source.logical_object_id)
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="stale incarnation witness",
            logical_object_id=prior_incarnation,
        )
        self.assertEqual(0, source.regeneration_shields)

    def test_regeneration_does_not_replace_sacrifice_exile_or_zero_toughness(self):
        sacrifice = self.session(7011907)
        sacrificed = self.add_skeleton(sacrifice, ref="A-sacrifice")
        create_regeneration_shield(
            sacrifice.engine,
            sacrificed.ref,
            actor="A",
            reason="sacrifice distinction",
            logical_object_id=sacrificed.logical_object_id,
        )
        sacrifice.engine.apply_effect(
            {"op": "sacrifice", "card": sacrificed.ref},
            actor="A",
        )
        self.assertEqual("graveyard", sacrificed.zone)
        self.assertEqual(0, sacrificed.regeneration_shields)

        exile = self.session(7011908)
        exiled = self.add_skeleton(exile, ref="A-exile")
        create_regeneration_shield(
            exile.engine,
            exiled.ref,
            actor="A",
            reason="exile distinction",
            logical_object_id=exiled.logical_object_id,
        )
        exile_permanent(
            exile.engine,
            exiled.ref,
            actor="B",
            reason="exile distinction",
        )
        self.assertEqual("exile", exiled.zone)
        self.assertEqual(0, exiled.regeneration_shields)

        zero = self.session(7011909)
        zero_toughness = self.add_skeleton(zero, ref="A-zero")
        create_regeneration_shield(
            zero.engine,
            zero_toughness.ref,
            actor="A",
            reason="zero-toughness distinction",
            logical_object_id=zero_toughness.logical_object_id,
        )
        zero_toughness.annotations["copy_overrides"] = {
            "name": zero_toughness.printed_name,
            "type_line": "Creature — Skeleton",
            "power": "1",
            "toughness": "0",
        }
        zero.engine._stabilize()
        self.assertEqual("graveyard", zero_toughness.zone)
        self.assertEqual(0, zero_toughness.regeneration_shields)

    def test_stale_and_competing_replacements_fail_before_mutation(self):
        session = self.session(7011905)
        engine = session.engine
        source = self.add_skeleton(session)
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="stale witness",
            logical_object_id=source.logical_object_id,
        )
        plan = prepare_destructions(
            engine,
            (request_for_card(source),),
            cause=DestructionCause.EFFECT,
            actor="B",
            reason="stale destruction witness",
        )
        source.regeneration_shields += 1
        before_stale = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(DestructionError, "stale"):
            commit_destruction_plan(engine, plan)
        self.assertEqual(before_stale, authoritative_state_hash(engine.state))

        source.regeneration_shields = 1
        source.counters["shield"] = 1
        before_choice = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(DestructionError, "affected-player choice"):
            prepare_destructions(
                engine,
                (request_for_card(source),),
                cause=DestructionCause.EFFECT,
                actor="B",
                reason="competing replacement witness",
            )
        self.assertEqual(before_choice, authoritative_state_hash(engine.state))

        source.temporary_keywords.append("indestructible")
        protected = prepare_destructions(
            engine,
            (request_for_card(source),),
            cause=DestructionCause.EFFECT,
            actor="B",
            reason="Indestructible witness",
        )
        self.assertEqual(
            DestructionDisposition.INDESTRUCTIBLE,
            protected.entries[0].disposition,
        )

    def test_regeneration_disposition_mutant_is_killed(self):
        session = self.session(7011906)
        engine = session.engine
        source = self.add_skeleton(session)
        create_regeneration_shield(
            engine,
            source.ref,
            actor="A",
            reason="disposition witness",
            logical_object_id=source.logical_object_id,
        )
        with patch(
            "quorune.destruction._destruction_disposition",
            return_value=DestructionDisposition.DESTROY,
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual(
                    DestructionDisposition.REGENERATION,
                    prepare_destructions(
                        engine,
                        (request_for_card(source),),
                        cause=DestructionCause.EFFECT,
                        actor="B",
                        reason="disposition mutation witness",
                    ).entries[0].disposition,
                )


if __name__ == "__main__":
    unittest.main()
