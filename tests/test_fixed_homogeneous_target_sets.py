from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler.fixed_homogeneous_target_sets import (
    FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
    FixedHomogeneousTargetSetTemplate,
    fixed_homogeneous_target_set_effect_template,
)
from quorune.deck import DeckLoader
from quorune.engine import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
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
from quorune.semantic_runtime import (
    DestroyPermanentTargetsIntent,
    execute_intent_plan,
    MoveObjectsSimultaneouslyIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
    SetPermanentsTappedIntent,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.fixed_target_set_handlers import (
    DestroyPermanentTargetSetHandler,
    FIXED_TARGET_SET_HANDLERS,
)
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-homogeneous-target-sets.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT
            / "tests"
            / "fixtures"
            / "fixed-homogeneous-target-set-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedHomogeneousTargetSetCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.base = cls.db.lookup("Sol Ring")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, text: str, *, type_line: str = "Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_homogeneous_target_sets_compile_across_contexts(self):
        cases = (
            (
                "Destroy two target enchantments.",
                "Sorcery",
                "spell_ability",
                "destroy_targets",
                {"count": 2},
            ),
            (
                "When Fixture enters, tap one or two target creatures.",
                "Artifact Creature — Construct",
                "triggered_ability",
                "tap_targets",
                {"min": 1, "max": 2},
            ),
            (
                "{T}: Untap up to four target artifacts and/or creatures.",
                "Artifact",
                "activated_ability",
                "untap_targets",
                {"up_to": 4},
            ),
            (
                "Return up to two target creature cards from your graveyard to your hand.",
                "Instant",
                "spell_ability",
                "return_graveyard_targets_to_owner_hand",
                {"up_to": 2},
            ),
            (
                "Exile up to three target cards from a single graveyard.",
                "Instant",
                "spell_ability",
                "exile_public_graveyard_targets",
                {"up_to": 3, "same_owner": True},
            ),
            (
                "Exile up to one target card from a graveyard.",
                "Instant",
                "spell_ability",
                "exile_public_graveyard_targets",
                {"up_to": 1},
            ),
            (
                "Exile up to one target creature.",
                "Instant",
                "spell_ability",
                "exile_permanent_targets",
                {"up_to": 1},
            ),
            (
                "Destroy up to one target creature.",
                "Instant",
                "spell_ability",
                "destroy_targets",
                {"up_to": 1},
            ),
            (
                "Return up to one target creature to its owner's hand.",
                "Instant",
                "spell_ability",
                "return_permanent_targets_to_owner_hand",
                {"up_to": 1},
            ),
            (
                "Return up to one target tapped creature an opponent controls "
                "to its owner's hand.",
                "Instant",
                "spell_ability",
                "return_permanent_targets_to_owner_hand",
                {"up_to": 1},
            ),
            (
                "Return up to one target permanent card from your graveyard "
                "to your hand.",
                "Sorcery",
                "spell_ability",
                "return_graveyard_targets_to_owner_hand",
                {"up_to": 1},
            ),
            (
                "Return up to one target card from your graveyard to your hand.",
                "Sorcery",
                "spell_ability",
                "return_graveyard_targets_to_owner_hand",
                {"up_to": 1},
            ),
            (
                "Return up to two target Goblin cards from your graveyard to "
                "your hand.",
                "Sorcery",
                "spell_ability",
                "return_graveyard_targets_to_owner_hand",
                {"up_to": 2},
            ),
            (
                "Tap up to two target creatures.",
                "Instant",
                "spell_ability",
                "tap_targets",
                {"up_to": 2},
            ),
        )
        for text, type_line, kind, operation, schema_fields in cases:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = next(
                    node
                    for node in ir.faces[0].nodes
                    if node.template_id
                    and "target-set" in node.template_id
                )
                self.assertEqual(kind, node.kind)
                self.assertEqual(operation, node.effects[0]["op"])
                self.assertEqual("$targets", node.effects[0]["cards"])
                self.assertTrue(
                    schema_fields.items() <= node.target_schema.items()
                )
                self.assertIn(
                    FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
                    node.capability_dependencies,
                )
                self.assertIn(
                    "target.revalidate_resolution",
                    node.capability_dependencies,
                )
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_fixed_homogeneous_target_set_grammar_stays_closed(self):
        unsupported = (
            "Destroy target creature and target land.",
            "Exile up to X target cards from a graveyard.",
            "Return two target creatures to their owners' hands, then draw a card.",
            "Return up to three target creature cards with total mana value 3 or less from your graveyard to the battlefield.",
            "Destroy two targets.",
            "Tap seven target creatures.",
            "Exile two target cards from your hand.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_homogeneous_target_set_effect_template(text)
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_target_set_shape_and_handler_mutants_fail_closed(self):
        template = fixed_homogeneous_target_set_effect_template(
            "Destroy three target artifacts."
        )
        self.assertIsInstance(template, FixedHomogeneousTargetSetTemplate)
        assert template is not None
        compiled_mechanics = template.compiled()[3]
        dependencies = set(
            capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=compiled_mechanics,
            )
        )
        self.assertTrue(
            {
                FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
                "permanent.destroy.effect",
                "target.revalidate_resolution",
            }.issubset(dependencies)
        )
        for effect in (
            {**template.effects[0], "cards": "$target.0"},
            {**template.effects[0], "maximum_targets": True},
            {**template.effects[0], "maximum_targets": 7},
            {**template.effects[0], "op": "destroy"},
            {**template.effects[0], "arbitrary": True},
        ):
            with self.subTest(effect=effect):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=template.target_schema,
                        mechanic_ids=compiled_mechanics,
                    )
                )
        for schema in (
            {**template.target_schema, "count": 1},
            {**template.target_schema, "count": 7},
            {**template.target_schema, "same_owner": True},
            {**template.target_schema, "unknown": True},
        ):
            with self.subTest(schema=schema):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=template.effects,
                        target_schema=schema,
                        mechanic_ids=compiled_mechanics,
                    )
                )

        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="target-set fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("A", "B", "C", "D"),
            ),
        )
        destruction = DestroyPermanentTargetSetHandler().lower(
            {
                "op": "destroy_targets",
                "cards": ["B01", "C01"],
                "maximum_targets": 2,
            },
            context,
        )
        self.assertIsInstance(
            destruction.intents[0], DestroyPermanentTargetsIntent
        )
        move = next(
            handler
            for handler in FIXED_TARGET_SET_HANDLERS
            if handler.operation == "exile_public_graveyard_targets"
        ).lower(
            {
                "op": "exile_public_graveyard_targets",
                "cards": ["B02", "B03"],
                "maximum_targets": 3,
            },
            context,
        )
        self.assertIsInstance(
            move.intents[0], MoveObjectsSimultaneouslyIntent
        )
        tap = next(
            handler
            for handler in FIXED_TARGET_SET_HANDLERS
            if handler.operation == "tap_targets"
        ).lower(
            {
                "op": "tap_targets",
                "cards": ["B04", "C04"],
                "maximum_targets": 2,
            },
            context,
        )
        self.assertTrue(
            all(
                isinstance(intent, SetPermanentsTappedIntent)
                for intent in tap.intents
            )
        )
        self.assertNotIn(
            "same_owner",
            TargetGroup.from_mapping({"count": 1}).public_dict([]),
        )
        with self.assertRaises(ValueError):
            TargetGroup.from_mapping(
                {"count": 2, "same_owner": "false"}
            )
        for malformed in (
            {
                "op": "destroy_targets",
                "cards": ["B01", "B01"],
                "maximum_targets": 2,
            },
            {
                "op": "destroy_targets",
                "cards": ["B01", "B02", "B03"],
                "maximum_targets": 2,
            },
            {
                "op": "destroy_targets",
                "cards": "$targets",
                "maximum_targets": 2,
            },
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(SemanticNodeError):
                    DestroyPermanentTargetSetHandler().lower(
                        malformed, context
                    )

        for dependency_id in (
            FIXED_HOMOGENEOUS_TARGET_SET_CAPABILITY,
            "target.revalidate_resolution",
        ):
            value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in value["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            ir = compile_oracle_card(
                replace(
                    self.base,
                    name="Fixture",
                    oracle_text="Destroy three target artifacts.",
                    type_line="Sorcery",
                    keywords=(),
                    faces=(),
                ),
                capability_registry=CapabilityRegistry(value),
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(ir.material_residuals)

        def exact() -> None:
            self.assertEqual(
                "exact",
                self.compile("Destroy three target artifacts.").status,
            )

        exact()
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_homogeneous_target_set_effect_template",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                exact()


class FixedHomogeneousTargetSetRuntimeTests(unittest.TestCase):
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

    def session(self, seed: int, *, spell: str | None = None):
        mishra = copy.deepcopy(self.mishra)
        if spell is not None:
            next(
                entry for entry in mishra.entries if entry.board == "mainboard"
            ).name = spell
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
            players=4,
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
    def card(engine, seat: str, *, name: str | None = None, exclude=()):
        excluded = set(exclude)
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.object_id not in excluded
            and card.zone != "command"
            and card.is_card_object
            and (name is None or card.printed_name == name)
        )

    @staticmethod
    def creature(engine, seat: str, *, exclude=()):
        excluded = set(exclude)
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.object_id not in excluded
            and card.zone != "command"
            and card.is_card_object
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )

    def ready_spell(self, session, name: str, mana: dict[str, int]):
        engine = session.engine
        source = self.card(engine, "A", name=name)
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update(mana)
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        engine._issue_priority("A", hints)
        return source, action

    def resolve_all(self, session):
        for _ in range(100):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "replacement.order":
                principal = session.pending_principals()[0]
                projected = StateProjector(self.db, session.state)._decision(
                    principal
                )
                assert projected is not None
                selected = next(
                    option
                    for option in projected["ctx"]["options"]
                    if option.get("decline")
                )
                result = session.act(
                    principal,
                    {"action_id": "choose", "replacement": selected["id"]},
                )
                self.assertTrue(result.ok, result.summary)
                continue
            if session.state.stack:
                principals = session.pending_principals()
                self.assertTrue(principals)
                result = session.act(principals[0], {"action_id": "pass"})
                self.assertTrue(result.ok, result.summary)
                continue
            if decision is None or decision.kind == "priority":
                return
            raise AssertionError(f"Unexpected pending decision {decision.kind}")
        self.fail("Resolution did not stabilize")

    def assert_replays(self, session, label: str):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / label
            session.save(output)
            replay = replay_record(output, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_four_player_same_graveyard_offer_resolution_and_replay(self):
        session = self.session(122001, spell="Homogeneous Grave Exile")
        engine = session.engine
        b_first = self.card(engine, "B")
        b_second = self.card(engine, "B", exclude=(b_first.object_id,))
        c_card = self.card(engine, "C")
        for card in (b_first, b_second, c_card):
            engine.move_card(card.object_id, "graveyard", log=False)
        source, action = self.ready_spell(
            session, "Homogeneous Grave Exile", {"B": 1}
        )
        group = action["target_schema"]["groups"][0]
        self.assertTrue(group["same_owner"])
        self.assertTrue(
            {b_first.ref, b_second.ref, c_card.ref}.issubset(
                group["legal_refs"]
            )
        )
        program = engine.semantics.get(
            f"{source.oracle_id}:spell:front"
        )
        with self.assertRaises(GameRuleError):
            engine._validate_semantic_targets(
                "A",
                program,
                [b_first.ref, c_card.ref],
                source_ref=source.ref,
            )
        self.assertEqual("hand", source.zone)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [b_first.ref, b_second.ref],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_all(session)
        self.assertEqual("exile", b_first.zone)
        self.assertEqual("exile", b_second.zone)
        self.assertEqual("graveyard", c_card.zone)
        self.assertEqual("graveyard", source.zone)
        for seat in ("A", "B", "C", "D"):
            rendered = json.dumps(
                StateProjector(self.db, engine.state)._snapshot(
                    f"pilot:{seat}"
                ),
                sort_keys=True,
            )
            self.assertNotIn("logical_object_id", rendered)
            self.assertNotIn(b_first.object_id, rendered)
            self.assertNotIn(b_second.object_id, rendered)
        self.assert_replays(session, "same-graveyard-target-set")

    def test_exact_same_graveyard_offer_requires_a_real_pair(self):
        session = self.session(122002)
        engine = session.engine
        b_first = self.card(engine, "B")
        c_card = self.card(engine, "C")
        engine.move_card(b_first.object_id, "graveyard", log=False)
        engine.move_card(c_card.object_id, "graveyard", log=False)
        template = fixed_homogeneous_target_set_effect_template(
            "Exile two target cards from a single graveyard."
        )
        assert template is not None
        self.assertIsNone(
            engine._public_target_schema(
                "A", template.target_schema, source_ref="S-fixture"
            )
        )
        b_second = self.card(engine, "B", exclude=(b_first.object_id,))
        engine.move_card(b_second.object_id, "graveyard", log=False)
        advertised = engine._public_target_schema(
            "A", template.target_schema, source_ref="S-fixture"
        )
        self.assertIsNotNone(advertised)
        assert advertised is not None
        self.assertTrue(advertised["groups"][0]["same_owner"])

    def test_partial_illegality_resolves_surviving_target_atomically(self):
        session = self.session(122003, spell="Homogeneous Return")
        engine = session.engine
        first = self.creature(engine, "B")
        second = self.creature(engine, "C")
        for card in (first, second):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller=card.owner,
                log=False,
            )
        _source, action = self.ready_spell(
            session, "Homogeneous Return", {"C": 1, "U": 1}
        )
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [first.ref, second.ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        engine.move_card(first.object_id, "graveyard", log=False)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", first.zone)
        self.assertEqual("hand", second.zone)
        self.assertTrue(
            any(event.code == "target.illegal" for event in engine.state.events)
        )

    def test_tap_and_untap_sets_reuse_scalar_stun_owner(self):
        session = self.session(122005)
        engine = session.engine
        first = self.card(engine, "B", name="Sol Ring")
        second = self.card(engine, "C", name="Panharmonicon")
        for card in (first, second):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller=card.owner,
                log=False,
            )
            card.tapped = False
        context = ReadOnlyHandlerContext.from_sequences(
            actor="A",
            default_reason="target-set tap-state fixture",
            seats=("A", "B", "C", "D"),
            active_seats=("A", "B", "C", "D"),
            apnap_order=("A", "B", "C", "D"),
        )
        refs = [first.ref, second.ref]
        tap_handler = next(
            handler
            for handler in FIXED_TARGET_SET_HANDLERS
            if handler.operation == "tap_targets"
        )
        execute_intent_plan(
            engine,
            tap_handler.lower(
                {
                    "op": "tap_targets",
                    "cards": refs,
                    "maximum_targets": 2,
                },
                context,
            ),
        )
        self.assertTrue(first.tapped)
        self.assertTrue(second.tapped)

        first.counters["stun"] = 1
        untap_handler = next(
            handler
            for handler in FIXED_TARGET_SET_HANDLERS
            if handler.operation == "untap_targets"
        )
        execute_intent_plan(
            engine,
            untap_handler.lower(
                {
                    "op": "untap_targets",
                    "cards": refs,
                    "maximum_targets": 2,
                },
                context,
            ),
        )
        self.assertTrue(first.tapped)
        self.assertNotIn("stun", first.counters)
        self.assertFalse(second.tapped)

    def test_wintermoon_mesa_composes_tapped_entry_with_fixed_target_set(self):
        session = self.session(122006, spell="Wintermoon Mesa")
        engine = session.engine
        source = self.card(engine, "A", name="Wintermoon Mesa")
        first = self.card(engine, "B", name="Island")
        second = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "C"
            and card.zone != "command"
            and card.is_card_object
            and "land"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        for target in (first, second):
            engine.move_card(
                target.object_id,
                "battlefield",
                controller=target.owner,
                log=False,
            )
            target.tapped = False

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertTrue(source.tapped)
        source.tapped = False
        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")

        action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("id") == f"activate:{source.ref}:ab3"
        )
        self.assertEqual(2, action["target_schema"]["count"])
        self.assertTrue(
            {first.ref, second.ref}.issubset(
                action["target_schema"]["legal_refs"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [first.ref, second.ref],
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", source.zone)
        self.resolve_all(session)
        self.assertTrue(first.tapped)
        self.assertTrue(second.tapped)
        self.assert_replays(session, "wintermoon-mesa-target-set")

    def test_multi_target_destruction_preserves_protections_and_replay(self):
        session = self.session(122004, spell="Homogeneous Ruin")
        engine = session.engine
        ordinary = self.card(engine, "C", name="Panharmonicon")
        shielded = self.card(engine, "B", name="Sol Ring")
        indestructible = self.card(engine, "C", name="Darksteel Citadel")
        for card in (ordinary, shielded, indestructible):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="B",
                log=False,
            )
        shielded.counters["shield"] = 1
        source, action = self.ready_spell(
            session, "Homogeneous Ruin", {"C": 1, "B": 1}
        )
        self.assertTrue(
            {ordinary.ref, shielded.ref, indestructible.ref}.issubset(
                action["target_schema"]["legal_refs"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [ordinary.ref, shielded.ref, indestructible.ref],
                "pay": "manual",
                "payment": {"C": 1, "B": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_all(session)
        self.assertEqual("graveyard", ordinary.zone)
        self.assertEqual("battlefield", shielded.zone)
        self.assertNotIn("shield", shielded.counters)
        self.assertEqual("battlefield", indestructible.zone)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "multi-target-destruction")


class EmptyFixedTargetBatchRuntimeTests(unittest.TestCase):
    """CR115.6/608.2b/c: zero chosen targets differ from all chosen illegal."""
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _Controls
    register_fixture_cards_on_add = True
    session = _Controls.session
    add = _Controls.add
    ready = _Controls.ready
    checkpoint = _Controls.checkpoint
    resolve = _Controls.resolve
    replay = _Controls.replay

    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / 'empty-targets.sqlite3'
        build_fixture_database([ROOT/'tests/fixtures/bound-effect-program-cards.json',
            ROOT/'tests/fixtures/empty-fixed-target-batch-cards.json'], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition('Empty target witnesses', [DeckEntry('Generic Bound Commander',1,'commander'),
            DeckEntry('Generic Bound Plains',30)], ['Generic Bound Commander'])

    @classmethod
    def tearDownClass(cls):
        cls.db.close(); cls.temporary.cleanup()

    def assert_admitted(self, session, source):
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.card_programs import bind_card_program_runtime
        program = compile_best_available_card_program(self.db, self.db.by_oracle_id(source.oracle_id),
            semantic_registry=session.engine.semantics, capability_registry=self.registry,
            capability_profile='commander_review')
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile='commander_review')
        self.assertTrue(bound['strict_capability_ready'], bound['blockers'])

    def assert_private_replay(self, session, private):
        for viewer in 'ABD':
            self.assertNotIn(private.ref, json.dumps(session.packet('pilot:'+viewer, full=True)))
        self.replay(session, load=True)

    def test_zero_target_zone_instruction_continues_to_independent_private_draw(self):
        session = self.session(729301)
        source = self.add(session.engine, 'Generic Empty Exile Draw', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assert_admitted(session, source)
        action = self.ready(session, source, {'C':1}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        rejected = session.act('pilot:B', {'action_id':action['id'], 'targets':[], 'pay':'auto'})
        self.assertFalse(rejected.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act('pilot:A', {'action_id':action['id'], 'targets':[], 'pay':'auto'})
        self.assertTrue(result.ok, result.summary)
        hand = len(session.state.players['A'].zones['hand'])
        library = len(session.state.players['A'].zones['library'])
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+1, len(session.state.players['A'].zones['hand']))
        self.assertEqual(library-1, len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assert_private_replay(session, private)

    def test_all_existing_homogeneous_operations_resolve_zero_targets_without_object_mutation(self):
        for kind in ('Destruction','Permanent Exile','Permanent Return','Graveyard Return',
                'Graveyard Exile','Tap','Untap'):
            session = self.session(729302)
            source = self.add(session.engine, 'Generic Empty '+kind, zone='hand')
            body = self.add(session.engine, 'Generic Bound Body', seat='B')
            body.tapped = kind == 'Untap'
            private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
            self.assert_admitted(session, source)
            action = self.ready(session, source, {'C':1}); self.checkpoint(session)
            result = session.act('pilot:A', {'action_id':action['id'], 'targets':[], 'pay':'auto'})
            self.assertTrue(result.ok, result.summary)
            session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
            self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
            self.assertEqual('battlefield', session.state.cards[body.object_id].zone)
            self.assertEqual(kind == 'Untap', session.state.cards[body.object_id].tapped)
            self.assert_private_replay(session, private)

    def test_nonempty_graveyard_targets_still_move_together_and_reject_mixed_owners(self):
        session = self.session(729303)
        source = self.add(session.engine, 'Generic Empty Graveyard Exile', zone='hand')
        cards = [self.add(session.engine, 'Generic Bound Body', seat='B', zone='graveyard', ref='empty-control-'+str(i)) for i in range(2)]
        other = self.add(session.engine, 'Generic Bound Body', seat='D', zone='graveyard', ref='empty-foreign')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assert_admitted(session, source)
        action = self.ready(session, source, {'C':1}); self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        for refs in ([cards[0].ref]*2, [cards[0].ref,other.ref], [private.ref]):
            denied = session.act('pilot:A', {'action_id':action['id'], 'targets':refs, 'pay':'auto'})
            self.assertFalse(denied.ok); self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':[c.ref for c in cards], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertTrue(all(session.state.cards[c.object_id].zone=='exile' for c in cards))
        self.assertEqual('graveyard', session.state.cards[other.object_id].zone)
        self.assert_private_replay(session, private)

    def test_empty_and_duplicate_ordinary_zone_snapshots_remain_invalid(self):
        from quorune.semantic_runtime.zone_replacements import capture_zone_change_replacement_snapshot
        from quorune.semantic_runtime.zone_replacement_model import ZoneReplacementError
        session = self.session(729304)
        source = self.add(session.engine, 'Generic Empty Graveyard Exile', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body', seat='B')
        self.ready(session, source, {'C':1})
        before = authoritative_state_hash(session.state)
        snapshot = capture_zone_change_replacement_snapshot(session.engine, [(body.object_id,'graveyard')])
        for subjects in ((), (snapshot.subjects[0],snapshot.subjects[0])):
            with self.assertRaisesRegex(ZoneReplacementError, 'unique affected objects'):
                replace(snapshot, subjects=subjects)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_zero_target_activation_keeps_source_payment_and_later_private_draw(self):
        session = self.session(729305)
        source = self.add(session.engine, 'Generic Empty Activation')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        self.assert_admitted(session, source)
        action = self.ready(session, source, {'C':1}); self.checkpoint(session)
        self.assertTrue(action['id'].startswith('activate:'+source.ref+':'))
        hand = len(session.state.players['A'].zones['hand'])
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':[], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual(hand+1, len(session.state.players['A'].zones['hand']))
        self.assertEqual('battlefield', session.state.cards[source.object_id].zone)
        self.assertTrue(session.state.cards[source.object_id].tapped)
        self.assert_private_replay(session, private)

    def test_all_actually_chosen_targets_becoming_illegal_still_stops_the_later_draw(self):
        session = self.session(729306)
        source = self.add(session.engine, 'Generic Empty Destroy Draw', zone='hand')
        body = self.add(session.engine, 'Generic Bound Body', seat='B')
        murder = self.add(session.engine, 'Murder', seat='C', zone='hand')
        private = self.add(session.engine, 'Generic Bound Growth', seat='C', zone='hand')
        session.state.players['C'].mana_pool.update({'B':2,'C':1})
        for card in (source,murder):self.assert_admitted(session, card)
        action = self.ready(session, source, {'C':1}); self.checkpoint(session)
        accepted = session.act('pilot:A', {'action_id':action['id'], 'targets':[body.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        hand = len(session.state.players['A'].zones['hand'])
        library = len(session.state.players['A'].zones['library'])
        for _ in range(8):
            if session.pending_principals()[0]=='pilot:C':break
            result = session.act(session.pending_principals()[0], {'action_id':'pass'})
            self.assertTrue(result.ok, result.summary)
        actions = session.packet('pilot:C', full=True)['decision']['ctx']['legal']['actions']
        reply = next(row for row in actions if row.get('card')==murder.ref)
        accepted = session.act('pilot:C', {'action_id':reply['id'], 'targets':[body.ref], 'pay':'auto'})
        self.assertTrue(accepted.ok, accepted.summary)
        session = self.replay(session, load=True); self.assertIsNone(self.resolve(session))
        self.assertEqual(hand, len(session.state.players['A'].zones['hand']))
        self.assertEqual(library, len(session.state.players['A'].zones['library']))
        self.assertEqual('graveyard', session.state.cards[source.object_id].zone)
        self.assertEqual('graveyard', session.state.cards[body.object_id].zone)
        self.assertEqual(0, sum(session.state.players['A'].mana_pool.values()))
        self.assert_private_replay(session, private)

    def test_empty_move_intent_mutation_is_killed_by_fresh_native_zero_target_draw(self):
        from quorune.semantic_runtime.fixed_target_set_handlers import MoveFixedTargetSetHandler
        original = MoveFixedTargetSetHandler.lower
        def restore_empty_move(handler, effect, context):
            plan = original(handler, effect, context)
            if effect.get('cards') == []:
                from quorune.semantic_runtime.intents import MoveObjectsSimultaneouslyIntent
                return replace(plan, result_shape='single', intents=(MoveObjectsSimultaneouslyIntent(actor=context.actor,
                    object_refs=(), expected_zones=(handler.expected_zone,), destination=handler.destination,
                    reason=context.default_reason, owned_only=handler.owned_only, replacement_selections=()),))
            return plan
        with patch.object(MoveFixedTargetSetHandler, 'lower', new=restore_empty_move):
            method = 'test_zero_target_zone_instruction_continues_to_independent_private_draw'
            with self.assertRaises(AssertionError): getattr(type(self)(method), method)()


class EmptyFixedTargetBatchModelTests(unittest.TestCase):
    def test_zero_target_lowering_has_no_mutations_and_nonempty_lowering_retains_one_intent(self):
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Empty target contract',
            seats='ABCD', active_seats='ABCD', apnap_order='ABCD')
        for handler in FIXED_TARGET_SET_HANDLERS:
            for refs in ([], ['B1']):
                effect = {'op':handler.operation, 'cards':refs, 'maximum_targets':2}
                plan = handler.lower(effect, context)
                self.assertEqual(int(bool(refs)), len(plan.intents), handler.operation)
                self.assertEqual(handler.operation, plan.operation)

    def test_empty_target_set_does_not_skip_descriptor_validation(self):
        context = ReadOnlyHandlerContext.from_sequences(actor='A', default_reason='Empty target contract',
            seats='ABCD', active_seats='ABCD', apnap_order='ABCD')
        for handler in FIXED_TARGET_SET_HANDLERS:
            source = {'op':handler.operation, 'cards':[], 'maximum_targets':2}
            for change in ({'unknown':1}, {'op':'invented'}, {'maximum_targets':True},
                    {'maximum_targets':0}, {'cards':['B1','B1']}, {'reason':False}):
                with self.assertRaises(SemanticNodeError): handler.lower({**source,**change}, context)
            for refs in ([], ['B1']):
                for invalid in (123, None, True):
                    with self.assertRaises(ValueError):
                        handler.lower({**source, 'cards':refs, '_replacement_selections':[invalid]}, context)


if __name__ == "__main__":
    unittest.main()
