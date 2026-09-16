from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.ability_fragments import StaticComponentSpec
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckLoader
from quorune.engine import TURN_STEPS
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "class-lifecycle-cards.json"
CAPABILITY = "permanent.class.fixed_lifecycle"
CLASS_ACTIVATION_HANDLER = "ability.activated.class-level.v1"
STATIC_SCOPE_HANDLER = "ability.static.component-scope.v1"
REMINDER_TEMPLATE = "class-lifecycle-reminder-v1"
ACTIVATION_TEMPLATE = "class-level-activation-v1"
SCOPE_TEMPLATE = "class-level-static-scope-v1"


def trusted_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(REGISTRY_PATH)
    registry.mark_evidence_verified("0" * 64)
    return registry


def class_record(
    oracle_text: str,
    *,
    name: str = "Constructed Class",
    layout: str = "class",
    type_line: str = "Enchantment — Class",
    keywords: tuple[str, ...] = ("Hexproof", "Lifelink", "Vigilance"),
) -> CardRecord:
    return CardRecord(
        oracle_id=f"fixture:{name.casefold().replace(' ', '-')}",
        name=name,
        mana_cost="{1}{W}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("W",),
        color_identity=("W",),
        keywords=keywords,
        produced_mana=(),
        layout=layout,
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


CLASS_TEXT = (
    "(Gain the next level as a sorcery to add its ability.)\n"
    "Hexproof\n"
    "{1}{W}: Level 2\n"
    "Lifelink\n"
    "{2}{W}: Level 3\n"
    "Vigilance"
)
SCHOLAR_CLASS_TEXT = (
    "(Gain the next level as a sorcery to add its ability.)\n"
    "You have no maximum hand size.\n"
    "{1}{U}: Level 2\n"
    "When this Class becomes level 2, draw two cards.\n"
    "{2}{U}: Level 3\n"
    "Vigilance"
)


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "class-lifecycle.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class ClassLifecycleCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry_value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        cls.capabilities = trusted_registry()

    def compile(self, record: CardRecord, *, registry=None):
        return compile_oracle_card(
            record,
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_class_structure_compiles_three_lifecycle_nodes_and_scoped_children(self):
        compiled = self.compile(class_record(CLASS_TEXT))

        self.assertEqual("exact", compiled.status, compiled.to_dict())
        nodes = compiled.faces[0].nodes
        lifecycle = [
            node
            for node in nodes
            if node.template_id
            in {REMINDER_TEMPLATE, ACTIVATION_TEMPLATE, SCOPE_TEMPLATE}
        ]
        self.assertEqual(5, len(lifecycle))
        reminder = next(node for node in lifecycle if node.template_id == REMINDER_TEMPLATE)
        self.assertEqual("reminder_text", reminder.kind)
        self.assertEqual("none", reminder.event)
        level_nodes = [node for node in lifecycle if node.template_id == ACTIVATION_TEMPLATE]
        self.assertEqual([2, 3], [node.effects[0]["level"] for node in level_nodes])
        self.assertEqual(
            ["$source.zone_object", "$source.zone_object"],
            [node.effects[0]["card"] for node in level_nodes],
        )
        self.assertEqual(
            [{"GENERIC": 1, "W": 1, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0},
             {"GENERIC": 2, "W": 1, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}],
            [node.cost["mana"] for node in level_nodes],
        )
        for node in level_nodes:
            self.assertIn(CAPABILITY, node.capability_dependencies)
            self.assertIn("current_ability_fragment_required", node.runtime_coverage)
            handler_ids = {handler["handler_id"] for handler in node.handlers}
            self.assertEqual({CLASS_ACTIVATION_HANDLER}, handler_ids)
        scope_nodes = [
            node for node in lifecycle if node.template_id == SCOPE_TEMPLATE
        ]
        self.assertEqual(2, len(scope_nodes))
        for level, node in zip((2, 3), scope_nodes, strict=True):
            self.assertEqual(
                {STATIC_SCOPE_HANDLER},
                {handler["handler_id"] for handler in node.handlers},
            )
            scope = next(
                handler["fragment"]["value"]
                for handler in node.handlers
                if handler["handler_id"] == STATIC_SCOPE_HANDLER
            )
            self.assertEqual(
                {
                    "kind": "source_numeric_designation_at_least",
                    "designation": "class_level",
                    "minimum": level,
                },
                scope["applicability"],
            )
            self.assertTrue(scope["child_semantic_keys"])

    def test_class_grammar_exclusions_remain_residual(self):
        variants = (
            class_record(CLASS_TEXT, layout="normal"),
            class_record(CLASS_TEXT, type_line="Enchantment — Room"),
            class_record(CLASS_TEXT.replace("Gain the next level", "Advance")),
            class_record(CLASS_TEXT.replace("{1}{W}: Level 2", "{X}{W}: Level 2")),
            class_record(CLASS_TEXT.replace("{1}{W}: Level 2", "Pay 2 life: Level 2")),
            class_record(CLASS_TEXT.replace("Level 2", "Level 3", 1)),
            class_record(CLASS_TEXT.replace("{2}{W}: Level 3\nVigilance", "")),
            class_record(CLASS_TEXT + "\n{3}{W}: Level 4\nTrample"),
            class_record(
                "Level up {1}{W}\nLEVEL 1-2\n2/2\nLEVEL 3+\n3/3",
                layout="leveler",
                type_line="Creature — Human",
                keywords=("Level Up",),
            ),
            class_record(
                "Prototype {1}{W} — 2/2",
                layout="prototype",
                type_line="Artifact Creature — Construct",
                keywords=("Prototype",),
            ),
        )
        for record in variants:
            with self.subTest(layout=record.layout, type_line=record.type_line):
                compiled = self.compile(record)
                self.assertFalse(
                    any(
                        node.template_id
                        in {REMINDER_TEMPLATE, ACTIVATION_TEMPLATE, SCOPE_TEMPLATE}
                        for face in compiled.faces
                        for node in face.nodes
                    )
                )
                if record.layout != "leveler":
                    self.assertNotEqual("exact", compiled.status)
                    self.assertTrue(compiled.material_residuals)

    def test_class_level_trigger_and_no_maximum_hand_size_compile_exactly(self):
        compiled = self.compile(
            class_record(
                SCHOLAR_CLASS_TEXT,
                name="Constructed Scholar Class",
                keywords=("Vigilance",),
            )
        )
        self.assertEqual("exact", compiled.status, compiled.to_dict())
        nodes = compiled.faces[0].nodes
        self.assertTrue(
            any(node.template_id == "no-maximum-hand-size-v1" for node in nodes)
        )
        trigger = next(
            node
            for node in nodes
            if node.template_id == "class-level-change-trigger-v1"
        )
        self.assertEqual("permanent.class_level_changed.self", trigger.event)
        self.assertEqual(
            {"field": "level", "op": "eq", "value": 2},
            trigger.event_condition,
        )
        self.assertEqual(
            (
                {
                    "op": "draw",
                    "player": "$controller",
                    "count": 2,
                    "private": True,
                },
            ),
            trigger.effects,
        )
        self.assertIn("current_ability_fragment_required", trigger.runtime_coverage)
        for unsupported in (
            SCHOLAR_CLASS_TEXT.replace(
                "You have no maximum hand size.",
                "Your maximum hand size is increased by two.",
            ),
            SCHOLAR_CLASS_TEXT.replace(
                "You have no maximum hand size.",
                "You have no maximum hand size during your turn.",
            ),
            SCHOLAR_CLASS_TEXT.replace(
                "When this Class becomes level 2",
                "Whenever this Class becomes level 2",
            ),
            SCHOLAR_CLASS_TEXT.replace(
                "When this Class becomes level 2",
                "When this Class becomes level 4",
            ),
        ):
            with self.subTest(unsupported=unsupported):
                rejected = self.compile(
                    class_record(
                        unsupported,
                        name="Unsupported Scholar Class",
                        keywords=("Vigilance",),
                    )
                )
                self.assertNotEqual("exact", rejected.status)

    def test_class_capability_and_compiler_mutations_fail_closed(self):
        value = deepcopy(self.registry_value)
        capability = next(row for row in value["capabilities"] if row["id"] == CAPABILITY)
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation witness"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        blocked = self.compile(class_record(CLASS_TEXT), registry=registry)
        self.assertNotEqual("exact", blocked.status)

        def assert_exact() -> None:
            self.assertEqual("exact", self.compile(class_record(CLASS_TEXT)).status)

        assert_exact()
        with mock.patch(
            "quorune.oracle_ir._class_context.parse_class_context",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class ClassLifecycleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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
        cls.capabilities = trusted_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
            auto_pass_empty=False,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        session.engine.state.stack.clear()
        session.engine.state.pending_trigger_batches.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_class(
        self,
        session,
        *,
        ref: str = "A-class",
        name: str = "Generic Training Class",
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine._next_zone_timestamp(),
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["battlefield"].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return card

    @staticmethod
    def prepare_priority(session, *, mana: int = 8) -> None:
        engine = session.engine
        for symbol in ("B", "C", "G", "R", "U", "W"):
            engine.state.players["A"].mana_pool[symbol] = mana
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()

    @staticmethod
    def class_abilities(engine, source: CardInstance):
        return {
            int(ability.effect_text.rsplit(" ", 1)[1]): ability
            for ability in engine._activated_abilities(source)
            if ability.effect_text.startswith("Level ")
        }

    @staticmethod
    def action_ids(session, *, seat: str = "A") -> set[str]:
        decision = session.packet(f"pilot:{seat}", full=True)["decision"]
        if decision is None:
            return set()
        return {
            row["id"]
            for row in decision["ctx"]["legal"]["actions"]
        }

    @staticmethod
    def resolve_top(engine) -> None:
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def pass_until_empty(session, *, limit: int = 48) -> None:
        for _ in range(limit):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            principals = session.pending_principals()
            if not principals:
                session.engine._grant_priority(session.state.active_player)
                principals = session.pending_principals()
            if not principals:
                raise AssertionError("Class resolution stopped without a decision")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Class resolution did not converge")

    def activate(self, session, source: CardInstance, level: int):
        ability = self.class_abilities(session.engine, source)[level]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)
        return action_id

    def test_class_offer_commit_and_sequential_stack_resolution(self):
        session = self.session(71601)
        source = self.add_class(session)
        self.prepare_priority(session)
        abilities = self.class_abilities(session.engine, source)
        level_2 = f"activate:{source.ref}:{abilities[2].ability_id}"
        level_3 = f"activate:{source.ref}:{abilities[3].ability_id}"
        self.assertIn(level_2, self.action_ids(session))
        self.assertNotIn(level_3, self.action_ids(session))
        self.assertIsNone(source.class_level)

        result = session.act("pilot:A", {"action_id": level_2})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(source.class_level)
        self.assertEqual(1, len(session.state.stack))
        self.resolve_top(session.engine)
        self.assertEqual(2, source.class_level)

        self.prepare_priority(session)
        self.assertNotIn(level_2, self.action_ids(session))
        self.assertIn(level_3, self.action_ids(session))
        self.activate(session, source, 3)
        self.resolve_top(session.engine)
        self.assertEqual(3, source.class_level)

    def test_class_insufficient_stale_and_removed_ability_roll_back(self):
        insufficient = self.session(71602)
        source = self.add_class(insufficient)
        self.prepare_priority(insufficient, mana=0)
        abilities = self.class_abilities(insufficient.engine, source)
        action_id = f"activate:{source.ref}:{abilities[2].ability_id}"
        self.assertNotIn(action_id, self.action_ids(insufficient))

        stale = self.session(71603)
        stale_source = self.add_class(stale)
        self.prepare_priority(stale)
        stale_ability = self.class_abilities(stale.engine, stale_source)[2]
        stale_action = f"activate:{stale_source.ref}:{stale_ability.ability_id}"
        self.assertIn(stale_action, self.action_ids(stale))
        pool = dict(stale.state.players["A"].mana_pool)
        stale_source.class_level = 2
        result = stale.act("pilot:A", {"action_id": stale_action})
        self.assertFalse(result.ok)
        self.assertEqual(pool, dict(stale.state.players["A"].mana_pool))
        self.assertFalse(stale.state.stack)

        removed = self.session(71604)
        removed_source = self.add_class(removed)
        self.prepare_priority(removed)
        removed_ability = self.class_abilities(removed.engine, removed_source)[2]
        removed_action = f"activate:{removed_source.ref}:{removed_ability.ability_id}"
        program = next(
            value
            for value in removed.engine.semantics.programs_for_oracle(
                removed_source.oracle_id,
                active_zone="battlefield",
                event="activate",
            )
            if value.ability_id == f"ability:{removed_ability.ability_id}"
        )
        commit_continuous_effect(
            removed.state,
            ContinuousEffect(
                effect_id="fixture:remove-class-level-2",
                source_id="fixture:remove-class-level-2",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed.engine._next_zone_timestamp(),
                operations=(
                    ContinuousOperation(
                        "remove_ability_fragment",
                        {
                            "kind": "static_component",
                            "value": StaticComponentSpec(program.key).to_dict(),
                        },
                    ),
                ),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                locked_objects=(
                    ContinuousObjectIdentity(
                        removed_source.object_id,
                        removed_source.logical_object_id,
                    ),
                ),
            ),
        )
        pool = dict(removed.state.players["A"].mana_pool)
        result = removed.act("pilot:A", {"action_id": removed_action})
        self.assertFalse(result.ok)
        self.assertEqual(pool, dict(removed.state.players["A"].mana_pool))
        self.assertFalse(removed.state.stack)

    def test_class_source_incarnation_counter_and_resolution_locking(self):
        countered = self.session(71605)
        source = self.add_class(countered)
        self.prepare_priority(countered)
        self.activate(countered, source, 2)
        countered.engine._counter_stack_item(countered.state.stack[-1].ref)
        self.assertIsNone(source.class_level)

        absent = self.session(71606)
        absent_source = self.add_class(absent)
        self.prepare_priority(absent)
        self.activate(absent, absent_source, 2)
        absent.engine.move_card(absent_source.object_id, "graveyard", semantic_events=True)
        self.resolve_top(absent.engine)
        self.assertIsNone(absent_source.class_level)

        returned = self.session(71607)
        returned_source = self.add_class(returned)
        self.prepare_priority(returned)
        self.activate(returned, returned_source, 2)
        old_identity = returned_source.logical_object_id
        returned.engine.move_card(returned_source.object_id, "graveyard", semantic_events=True)
        returned.engine.move_card(
            returned_source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertNotEqual(old_identity, returned_source.logical_object_id)
        self.resolve_top(returned.engine)
        self.assertIsNone(returned_source.class_level)

        locked = self.session(71608)
        locked_source = self.add_class(locked)
        self.prepare_priority(locked)
        self.activate(locked, locked_source, 2)
        for operation in (
            ContinuousOperation("set_types", ["Enchantment"]),
            ContinuousOperation("remove_all_abilities"),
        ):
            layer = Layer.TYPE if operation.op == "set_types" else Layer.ABILITY
            commit_continuous_effect(
                locked.state,
                ContinuousEffect(
                    effect_id=f"fixture:class-resolution:{operation.op}",
                    source_id=f"fixture:class-resolution:{operation.op}",
                    layer=layer,
                    sublayer="4" if layer == Layer.TYPE else "6",
                    timestamp=locked.engine._next_zone_timestamp(),
                    operations=(operation,),
                    origin=ContinuousEffectOrigin.RESOLUTION,
                    duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                    locked_objects=(
                        ContinuousObjectIdentity(
                            locked_source.object_id,
                            locked_source.logical_object_id,
                        ),
                    ),
                ),
            )
        self.resolve_top(locked.engine)
        self.assertEqual(2, locked_source.class_level)

    def test_class_level_gates_children_and_is_noncopiable_and_zone_scoped(self):
        session = self.session(71609)
        engine = session.engine
        source = self.add_class(session)
        self.assertEqual(["Hexproof"], engine._effective_card_data(source)["keywords"])
        source.class_level = 2
        self.assertEqual(
            ["Hexproof", "Lifelink"],
            engine._effective_card_data(source)["keywords"],
        )
        source.class_level = 3
        self.assertEqual(
            ["Hexproof", "Lifelink", "Vigilance"],
            engine._effective_card_data(source)["keywords"],
        )

        copied = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == "Goblin Engineer"
        )
        engine.move_card(copied.object_id, "battlefield", controller="A", log=False)
        copied.annotations["copy_overrides"] = engine._copyable_characteristics(source)
        self.assertIsNone(copied.class_level)
        self.assertEqual(["Hexproof"], engine._effective_card_data(copied)["keywords"])

        engine.change_control(source.object_id, "B", reason="Class control fixture")
        self.assertEqual(3, source.class_level)
        source.phased_out = True
        self.assertEqual(3, source.class_level)
        source.phased_out = False
        engine.move_card(source.object_id, "graveyard", semantic_events=True)
        self.assertIsNone(source.class_level)

    def test_class_save_load_replay_and_four_player_projection(self):
        session = self.session(71610, players=4)
        source = self.add_class(session)
        self.prepare_priority(session)
        ability = self.class_abilities(session.engine, source)[2]
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        for seat in "BCD":
            self.assertNotIn(action_id, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "class-lifecycle-record"
            session.save(directory)
            loaded = type(session).load(self.db, directory)
            self.assertIsNone(loaded.state.cards[source.object_id].class_level)
            self.pass_until_empty(loaded)
            loaded_source = loaded.state.cards[source.object_id]
            self.assertEqual(2, loaded_source.class_level)
            for seat in loaded.engine.seats:
                packet = loaded.packet(f"pilot:{seat}", full=True)
                self.assertIn('"class_level": 2', json.dumps(packet, sort_keys=True))
                self.assertNotIn(source.object_id, json.dumps(packet, sort_keys=True))
            expected_hash = authoritative_state_hash(loaded.state)
            loaded.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_class_level_trigger_and_no_maximum_hand_size_complete_generic_program(self):
        session = self.session(71613)
        source = self.add_class(
            session,
            name="Generic Scholar Class",
            ref="A-scholar-class",
        )
        self.prepare_priority(session)
        hand_before = len(session.state.players["A"].zones["hand"])
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()

        self.activate(session, source, 2)
        self.pass_until_empty(session)

        self.assertEqual(2, source.class_level)
        self.assertEqual(
            hand_before + 2,
            len(session.state.players["A"].zones["hand"]),
        )
        self.assertTrue(
            any(
                event.code == "permanent.class_level"
                and event.details.get("level") == 2
                for event in session.state.events
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "class-level-trigger-record"
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)

    def test_no_maximum_hand_size_component_removal_restores_cleanup_discard(self):
        def pending_discard(seed: int, *, remove_component: bool) -> int | None:
            session = self.session(seed)
            source = self.add_class(
                session,
                name="Generic Scholar Class",
                ref=f"A-scholar-{seed}",
            )
            engine = session.engine
            for _ in range(2):
                object_id = engine.state.players["A"].zones["library"][-1]
                engine.move_card(object_id, "hand", log=False)
            if remove_component:
                program = next(
                    value
                    for value in engine.semantics.programs_for_oracle(
                        source.oracle_id,
                        active_zone="battlefield",
                        event="characteristics.evaluate",
                    )
                    if value.provenance.get("template_id")
                    == "no-maximum-hand-size-v1"
                )
                commit_continuous_effect(
                    engine.state,
                    ContinuousEffect(
                        effect_id=f"fixture:remove-no-max:{seed}",
                        source_id=f"fixture:remove-no-max:{seed}",
                        layer=Layer.ABILITY,
                        sublayer="6",
                        timestamp=engine._next_zone_timestamp(),
                        operations=(
                            ContinuousOperation(
                                "remove_ability_fragment",
                                {
                                    "kind": "static_component",
                                    "value": StaticComponentSpec(
                                        program.key
                                    ).to_dict(),
                                },
                            ),
                        ),
                        origin=ContinuousEffectOrigin.RESOLUTION,
                        duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                        locked_objects=(
                            ContinuousObjectIdentity(
                                source.object_id,
                                source.logical_object_id,
                            ),
                        ),
                    ),
                )
            engine.state.phase_index = TURN_STEPS.index(("ending", "cleanup"))
            engine._enter_step()
            decision = engine.state.pending_decision
            return (
                int(decision.payload_by_actor["A"]["count"])
                if decision is not None and decision.kind == "cleanup.discard"
                else None
            )

        self.assertIsNone(pending_discard(71614, remove_component=False))
        self.assertEqual(2, pending_discard(71615, remove_component=True))
        with mock.patch(
            "quorune.engine.effective_maximum_hand_size",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                self.assertEqual(2, pending_discard(71616, remove_component=True))

    def test_class_designation_mutation_is_killed(self):
        def assert_advances(seed: int) -> None:
            session = self.session(seed)
            source = self.add_class(session)
            self.prepare_priority(session)
            self.activate(session, source, 2)
            self.resolve_top(session.engine)
            self.assertEqual(2, source.class_level)

        assert_advances(71611)
        with mock.patch(
            "quorune.effect_runtime.state_and_permissions.advance_class_level",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_advances(71612)


if __name__ == "__main__":
    unittest.main()
