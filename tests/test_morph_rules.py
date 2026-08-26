from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler import keyword_nodes as keyword_nodes_module
from quorune.compiled_morph import (
    compiled_fixed_mana_face_down_method_spec,
    compiled_fixed_mana_morph_spec,
)
from quorune.continuous_effect_model import Layer
from quorune.continuous_effect_state import (
    ResolutionEffectSource,
    create_resolution_continuous_effect,
)
from quorune.continuous_effects import ContinuousOperation
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.morph import (
    compile_fixed_mana_face_down_method,
    compile_fixed_mana_morph,
    DISGUISE_CAPABILITY_ID,
    DISGUISE_CAST_METHOD,
    DISGUISE_HANDLER_ID,
    FACE_DOWN_METHOD_RUNTIME_EVENTS,
    FixedManaMorphSpec,
    MEGAMORPH_CAPABILITY_ID,
    MEGAMORPH_CAST_METHOD,
    MEGAMORPH_HANDLER_ID,
    MORPH_CAPABILITY_ID,
    MORPH_HANDLER_ID,
    MorphError,
    morph_handler_descriptor,
)
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.projection import StateProjector
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.rules.casting.commit import commit_cast
from quorune.rules.casting.model import CastProposalError, CastProposalRequest
from quorune.rules.casting.proposal import build_cast_proposal
from quorune.rules.morph_actions import commit_turn_face_up
from quorune.semantic_runtime import SemanticNodeError
from quorune.semantic_runtime.morph import default_fixed_mana_morph_registry
from quorune.semantics import SemanticRegistry
from quorune.trigger_processing import collect_ward_occurrences
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "morph-rules-cards.json"
PARTIAL_FIXTURE_PATH = ROOT / "tests" / "fixtures" / "morph-partial-card.json"
METHOD_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "face-down-method-cards.json"
)


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "morph-rules.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            FIXTURE_PATH,
            PARTIAL_FIXTURE_PATH,
            METHOD_FIXTURE_PATH,
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "targeted-destruction-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class FixedManaMorphModelTests(unittest.TestCase):
    def test_fixed_mana_morph_descriptor_is_strict_and_registered(self):
        spec = compile_fixed_mana_morph("Morph {2}{W}.")
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(
            {
                "GENERIC": 2,
                "W": 1,
                "U": 0,
                "B": 0,
                "R": 0,
                "G": 0,
                "C": 0,
            },
            spec.requirements_dict,
        )
        self.assertEqual("{2}{W}", spec.cost_text)
        self.assertEqual(spec, FixedManaMorphSpec.from_dict(spec.to_dict()))
        self.assertEqual(
            (spec,),
            default_fixed_mana_morph_registry().lower(
                morph_handler_descriptor(spec),
                None,
            ),
        )

        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(MorphError, "closed shape"):
            FixedManaMorphSpec.from_dict(malformed)
        descriptor = morph_handler_descriptor(spec)
        descriptor["event"] = "activate"
        with self.assertRaises(SemanticNodeError):
            default_fixed_mana_morph_registry().validate(descriptor)

        for unsupported in (
            "Morph {X}{R}",
            "Morph {W/U}",
            "Morph—Pay 5 life.",
            "Megamorph {1}{U}",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(compile_fixed_mana_morph(unsupported))

    def test_fixed_face_down_method_descriptors_are_method_pinned(self):
        for line, method, handler_id in (
            ("Megamorph {5}{G}", MEGAMORPH_CAST_METHOD, MEGAMORPH_HANDLER_ID),
            ("Disguise {4}{W}.", DISGUISE_CAST_METHOD, DISGUISE_HANDLER_ID),
        ):
            with self.subTest(method=method):
                spec = compile_fixed_mana_face_down_method(line)
                self.assertIsNotNone(spec)
                assert spec is not None
                self.assertEqual(method, spec.method)
                descriptor = {
                    "handler_id": handler_id,
                    "schema_version": 1,
                    "event": FACE_DOWN_METHOD_RUNTIME_EVENTS[method],
                    "requires_complete_card_program": True,
                    "face_down_method": spec.to_dict(),
                }
                self.assertEqual(
                    (spec,),
                    default_fixed_mana_morph_registry().lower(
                        descriptor,
                        None,
                    ),
                )

        for unsupported in (
            "Megamorph {X}{G}",
            "Disguise {W/U}",
            "Disguise—Pay 2 life.",
        ):
            with self.subTest(unsupported=unsupported):
                self.assertIsNone(
                    compile_fixed_mana_face_down_method(unsupported)
                )


class FixedManaMorphCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, name: str, *, capabilities=None):
        return compile_oracle_card(
            self.db.lookup(name),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def morph_node(self, name: str = "Lumithread Field", *, capabilities=None):
        ir = self.compile(name, capabilities=capabilities)
        return ir, next(
            node
            for node in ir.faces[0].nodes
            if "morph" in node.mechanics
        )

    def method_node(self, name: str, method: str, *, capabilities=None):
        ir = self.compile(name, capabilities=capabilities)
        return ir, next(
            node
            for node in ir.faces[0].nodes
            if method in node.mechanics
        )

    def test_fixed_mana_morph_compiles_source_spanned_typed_program(self):
        record = self.db.lookup("Lumithread Field")
        ir, node = self.morph_node()

        self.assertTrue(node.exact, ir.material_residuals)
        self.assertEqual("keyword_ability", node.kind)
        self.assertEqual("all", node.active_zone)
        self.assertEqual("morph.action", node.event)
        self.assertEqual(
            "morph-fixed-mana-face-down-special-action-v1",
            node.template_id,
        )
        self.assertEqual((MORPH_CAPABILITY_ID,), node.capability_dependencies)
        self.assertEqual(MORPH_HANDLER_ID, node.handlers[0]["handler_id"])
        self.assertEqual(
            record.oracle_text[node.span.start : node.span.end],
            node.text,
        )

        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        program = next(
            program
            for program in registry.programs_for_oracle(record.oracle_id)
            if program.event == "morph.action"
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertTrue(
            registry.card_program_for_oracle(record.oracle_id).trust_closure[
                "trusted"
            ]
        )

    def test_nonordinary_morph_costs_remain_residual(self):
        variable_ir, variable = self.morph_node("Warbreak Trumpeter")
        self.assertFalse(variable.exact)
        self.assertTrue(variable.residual_ids)
        self.assertTrue(variable_ir.material_residuals)
        self.assertIsNone(variable.template_id)

        nonmana = self.compile("Gift of Doom")
        self.assertTrue(nonmana.material_residuals)
        self.assertFalse(
            any(
                node.template_id
                == "morph-fixed-mana-face-down-special-action-v1"
                for node in nonmana.faces[0].nodes
            )
        )

    def test_morph_dependency_fails_closed(self):
        value = deepcopy(self.registry_value)
        row = next(
            item
            for item in value["capabilities"]
            if item["id"] == MORPH_CAPABILITY_ID
        )
        row["status"] = "blocked"
        row["blockers"] = ["focused Morph dependency mutation"]
        _ir, node = self.morph_node(
            capabilities=CapabilityRegistry(value)
        )
        self.assertFalse(node.exact)
        self.assertTrue(node.residual_ids)

    def test_morph_compiler_mutation_is_killed(self):
        def assert_exact() -> None:
            _ir, node = self.morph_node()
            self.assertTrue(node.exact)
            self.assertEqual(MORPH_HANDLER_ID, node.handlers[0]["handler_id"])

        assert_exact()
        with mock.patch.object(
            keyword_nodes_module,
            "fixed_mana_morph_keyword_node",
            return_value=None,
        ):
            with self.assertRaises((AssertionError, StopIteration)):
                assert_exact()

    def test_fixed_face_down_methods_compile_and_fail_closed(self):
        for name, method, capability, handler in (
            (
                "Aerie Bowmasters",
                MEGAMORPH_CAST_METHOD,
                MEGAMORPH_CAPABILITY_ID,
                MEGAMORPH_HANDLER_ID,
            ),
            (
                "Defenestrated Phantom",
                DISGUISE_CAST_METHOD,
                DISGUISE_CAPABILITY_ID,
                DISGUISE_HANDLER_ID,
            ),
        ):
            with self.subTest(name=name):
                ir, node = self.method_node(name, method)
                self.assertTrue(node.exact, ir.material_residuals)
                self.assertEqual(
                    FACE_DOWN_METHOD_RUNTIME_EVENTS[method],
                    node.event,
                )
                self.assertEqual(
                    f"{method}-fixed-mana-face-down-special-action-v1",
                    node.template_id,
                )
                self.assertEqual((capability,), node.capability_dependencies)
                self.assertEqual(handler, node.handlers[0]["handler_id"])

                blocked = deepcopy(self.registry_value)
                row = next(
                    item
                    for item in blocked["capabilities"]
                    if item["id"] == capability
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused face-down dependency mutation"]
                _blocked_ir, blocked_node = self.method_node(
                    name,
                    method,
                    capabilities=CapabilityRegistry(blocked),
                )
                self.assertFalse(blocked_node.exact)
                self.assertTrue(blocked_node.residual_ids)

        trigger_ir = self.compile("Shieldhide Dragon")
        trigger = next(
            node
            for node in trigger_ir.faces[0].nodes
            if node.event == "permanent.turned_face_up"
        )
        self.assertTrue(trigger.exact, trigger_ir.material_residuals)
        self.assertEqual(
            {
                "field": "card",
                "op": "eq",
                "value": "$source.ref",
            },
            trigger.event_condition,
        )
        self.assertIn(
            "trigger-event-normalized-public-action",
            trigger.mechanics,
        )

        with mock.patch.object(
            keyword_nodes_module,
            "fixed_mana_morph_keyword_node",
            return_value=None,
        ):
            mutated = self.compile("Aerie Bowmasters")
        self.assertTrue(mutated.material_residuals)


class FixedManaMorphRuntimeTests(unittest.TestCase):
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, players: int = 4):
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
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        session,
        *,
        name: str,
        ref: str,
        zone: str = "hand",
        seat: str = "B",
    ):
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=[seat] if zone in {"hand", "library"} else list(engine.seats),
            revealed_to=[] if zone in {"hand", "library"} else list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=self.capabilities,
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_priority(session, seat: str = "B") -> None:
        engine = session.engine
        engine.state.active_player = seat
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.clear()
        engine.state.priority_passes = []
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority(seat)
        engine.pump()

    @staticmethod
    def resolve_spell_with_passes(session, card) -> None:
        for _ in range(12):
            if card.zone != "stack":
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Face-down spell did not resolve")

    @staticmethod
    def pass_until(session, predicate, *, limit: int = 48) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Resolution stopped without a decision")
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "trigger.order":
                controller = decision.actors[0]
                trigger_refs = [
                    item.ref
                    for batch in session.state.pending_trigger_batches
                    for item in batch.items
                    if item.controller == controller
                ]
                response = {
                    "action_id": "order",
                    "triggers": trigger_refs,
                }
            elif decision is not None and decision.kind == "arbiter.resolve":
                response = {"a": "resolve"}
            else:
                response = {"action_id": "pass"}
            result = session.act(principals[0], response)
            if not result.ok:
                raise AssertionError(
                    f"{result.summary}; principal={principals[0]}; "
                    f"decision={getattr(decision, 'kind', None)}"
                )
        raise AssertionError("Resolution did not converge")

    @staticmethod
    def choose_replacements(session, *, limit: int = 8) -> None:
        for _ in range(limit):
            decision = session.state.pending_decision
            if decision is None or decision.kind != "replacement.order":
                return
            seat = decision.actors[0]
            packet = StateProjector(
                session.engine.card_db,
                session.state,
            )._decision(f"pilot:{seat}")
            assert packet is not None
            selected = packet["ctx"]["options"][0]["id"]
            result = session.act(
                f"pilot:{seat}",
                {"a": "choose", "replacement": selected},
            )
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Replacement sequence did not converge")

    def cast_face_down(self, session, card, *, method: str) -> None:
        action = next(
            value
            for value in session.engine._priority_action_hints(
                card.controller
            )["actions"]
            if value["id"] == f"cast-{method}:{card.ref}"
        )
        result = session.act(
            f"pilot:{card.controller}",
            {"action_id": action["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_spell_with_passes(session, card)
        self.assertEqual("battlefield", card.zone)
        self.assertTrue(card.face_down)

    def test_megamorph_counter_precedes_face_up_trigger_and_replays(self):
        session = self.session(70237008, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Shieldhide Dragon",
            ref="MEGAMORPH-TRIGGER",
        )
        witness_ref = engine.create_token(
            "B",
            name="Dragon Witness",
            characteristics={
                "type_line": "Token Creature — Dragon",
                "power": "2",
                "toughness": "2",
            },
            reason="Megamorph face-up trigger witness",
        )[0]
        witness = engine._resolve_object("B", witness_ref)
        engine.state.players["B"].mana_pool.update({"W": 2, "C": 8})
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.cast_face_down(
            session,
            card,
            method=MEGAMORPH_CAST_METHOD,
        )
        offer = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"turn-face-up:{card.ref}"
        )
        result = session.act("pilot:B", {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(card.face_down)
        self.assertEqual(1, card.counters.get("+1/+1", 0))
        self.assertEqual(0, witness.counters.get("+1/+1", 0))

        self.pass_until(
            session,
            lambda: witness.counters.get("+1/+1", 0) == 1,
        )
        self.assertEqual(1, card.counters.get("+1/+1", 0))
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "megamorph-trigger-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_megamorph_counter_replacement_suspends_and_replays(self):
        session = self.session(70237009, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Aerie Bowmasters",
            ref="MEGAMORPH-REPLACEMENT",
        )
        self.add_card(
            session,
            name="Vorinclex, Monstrous Raider",
            ref="B-VORINCLEX",
            zone="battlefield",
        )
        self.add_card(
            session,
            name="Doc Samson, Super Psychiatrist",
            ref="B-DOC",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool.update({"G": 1, "C": 8})
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.cast_face_down(
            session,
            card,
            method=MEGAMORPH_CAST_METHOD,
        )
        before_mana = sum(engine.state.players["B"].mana_pool.values())
        result = session.act(
            "pilot:B",
            {"action_id": f"turn-face-up:{card.ref}"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        current = engine.state.cards[card.object_id]
        self.assertTrue(current.face_down)
        self.assertEqual(0, current.counters.get("+1/+1", 0))
        self.assertEqual(
            before_mana,
            sum(engine.state.players["B"].mana_pool.values()),
        )
        self.assertIsNotNone(
            StateProjector(self.db, engine.state)._decision("pilot:B")
        )
        for seat in ("A", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(
                    f"pilot:{seat}"
                )
            )

        self.choose_replacements(session)
        current = engine.state.cards[card.object_id]
        self.assertFalse(current.face_down)
        self.assertGreater(current.counters.get("+1/+1", 0), 1)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "megamorph-replacement-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_disguise_ward_uses_face_down_characteristics_only(self):
        session = self.session(70237010, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Defenestrated Phantom",
            ref="DISGUISE-WARD",
        )
        murder = self.add_card(
            session,
            name="Murder",
            ref="WARD-MURDER",
            seat="A",
        )
        engine.state.players["B"].mana_pool.update({"W": 1, "C": 7})
        engine.state.players["A"].mana_pool.update({"B": 2, "C": 1})
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self.cast_face_down(
            session,
            card,
            method=DISGUISE_CAST_METHOD,
        )
        self.pass_until(
            session,
            lambda: session.pending_principals() == ["pilot:A"],
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": f"cast:{murder.ref}",
                "targets": [card.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        ward_items = [
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:ward"
        ]
        ward_items.extend(
            item
            for batch in engine.state.pending_trigger_batches
            for item in batch.items
            if item.semantic_key == "builtin:ward"
        )
        self.assertEqual(1, len(ward_items))
        self.assertEqual(2, ward_items[0].context["cost"]["GENERIC"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "disguise-ward-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        revealed = self.session(70237011, players=2)
        revealed_card = self.add_card(
            revealed,
            name="Defenestrated Phantom",
            ref="DISGUISE-REVEALED",
        )
        revealed.engine.state.players["B"].mana_pool.update(
            {"W": 1, "C": 7}
        )
        self.prepare_priority(revealed)
        self.cast_face_down(
            revealed,
            revealed_card,
            method=DISGUISE_CAST_METHOD,
        )
        result = revealed.act(
            "pilot:B",
            {"action_id": f"turn-face-up:{revealed_card.ref}"},
        )
        self.assertTrue(result.ok, result.summary)
        item = StackItem(
            stack_id="fixture:post-disguise-target",
            ref="S-post-disguise-target",
            kind="activated_ability",
            controller="A",
            label="Target revealed creature",
            semantic_key="fixture:post-disguise-target",
            targets=[revealed_card.ref],
            visibility=list(revealed.engine.seats),
        )
        revealed.engine.state.stack.append(item)
        self.assertEqual(
            [],
            collect_ward_occurrences(revealed.engine, item),
        )

    def test_layer_six_removal_uses_shared_face_down_method_query(self):
        for index, (name, method) in enumerate(
            (
                ("Aerie Bowmasters", MEGAMORPH_CAST_METHOD),
                ("Defenestrated Phantom", DISGUISE_CAST_METHOD),
            )
        ):
            with self.subTest(method=method):
                session = self.session(70237012 + index, players=2)
                engine = session.engine
                card = self.add_card(
                    session,
                    name=name,
                    ref=f"REMOVED-{method}",
                )
                engine.state.players["B"].mana_pool.update(
                    {"G": 1, "W": 1, "C": 8}
                )
                self.prepare_priority(session)
                self.cast_face_down(session, card, method=method)
                create_resolution_continuous_effect(
                    engine,
                    source=ResolutionEffectSource(
                        stack_ref=f"test:remove-{method}"
                    ),
                    targets=(card,),
                    layer=Layer.ABILITY,
                    sublayer="6",
                    operations=(
                        ContinuousOperation("remove_all_abilities"),
                    ),
                )
                self.assertNotIn(
                    method,
                    {
                        keyword.casefold()
                        for keyword in engine._effective_card_data(
                            card,
                            ignore_face_down=True,
                        )["keywords"]
                    },
                )
                self.assertFalse(
                    any(
                        action["id"] == f"turn-face-up:{card.ref}"
                        for action in engine._priority_action_hints("B")[
                            "actions"
                        ]
                    )
                )

                self.assertIsNotNone(
                    compiled_fixed_mana_face_down_method_spec(
                        engine,
                        card,
                        method=method,
                    )
                )

    def test_morph_cast_turn_up_privacy_and_replay(self):
        session = self.session(70237001, players=4)
        engine = session.engine
        card = self.add_card(session, name="Lumithread Field", ref="MORPH1")
        witness_ref = engine.create_token(
            "B",
            name="Morph Witness",
            characteristics={
                "type_line": "Token Creature",
                "power": "2",
                "toughness": "2",
            },
            reason="Morph rules fixture",
        )[0]
        witness = engine._resolve_object("B", witness_ref)
        engine.state.players["B"].mana_pool.update(
            {"W": 1, "C": 4}
        )
        self.prepare_priority(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        actions = engine._priority_action_hints("B")["actions"]
        ordinary = next(
            action
            for action in actions
            if action.get("card") == card.ref and action["id"] == f"cast:{card.ref}"
        )
        morph = next(
            action
            for action in actions
            if action["id"] == f"cast-morph:{card.ref}"
        )
        self.assertEqual("{1}{W}", ordinary["cost"])
        self.assertEqual("{3}", morph["cost"])
        self.assertEqual("morph", morph["cast_method"])

        result = session.act(
            "pilot:B",
            {"action_id": morph["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", card.zone)
        self.assertTrue(card.face_down)
        effective = engine._effective_card_data(card)
        self.assertEqual("", effective["name"])
        self.assertEqual("", effective["mana_cost"])
        self.assertEqual(0, effective["mana_value"])
        self.assertEqual("Creature", effective["type_line"])
        self.assertEqual("2", effective["power"])
        self.assertEqual("2", effective["toughness"])
        self.assertEqual([], effective["keywords"])
        opponent = json.dumps(
            session.packet("pilot:A", full=True),
            sort_keys=True,
        )
        owner = json.dumps(
            session.packet("pilot:B", full=True)["state"],
            sort_keys=True,
        )
        self.assertNotIn(card.printed_name, opponent)
        self.assertIn(card.printed_name, owner)
        self.assertIn("Face-down spell", opponent)

        self.resolve_spell_with_passes(session, card)
        self.assertEqual("battlefield", card.zone)
        self.assertTrue(card.face_down)
        self.assertEqual("2", engine._effective_card_data(witness)["toughness"])
        self.assertNotIn(
            card.printed_name,
            json.dumps(
                session.packet("pilot:A", full=True),
                sort_keys=True,
            ),
        )

        turn_offer = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"turn-face-up:{card.ref}"
        )
        self.assertEqual("{1}{W}", turn_offer["cost"])
        result = session.act(
            "pilot:B",
            {"action_id": turn_offer["id"]},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(card.face_down)
        self.assertEqual("Enchantment", engine._effective_card_data(card)["type_line"])
        self.assertEqual("3", engine._effective_card_data(witness)["toughness"])
        self.assertIn(
            card.printed_name,
            json.dumps(
                session.packet("pilot:A", full=True)["state"],
                sort_keys=True,
            ),
        )
        self.assertFalse(engine.state.stack)
        self.assertEqual("B", engine.state.priority_player)
        self.assertTrue(
            any(event.code == "permanent.turn_face_up" for event in engine.state.events)
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "morph-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_face_down_static_source_is_inert_until_turned_face_up(self):
        session = self.session(70237002, players=2)
        engine = session.engine
        card = self.add_card(
            session,
            name="Lumithread Field",
            ref="MORPH2",
            zone="hand",
        )
        witness_ref = engine.create_token(
            "B",
            name="Static Witness",
            characteristics={
                "type_line": "Token Creature",
                "power": "1",
                "toughness": "1",
            },
            reason="Morph static-source fixture",
        )[0]
        witness = engine._resolve_object("B", witness_ref)
        engine.state.players["B"].mana_pool.update({"W": 1, "C": 4})
        self.prepare_priority(session)
        morph = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"cast-morph:{card.ref}"
        )
        engine.permissions.invalidate_current()
        engine._cast("B", morph)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

        self.assertTrue(card.face_down)
        self.assertEqual("1", engine._effective_card_data(witness)["toughness"])
        engine.state.priority_player = "B"
        commit_turn_face_up(
            engine,
            seat="B",
            response={"card": card.ref, "pay": "auto"},
        )
        self.assertEqual("2", engine._effective_card_data(witness)["toughness"])

    def test_stale_or_ability_removed_turn_up_rolls_back(self):
        session = self.session(70237003, players=2)
        engine = session.engine
        card = self.add_card(
            session,
            name="Lumithread Field",
            ref="MORPH3",
            zone="hand",
        )
        engine.state.players["B"].mana_pool.update({"W": 1, "C": 4})
        self.prepare_priority(session)
        morph = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"cast-morph:{card.ref}"
        )
        engine.permissions.invalidate_current()
        engine._cast("B", morph)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        engine.state.priority_player = "B"
        offer = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"turn-face-up:{card.ref}"
        )
        removed = create_resolution_continuous_effect(
            engine,
            source=ResolutionEffectSource(
                stack_ref="test:morph-remove-all-abilities"
            ),
            targets=(card,),
            layer=Layer.ABILITY,
            sublayer="6",
            operations=(ContinuousOperation("remove_all_abilities"),),
        )
        self.assertIsNotNone(removed)
        self.assertNotIn(
            "morph",
            {
                keyword.casefold()
                for keyword in engine._effective_card_data(
                    card, ignore_face_down=True
                )["keywords"]
            },
        )
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(GameRuleError, "cannot currently"):
            commit_turn_face_up(
                engine,
                seat="B",
                response=offer,
            )

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertTrue(card.face_down)

    def test_stale_morph_cast_contract_rolls_back_before_payment(self):
        session = self.session(70237006, players=2)
        engine = session.engine
        card = self.add_card(
            session,
            name="Lumithread Field",
            ref="MORPH6",
            zone="hand",
        )
        engine.state.players["B"].mana_pool["C"] = 3
        self.prepare_priority(session)
        offer = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"cast-morph:{card.ref}"
        )
        proposal = build_cast_proposal(
            engine,
            CastProposalRequest.from_submission("B", offer),
        )
        card.annotations["copy_overrides"] = {"name": "Copied fixture"}
        before = authoritative_state_hash(engine.state)

        with self.assertRaisesRegex(CastProposalError, "changed before commit"):
            commit_cast(engine, proposal, offer)

        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("hand", card.zone)
        self.assertEqual(3, engine.state.players["B"].mana_pool["C"])

    def test_face_down_departure_reveals_identity_and_resets_method(self):
        session = self.session(70237005, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Lumithread Field",
            ref="MORPH5",
            zone="hand",
        )
        engine.state.players["B"].mana_pool["C"] = 3
        self.prepare_priority(session)
        morph = next(
            action
            for action in engine._priority_action_hints("B")["actions"]
            if action["id"] == f"cast-morph:{card.ref}"
        )
        engine.permissions.invalidate_current()
        engine._cast("B", morph)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertTrue(card.face_down)
        self.assertNotIn(
            card.printed_name,
            json.dumps(session.packet("pilot:A", full=True), sort_keys=True),
        )

        engine.move_card(
            card.object_id,
            "graveyard",
            reason="Morph departure reveal fixture",
        )

        self.assertEqual("graveyard", card.zone)
        self.assertFalse(card.face_down)
        self.assertNotIn("face_down_method", card.annotations)
        self.assertNotIn("face_down_characteristics", card.annotations)
        self.assertIn(
            card.printed_name,
            json.dumps(session.packet("pilot:A", full=True), sort_keys=True),
        )

    def test_land_card_has_only_the_trusted_morph_cast_offer(self):
        session = self.session(70237004, players=2)
        card = self.add_card(session, name="Zoetic Cavern", ref="MORPH4")
        session.engine.state.players["B"].mana_pool["C"] = 3
        self.prepare_priority(session)

        actions = [
            action
            for action in session.engine._priority_action_hints("B")["actions"]
            if action.get("card") == card.ref and action["action"] == "cast"
        ]
        self.assertEqual([f"cast-morph:{card.ref}"], [row["id"] for row in actions])
        self.assertIsNotNone(compiled_fixed_mana_morph_spec(session.engine, card))

    def test_partial_card_does_not_receive_a_morph_cast_offer(self):
        session = self.session(70237007, players=2)
        card = self.add_card(session, name="Brine Elemental", ref="MORPH-PARTIAL")
        session.engine.state.players["B"].mana_pool["C"] = 3
        self.prepare_priority(session)

        actions = session.engine._priority_action_hints("B")["actions"]
        self.assertFalse(
            any(row.get("id") == f"cast-morph:{card.ref}" for row in actions)
        )
        self.assertIsNone(compiled_fixed_mana_morph_spec(session.engine, card))
        program = next(
            program
            for program in session.engine.semantics.programs_for_oracle(
                card.oracle_id,
                event="morph.action",
            )
            if any(
                descriptor.get("handler_id") == MORPH_HANDLER_ID
                for descriptor in program.handlers
            )
        )
        self.assertEqual(
            {
                "schema_version": 1,
                "oracle_ir_status": "partial",
                "material_residual_count": 1,
            },
            program.provenance["card_program_admission"],
        )


if __name__ == "__main__":
    unittest.main()
