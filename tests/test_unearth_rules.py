from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.compiler import unearth_nodes as unearth_nodes_module
from quorune.deck import DeckLoader
from quorune.haste import has_effective_haste, summoning_sickness_prohibits_attack
from quorune.model import CardInstance, DecisionGroup
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime.context import ReadOnlyHandlerContext, SemanticSourceContext
from quorune.semantic_runtime.unearth import (
    default_ordinary_unearth_ability_registry,
    UnearthEffectHandler,
)
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from quorune.unearth import (
    compile_ordinary_unearth_ability,
    OrdinaryUnearthAbilitySpec,
    ordinary_unearth_handler_descriptor,
    UNEARTH_ABILITY_HANDLER_ID,
    UNEARTH_CAPABILITY_ID,
    UnearthError,
    UnearthIntent,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "unearth-rules-cards.json"
PARTIAL_FIXTURE_PATH = (
    ROOT / "tests" / "fixtures" / "unearth-partial-card.json"
)


def focused_card_database(directory: str) -> CardDatabase:
    database = Path(directory) / "unearth-rules.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "bound-effect-program-cards.json",
            FIXTURE_PATH,
            PARTIAL_FIXTURE_PATH,
        ],
        database,
    )
    return CardDatabase(database)


class OrdinaryUnearthModelTests(unittest.TestCase):
    def test_unearth_descriptor_and_effect_handler_are_strict(self):
        spec = compile_ordinary_unearth_ability(
            material_line="Unearth {2}{B}.",
            oracle_line="Unearth {2}{B}.",
            line_index=1,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual("ab2", spec.ability_id)
        self.assertEqual(
            {
                "GENERIC": 2,
                "W": 0,
                "U": 0,
                "B": 1,
                "R": 0,
                "G": 0,
                "C": 0,
            },
            dict(spec.to_dict()["mana_cost"]),
        )
        self.assertEqual(spec, OrdinaryUnearthAbilitySpec.from_dict(spec.to_dict()))
        self.assertEqual(
            (spec,),
            default_ordinary_unearth_ability_registry().lower(
                ordinary_unearth_handler_descriptor(spec),
                None,
            ),
        )
        ability = spec.to_activated_ability()
        self.assertEqual(("graveyard",), ability.zones)
        self.assertTrue(ability.sorcery_speed)

        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(UnearthError, "unknown"):
            OrdinaryUnearthAbilitySpec.from_dict(malformed)
        self.assertIsNone(
            compile_ordinary_unearth_ability(
                material_line="Unearth {X}{B}",
                oracle_line="Unearth {X}{B}",
                line_index=0,
            )
        )

        context = ReadOnlyHandlerContext.from_sequences(
            actor="A",
            default_reason="Unearth model test",
            seats=("A", "B"),
            active_seats=("A", "B"),
            apnap_order=("A", "B"),
            source=SemanticSourceContext(
                stack_ref="S1",
                object_id="object:1",
                logical_object_id="object:1@0",
                card_ref="C1",
            ),
        )
        plan = UnearthEffectHandler().lower(
            {"op": "unearth", "action": "return"},
            context,
        )
        self.assertEqual(
            UnearthIntent(
                action="return",
                actor="A",
                stack_ref="S1",
                object_id="object:1",
                card_ref="C1",
                logical_object_id="object:1@0",
            ),
            plan.intents[0],
        )
        with self.assertRaisesRegex(ValueError, "invalid shape"):
            UnearthEffectHandler().lower(
                {"op": "unearth", "action": "return", "unknown": True},
                context,
            )


class OrdinaryUnearthCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_card_database(cls.temporary.name)
        cls.record = cls.db.lookup("Dregscape Zombie")
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            REGISTRY_PATH.read_text(encoding="utf-8")
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def compile(self, record=None, *, capabilities=None):
        return compile_oracle_card(
            record or self.record,
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def node(self, record=None, *, capabilities=None):
        ir = self.compile(record, capabilities=capabilities)
        return ir, next(
            node
            for node in ir.faces[0].nodes
            if "unearth" in node.mechanics
        )

    def test_fixed_mana_unearth_compiles_source_spanned_typed_program(self):
        ir, node = self.node()
        self.assertTrue(node.exact, ir.material_residuals)
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("graveyard", node.active_zone)
        self.assertEqual("activate", node.event)
        self.assertEqual("ordinary-fixed-mana-unearth-v1", node.template_id)
        self.assertEqual((UNEARTH_CAPABILITY_ID,), node.capability_dependencies)
        self.assertEqual(UNEARTH_ABILITY_HANDLER_ID, node.handlers[0]["handler_id"])
        self.assertEqual(
            self.record.oracle_text[node.span.start : node.span.end],
            node.text,
        )
        self.assertEqual({"op": "unearth", "action": "return"}, node.effects[0])

        registry = SemanticRegistry(include_builtin_packs=False)
        register_generated_programs(
            self.db,
            registry,
            (self.record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
            promote_exact_effect_programs=True,
        )
        card_program = registry.card_program_for_oracle(self.record.oracle_id)
        self.assertIsNotNone(card_program)
        assert card_program is not None
        self.assertTrue(card_program.trust_closure["trusted"])
        program = next(
            program
            for program in registry.programs_for_oracle(self.record.oracle_id)
            if program.event == "activate"
        )
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual(
            {
                "schema_version": 1,
                "oracle_ir_status": "exact",
                "material_residual_count": 0,
            },
            program.provenance["card_program_admission"],
        )

    def test_nonordinary_unearth_costs_remain_residual(self):
        variable = replace(
            self.record,
            oracle_text="Unearth {X}{B}",
            keywords=("Unearth",),
        )
        ir, node = self.node(variable)
        self.assertFalse(node.exact)
        self.assertFalse(node.lowerable)
        self.assertEqual("ordinary-unearth-residual-v1", node.template_id)
        self.assertTrue(ir.material_residuals)

    def test_unearth_dependency_and_compiler_mutations_fail_closed(self):
        capability = next(
            row
            for row in self.registry_value["capabilities"]
            if row["id"] == UNEARTH_CAPABILITY_ID
        )
        for blocked in (UNEARTH_CAPABILITY_ID, *capability["dependencies"]):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item for item in value["capabilities"] if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused Unearth dependency mutation"]
                _ir, node = self.node(
                    capabilities=CapabilityRegistry(value)
                )
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)

        def assert_exact() -> None:
            _ir, node = self.node()
            self.assertTrue(node.exact)
            self.assertEqual(UNEARTH_ABILITY_HANDLER_ID, node.handlers[0]["handler_id"])

        assert_exact()
        with mock.patch.object(
            unearth_nodes_module,
            "compile_ordinary_unearth_ability",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class OrdinaryUnearthRuntimeTests(unittest.TestCase):
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
        seat: str = "B",
        zone: str = "graveyard",
        controller: str | None = None,
    ):
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=controller or seat,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
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
            promote_exact_effect_programs=True,
        )
        return card

    @staticmethod
    def prepare_main(session, seat: str = "B") -> None:
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
    def resolve_stack_with_passes(session) -> DecisionGroup | None:
        for _ in range(12):
            decision = session.state.pending_decision
            if decision is not None and decision.kind != "priority":
                return decision
            if not session.engine.state.stack:
                return None
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Unearth activation did not resolve")

    @staticmethod
    def direct_unearth(session, card) -> None:
        engine = session.engine
        action = next(
            action
            for action in engine._priority_action_hints(card.owner)["actions"]
            if action.get("source") == card.ref
            and action.get("ability") == "ab1"
        )
        engine.permissions.invalidate_current()
        engine._activate(card.owner, action)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    @staticmethod
    def resolve_delayed_end_step(engine) -> None:
        triggers = engine._matching_delayed_triggers(
            "step.begin",
            {"phase": "ending", "step": "end_step", "player": "B"},
        )
        if not triggers:
            raise AssertionError("Unearth delayed trigger was not available")
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._start_trigger_batch(triggers, after="grant_priority")
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_unearth_offer_resolution_haste_and_delayed_exile_replay(self):
        session = self.session(70284001, players=4)
        engine = session.engine
        card = self.add_card(session, name="Dregscape Zombie", ref="UNE1")
        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        owner_actions = engine._priority_action_hints("B")["actions"]
        action = next(
            row
            for row in owner_actions
            if row.get("source") == card.ref and row.get("ability") == "ab1"
        )
        self.assertFalse(
            any(
                row.get("source") == card.ref
                for row in engine._priority_action_hints("A")["actions"]
            )
        )
        result = session.act("pilot:B", {"action_id": action["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", card.zone)
        self.assertEqual(1, len(engine.state.stack))
        self.resolve_stack_with_passes(session)

        self.assertEqual("battlefield", card.zone)
        self.assertTrue(card.unearthed)
        self.assertTrue(has_effective_haste(engine, card))
        self.assertFalse(summoning_sickness_prohibits_attack(engine, card))
        self.assertTrue(
            any(
                trigger.active and trigger.source_object_id == card.object_id
                for trigger in engine.state.delayed_triggers
            )
        )
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertIn('"unearthed": true', packet)
            self.assertNotIn(card.object_id, packet)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "unearth-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        self.resolve_delayed_end_step(engine)
        self.assertEqual("exile", card.zone)
        self.assertFalse(card.unearthed)
        self.assertFalse(engine.state.stack)

    def test_unearthed_leave_replacement_precedes_competing_destination(self):
        session = self.session(70284002, players=4)
        session.state.unearth_leave_replacement_version = None
        engine = session.engine
        card = self.add_card(session, name="Dregscape Zombie", ref="UNE2")
        voidwalker = self.add_card(
            session,
            name="Dauthi Voidwalker",
            ref="UNE-VOIDWALKER",
            seat="A",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        self.direct_unearth(session, card)
        before_events = len(engine.state.events)

        engine.move_card(
            card.object_id,
            "graveyard",
            reason="Unearth competing replacement fixture",
        )

        self.assertEqual("exile", card.zone)
        self.assertFalse(card.unearthed)
        applied = [
            event
            for event in engine.state.events[before_events:]
            if event.code == "replacement.apply"
        ]
        self.assertEqual([card.ref], [event.details["source"] for event in applied])
        self.assertFalse(
            any(event.details.get("source") == voidwalker.ref for event in applied)
        )

        session = self.session(70284003, players=4)
        session.state.unearth_leave_replacement_version = None
        engine = session.engine
        card = self.add_card(session, name="Dregscape Zombie", ref="UNE3")
        self.add_card(
            session,
            name="Dauthi Voidwalker",
            ref="UNE-VOIDWALKER-2",
            seat="A",
            zone="battlefield",
        )
        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        self.direct_unearth(session, card)
        with mock.patch(
            "quorune.semantic_runtime.zone_replacements.unearthed_leave_replacement",
            return_value=None,
        ):
            engine.move_card(card.object_id, "graveyard", reason="mutant")
        replacement = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "replacement.apply"
        )
        self.assertNotEqual(card.ref, replacement.details["source"])

    def test_current_unearthed_leave_offers_both_destination_replacements(self):
        session = self.session(70284009)
        engine = session.engine
        zombie = self.add_card(session, name="Dregscape Zombie", ref="unearthed-zombie")
        voidwalker = self.add_card(session, name="Dauthi Voidwalker", ref="opposing-voidwalker",
                                  seat="A", zone="battlefield")
        murder = self.add_card(session, name="Murder", ref="destroy-unearthed-zombie", zone="hand")
        engine.state.players["B"].mana_pool.update({"B": 3, "C": 1})
        self.prepare_main(session)
        session.initial_checkpoint = checkpoint_envelope(session.state)

        actions = session.packet("pilot:B", full=True)["decision"]["legal_actions"]
        unearth, = [action for action in actions if action["id"].startswith(f"activate:{zombie.ref}:")]
        result = session.act("pilot:B", {"action_id": unearth["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve_stack_with_passes(session))
        self.assertEqual(("battlefield", True), (zombie.zone, zombie.unearthed))

        actions = session.packet("pilot:B", full=True)["decision"]["legal_actions"]
        cast, = [action for action in actions if action["id"] == f"cast:{murder.ref}"]
        result = session.act("pilot:B", {"action_id": cast["id"], "pay": "auto", "targets": [zombie.ref]})
        self.assertTrue(result.ok, result.summary)
        decision = self.resolve_stack_with_passes(session)
        self.assertIsNotNone(decision)
        self.assertEqual("replacement.order", decision.kind)
        self.assertIsNone(session.packet("pilot:A", full=True)["decision"])
        options = session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        self.assertEqual(1, sum(option["id"].startswith("rule:unearth:") for option in options))
        pending_hash = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "replacement": options[0]["id"]}).ok)
        self.assertFalse(session.act("pilot:B", {"action_id": "choose", "replacement": "unoffered"}).ok)
        self.assertEqual(pending_hash, authoritative_state_hash(session.state))

        with tempfile.TemporaryDirectory() as directory:
            pending = Path(directory) / "pending-departure"
            session.save(pending)
            for index, option in enumerate(options):
                with self.subTest(option=option["id"]):
                    resumed = CommanderSession.load(self.db, pending)
                    self.assertEqual(1, resumed.state.unearth_leave_replacement_version)
                    self.assertEqual(pending_hash, authoritative_state_hash(resumed.state))
                    result = resumed.act("pilot:B", {"action_id": "choose", "replacement": option["id"]})
                    self.assertTrue(result.ok, result.summary)
                    self.assertIsNone(self.resolve_stack_with_passes(resumed))
                    departed = resumed.state.cards[zombie.object_id]
                    chose_unearth = option["id"].startswith("rule:unearth:")
                    self.assertEqual(("exile", False, 0 if chose_unearth else 1),
                                     (departed.zone, departed.unearthed, departed.counters.get("void", 0)))
                    applied = [event.details["source"] for event in resumed.state.events
                               if event.code == "replacement.apply" and event.details["object"] == zombie.ref]
                    self.assertEqual([zombie.ref if chose_unearth else voidwalker.ref], applied)
                    record = Path(directory) / f"chosen-{index}"
                    resumed.save(record)
                    replay = replay_record(record, self.db, verify=True)
                    self.assertTrue(replay["ok"], replay)
                    self.assertEqual(authoritative_state_hash(resumed.state), replay["final_state_hash"])

    def test_countered_or_stale_unearth_activation_leaves_card_in_graveyard(self):
        session = self.session(70284004, players=2)
        engine = session.engine
        card = self.add_card(session, name="Dregscape Zombie", ref="UNE4")
        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        action = next(
            row
            for row in engine._priority_action_hints("B")["actions"]
            if row.get("source") == card.ref
        )
        engine.permissions.invalidate_current()
        engine._activate("B", action)
        engine._counter_stack_item(engine.state.stack[-1].ref, reason="Unearth test")
        self.assertEqual("graveyard", card.zone)
        self.assertFalse(card.unearthed)

        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        action = next(
            row
            for row in engine._priority_action_hints("B")["actions"]
            if row.get("source") == card.ref
        )
        engine.permissions.invalidate_current()
        engine._activate("B", action)
        engine.move_card(card.object_id, "exile", log=False)
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("exile", card.zone)
        self.assertFalse(card.unearthed)

    def test_complete_card_program_admission_hides_partial_unearth_card(self):
        session = self.session(70284005, players=2)
        engine = session.engine
        complete = self.add_card(
            session,
            name="Dregscape Zombie",
            ref="UNE5",
        )
        partial = self.add_card(
            session,
            name="Anathemancer",
            ref="UNE-PARTIAL",
        )
        engine.state.players["B"].mana_pool.update({"B": 6, "R": 1})
        self.prepare_main(session)
        actions = engine._priority_action_hints("B")["actions"]

        self.assertTrue(any(row.get("source") == complete.ref for row in actions))
        self.assertFalse(any(row.get("source") == partial.ref for row in actions))
        card_program = engine.semantics.card_program_for_oracle(partial.oracle_id)
        self.assertIsNotNone(card_program)
        assert card_program is not None
        self.assertTrue(card_program.trust_closure["trusted"])
        unearth_program = next(
            program
            for program in engine.semantics.programs_for_oracle(
                partial.oracle_id,
                event="activate",
            )
            if any(
                descriptor.get("handler_id") == UNEARTH_ABILITY_HANDLER_ID
                for descriptor in program.handlers
            )
        )
        self.assertEqual(
            {
                "schema_version": 1,
                "oracle_ir_status": "partial",
                "material_residual_count": 1,
            },
            unearth_program.provenance["card_program_admission"],
        )

    def test_control_change_phasing_and_returned_incarnation_are_isolated(self):
        session = self.session(70284006, players=4)
        engine = session.engine
        card = self.add_card(session, name="Dregscape Zombie", ref="UNE6")
        engine.state.players["B"].mana_pool["B"] = 1
        self.prepare_main(session)
        self.direct_unearth(session, card)
        returned_identity = card.logical_object_id
        engine.change_control(card.object_id, "A", reason="Unearth fixture")
        card.phased_out = True

        self.resolve_delayed_end_step(engine)

        self.assertEqual("battlefield", card.zone)
        self.assertEqual("A", card.controller)
        self.assertTrue(card.unearthed)
        card.phased_out = False
        engine.move_card(card.object_id, "graveyard", reason="Unearth departure")
        self.assertEqual("exile", card.zone)
        self.assertNotEqual(returned_identity, card.logical_object_id)
        engine.move_card(card.object_id, "graveyard", log=False)
        engine.move_card(card.object_id, "battlefield", controller="B", log=False)
        self.assertFalse(card.unearthed)
        self.assertFalse(has_effective_haste(engine, card))


if __name__ == "__main__":
    unittest.main()
