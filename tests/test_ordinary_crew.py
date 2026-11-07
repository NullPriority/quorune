from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from common import ROOT, keep_all, load_assets, make_session
import test_bound_effect_programs as bound
from test_qualified_zone_event_queries import query_record
import test_ring_native_actions as turn_actions
import test_self_public_zone_casting as public_actions
from quorune.ability_fragment_primitives import (
    AbilityFragmentError,
    CREW_POWER_BONUS_CAPABILITY,
    CrewPowerBonusSpec,
)
from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict
from quorune.carddb import CardRecord
from quorune.compiler import crew_nodes as crew_nodes_module
from quorune.counter_state import (
    CounterChange,
    commit_counter_changes,
    plan_counter_changes,
)
from quorune.crew import (
    CrewAbilityError,
    OrdinaryCrewAbilitySpec,
    commit_crew_cost,
    compile_ordinary_crew_ability,
    crew_candidates,
    ordinary_crew_handler_descriptor,
    prepare_crew_cost,
)
from quorune.haste import is_summoning_sick
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.preflight import card_semantic_status
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.crew_abilities import (
    ordinary_crew_specs_from_descriptors,
)
from quorune.semantics import SemanticRegistry
from quorune.tap_state import set_permanent_tapped


def crew_record(
    base: CardRecord,
    oracle_text: str,
    *,
    keywords: tuple[str, ...] = ("Crew",),
) -> CardRecord:
    return replace(
        base,
        oracle_id="00000000-0000-4000-8000-000000000122",
        name="Ordinary Crew Fixture",
        oracle_text=oracle_text,
        keywords=keywords,
        type_line="Artifact — Vehicle",
        faces=(),
    )


class OrdinaryCrewModelTests(unittest.TestCase):
    def test_fixed_crew_bonus_codec_rejects_invalid_amounts(self):
        spec = CrewPowerBonusSpec(2)
        self.assertEqual(spec, ability_fragment_from_dict(ability_fragment_to_dict(spec)))
        for change in ({"amount": 0}, {"amount": -2}, {"amount": True},
                       {"amount": "2"}, {"schema_version": True}, {"scope": "saddle"}):
            with self.subTest(change=change), self.assertRaises(AbilityFragmentError):
                CrewPowerBonusSpec.from_dict({**spec.to_dict(), **change})

    def test_ordinary_crew_descriptor_is_strict_immutable_and_round_trips(self):
        spec = compile_ordinary_crew_ability(
            material_line="Crew 3",
            oracle_line="Crew 3",
            line_index=2,
        )
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec, OrdinaryCrewAbilitySpec.from_dict(spec.to_dict()))
        self.assertEqual("ab3", spec.ability_id)
        ability = spec.to_activated_ability()
        self.assertEqual(("battlefield",), ability.zones)
        self.assertEqual(3, ability.crew_threshold)
        self.assertEqual("Crew 3", ability.cost_text)

        payload = spec.to_dict()
        payload["threshold"] = 99
        self.assertEqual(3, spec.threshold)
        malformed = spec.to_dict()
        malformed["unknown"] = True
        with self.assertRaisesRegex(CrewAbilityError, "unknown"):
            OrdinaryCrewAbilitySpec.from_dict(malformed)
        malformed = spec.to_dict()
        malformed["threshold"] = True
        with self.assertRaisesRegex(CrewAbilityError, "nonnegative integer"):
            OrdinaryCrewAbilitySpec.from_dict(malformed)

        descriptor = ordinary_crew_handler_descriptor(spec)
        self.assertEqual(
            (spec,), ordinary_crew_specs_from_descriptors([descriptor])
        )
        descriptor["unknown"] = True
        with self.assertRaisesRegex(ValueError, "unknown"):
            ordinary_crew_specs_from_descriptors([descriptor])

    def test_crew_zero_accepts_an_empty_tap_cost(self):
        db, mishra, zimone = load_assets()
        try:
            session = make_session(db, mishra, zimone, players=2, seed=70212200)
            keep_all(session)
            engine = session.engine
            source = next(
                card
                for card in engine.state.cards.values()
                if card.owner == "A" and card.printed_name == "Demonic Junker"
            )
            engine.move_card(
                source.object_id,
                "battlefield",
                controller="A",
                log=False,
            )

            plan = prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=0,
                response={},
            )

            self.assertEqual((), plan.selected)
            self.assertEqual(0, plan.total_power)
        finally:
            db.close()


class OrdinaryCrewCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.base = cls.db.lookup("Demonic Junker")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def compile(self, text: str, *, keywords=("Crew",)):
        return compile_oracle_card(
            crew_record(self.base, text, keywords=tuple(keywords)),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_ordinary_crew_compiles_source_spanned_activation(self):
        text = "Crew 3"
        ir = self.compile(text)

        self.assertEqual("exact", ir.status)
        self.assertEqual(1, len(ir.faces[0].nodes))
        node = ir.faces[0].nodes[0]
        self.assertEqual("activated_ability", node.kind)
        self.assertEqual("ordinary-crew-activation-v1", node.template_id)
        self.assertEqual("battlefield", node.active_zone)
        self.assertEqual("activate", node.event)
        self.assertEqual(
            ("activation.crew.fixed_power",),
            node.capability_dependencies,
        )
        self.assertEqual(3, node.cost["crew"])
        self.assertEqual("Crew 3", node.cost["text"])
        self.assertEqual(
            {
                "op": "add_types_until_end_of_turn",
                "card": "$source.zone_object",
                "types": ["Artifact", "Creature"],
            },
            node.effects[0],
        )
        self.assertEqual(text, text[node.span.start : node.span.end])
        self.assertEqual(
            "ability.activated.crew.v1",
            node.handlers[0]["handler_id"],
        )

    def test_unsupported_crew_variants_remain_precise_residuals(self):
        for text in (
            "Crew X",
            "Crew {3}",
            "Crew — Tap another artifact you control.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)
                self.assertEqual(
                    "unsupported_crew_cost",
                    ir.material_residuals[0].kind,
                )

        for text in (
            "Creatures you control crew Vehicles as though their power were 2 greater.",
            "This creature saddles Mounts and crews Vehicles as though its power were 2 greater.",
            "Target Vehicle becomes crewed until end of turn.",
            "Whenever this Vehicle becomes crewed, draw a card.",
            "This creature can't crew Vehicles.",
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_fixed_crew_bonus_is_source_spanned_and_closed(self):
        text = "This creature crews Vehicles as though its power were 2 greater."
        ir = compile_oracle_card(query_record(text), capability_registry=self.capabilities,
            capability_profile="commander_review")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        handler, = node.handlers
        self.assertEqual((text, 0, len(text)), (node.text, node.span.start, node.span.end))
        self.assertEqual((CREW_POWER_BONUS_CAPABILITY,), node.capability_dependencies)
        self.assertEqual(CrewPowerBonusSpec(2), ability_fragment_from_dict(handler["fragment"]))

    def test_fixed_crew_bonus_withdrawal_rejects_only_dependent_clause(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        crew = next(row for row in raw["capabilities"] if row["id"] == "activation.crew.fixed_power")
        crew.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        text = "This creature crews Vehicles as though its power were 2 greater."
        affected = compile_oracle_card(query_record(text), capability_registry=registry,
            capability_profile="commander_review")
        control = compile_oracle_card(query_record("Flying"), capability_registry=registry,
            capability_profile="commander_review")
        self.assertNotEqual("exact", affected.status)
        self.assertTrue(affected.material_residuals)
        self.assertEqual("exact", control.status)

    def test_fixed_crew_bonus_compiler_omission_is_killed(self):
        with mock.patch.object(crew_nodes_module, "crew_power_bonus_handler", return_value=None):
            with self.assertRaises(AssertionError):
                self.test_fixed_crew_bonus_is_source_spanned_and_closed()

    def test_generated_crew_program_is_capability_closed(self):
        record = crew_record(self.base, "Crew 2")
        registry = SemanticRegistry(include_builtin_packs=False)
        result = register_generated_programs(
            self.db,
            registry,
            (record,),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        program = registry.get(f"{record.oracle_id}:ability:ab1")
        self.assertIsNotNone(program)
        assert program is not None
        self.assertEqual("trusted", program.trust_level)
        self.assertEqual("battlefield", program.active_zone)
        self.assertEqual(
            ["activation.crew.fixed_power"],
            program.capability_dependencies,
        )
        self.assertEqual(1, result["runtime_handlers_promoted"])
        status = card_semantic_status(
            record,
            registry,
            db=self.db,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotIn("activated_ability:crew", status["unresolved"])

    def test_ordinary_crew_compiler_mutant_is_killed(self):
        def assert_exact() -> None:
            ir = self.compile("Crew 2")
            node = ir.faces[0].nodes[0]
            self.assertTrue(node.exact)
            self.assertEqual(2, node.cost["crew"])

        assert_exact()
        with mock.patch.object(
            crew_nodes_module,
            "compile_ordinary_crew_ability",
            return_value=None,
        ):
            with self.assertRaises(AssertionError):
                assert_exact()


class OrdinaryCrewRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    add = bound.BoundEffectProgramRuntimeTests.add
    checkpoint = bound.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound.BoundEffectProgramRuntimeTests.resolve
    replay = bound.BoundEffectProgramRuntimeTests.replay
    prepare_native = public_actions.SelfPublicZoneRuntimeTests.prepare
    cast = public_actions.SelfPublicZoneRuntimeTests.cast
    assert_closed = public_actions.SelfPublicZoneRuntimeTests.assert_closed
    until = turn_actions.RingNativeActionTests.until

    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.registry = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        return session

    @staticmethod
    def card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def creature(engine, seat: str, name: str, power: int):
        ref = engine.create_token(
            seat,
            name=name,
            characteristics={
                "type_line": "Token Creature — Pilot",
                "power": str(power),
                "toughness": "4",
            },
            reason="ordinary Crew fixture",
        )[0]
        return engine._resolve_object(seat, ref)

    def prepare(
        self,
        session,
        *,
        seat: str = "A",
        powers: tuple[int, ...] = (1, 1),
        power_counters: tuple[int, ...] = (),
        source_type_line: str | None = None,
    ):
        engine = session.engine
        source = self.card(engine, seat, "Demonic Junker")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller=seat,
            log=False,
        )
        if source_type_line is not None:
            source.annotations.setdefault("copy_overrides", {})[
                "type_line"
            ] = source_type_line
        candidates = tuple(
            self.creature(engine, seat, f"Crew Pilot {index}", power)
            for index, power in enumerate(powers, start=1)
        )
        if power_counters:
            self.assertEqual(len(candidates), len(power_counters))
            changes = tuple(
                CounterChange(
                    "permanent",
                    candidate.object_id,
                    "+1/+1",
                    amount,
                    expected_zone="battlefield",
                    expected_logical_object_id=candidate.logical_object_id,
                )
                for candidate, amount in zip(candidates, power_counters, strict=True)
                if amount
            )
            if changes:
                commit_counter_changes(engine, plan_counter_changes(engine, changes))
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
        crew_abilities = [
            ability
            for ability in engine._activated_abilities(source)
            if ability.crew_threshold is not None
        ]
        self.assertEqual(1, len(crew_abilities))
        ability = crew_abilities[0]
        self.assertEqual("ab3", ability.ability_id)
        action_id = f"activate:{source.ref}:{ability.ability_id}"
        actions = session.packet(f"pilot:{seat}", full=True)["decision"]["ctx"][
            "legal"
        ]["actions"]
        self.assertIn(action_id, {action["id"] for action in actions})
        return source, candidates, action_id

    @staticmethod
    def resolve_until(session, predicate, *, limit: int = 32) -> None:
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            if not principals:
                raise AssertionError("Crew resolution stopped without priority")
            result = session.act(principals[0], {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Crew did not resolve within the bounded loop")

    @staticmethod
    def effective_types(engine, card) -> set[str]:
        return engine._type_parts(
            str(engine._effective_card_data(card).get("type_line") or "")
        )[0]

    @staticmethod
    def effective_subtypes(engine, card) -> set[str]:
        return engine._type_parts(
            str(engine._effective_card_data(card).get("type_line") or "")
        )[1]

    @staticmethod
    def effective_supertypes(engine, card) -> set[str]:
        return engine._type_parts(
            str(engine._effective_card_data(card).get("type_line") or "")
        )[2]

    def test_generic_crew_taps_exact_creatures_and_resolves_type_effect(self):
        session = self.session(70212201)
        engine = session.engine
        source, candidates, action_id = self.prepare(session)
        self.assertTrue(
            all(is_summoning_sick(engine, card) for card in candidates)
        )

        result = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [card.ref for card in candidates],
            },
        )

        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(card.tapped for card in candidates))
        self.assertNotIn("creature", self.effective_types(engine, source))
        item = engine.state.stack[-1]
        self.assertEqual(
            [card.ref for card in candidates],
            item.context["cost_objects"],
        )
        self.assertEqual(2, item.context["crew"]["threshold"])
        self.assertEqual(2, len(item.context["crew"]["crewed_by"]))

        self.resolve_until(
            session,
            lambda: "creature" in self.effective_types(engine, source),
        )
        self.assertIn("artifact", self.effective_types(engine, source))
        self.assertIn("creature", self.effective_types(engine, source))

    def test_crew_retains_existing_card_types_and_subtypes(self):
        session = self.session(70212215)
        engine = session.engine
        source, candidates, action_id = self.prepare(
            session,
            powers=(2,),
            source_type_line=(
                "Legendary Artifact Land — Vehicle Island"
            ),
        )
        self.assertEqual(
            {"artifact", "land"},
            self.effective_types(engine, source),
        )
        self.assertEqual(
            {"island", "vehicle"},
            self.effective_subtypes(engine, source),
        )
        self.assertEqual(
            {"legendary"},
            self.effective_supertypes(engine, source),
        )
        self.assertTrue(
            any(
                ability.ability_id == "intrinsic_island"
                and ability.mana_ability
                for ability in engine._activated_abilities(source)
            )
        )

        result = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [candidates[0].ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_until(
            session,
            lambda: "creature" in self.effective_types(engine, source),
        )

        self.assertEqual(
            {"artifact", "creature", "land"},
            self.effective_types(engine, source),
        )
        self.assertEqual(
            {"island", "vehicle"},
            self.effective_subtypes(engine, source),
        )
        self.assertEqual(
            {"legendary"},
            self.effective_supertypes(engine, source),
        )
        self.assertTrue(
            any(
                ability.ability_id == "intrinsic_island"
                and ability.mana_ability
                for ability in engine._activated_abilities(source)
            )
        )

    def test_crew_zero_offer_and_commit_share_empty_cost_legality(self):
        session = self.session(70212209)
        engine = session.engine
        with mock.patch.object(engine, "_crew_threshold", return_value=0):
            source, candidates, action_id = self.prepare(
                session,
                powers=(),
            )
            self.assertEqual((), candidates)
            action = next(
                row
                for row in session.packet("pilot:A", full=True)["decision"][
                    "ctx"
                ]["legal"]["actions"]
                if row["id"] == action_id
            )
            self.assertEqual(
                0,
                action["cost_summary"]["choose_cost"][0]["minimum"],
            )

            result = session.act("pilot:A", {"action_id": action_id})

        self.assertTrue(result.ok, result.summary)
        self.assertEqual([], engine.state.stack[-1].context["cost_objects"])
        self.assertEqual(
            [],
            engine.state.stack[-1].context["crew"]["crewed_by"],
        )
        self.resolve_until(
            session,
            lambda: "creature" in self.effective_types(engine, source),
        )
        self.assertIn("artifact", self.effective_types(engine, source))

    def test_equivalent_selection_order_produces_one_canonical_cost_plan(self):
        session = self.session(70212207)
        engine = session.engine
        source, candidates, _ = self.prepare(session)
        forward = prepare_crew_cost(
            engine,
            seat="A",
            source=source,
            threshold=2,
            response={"cost_cards": [card.ref for card in candidates]},
        )
        reverse = prepare_crew_cost(
            engine,
            seat="A",
            source=source,
            threshold=2,
            response={
                "cost_cards": [card.ref for card in reversed(candidates)]
            },
        )

        self.assertEqual(forward, reverse)
        self.assertEqual(forward.context_dict(), reverse.context_dict())

    def test_selected_negative_power_counts_against_crew_total_and_rolls_back(self):
        session = self.session(70212202)
        engine = session.engine
        source, candidates, action_id = self.prepare(
            session,
            powers=(3, -2),
        )
        before = authoritative_state_hash(engine.state)

        result = session.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [card.ref for card in candidates],
            },
        )

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertTrue(all(not card.tapped for card in candidates))
        self.assertFalse(engine.state.stack)
        self.assertNotIn("creature", self.effective_types(engine, source))

        payable = self.session(70212213)
        source, candidates, action_id = self.prepare(
            payable,
            powers=(4, -2),
        )
        result = payable.act(
            "pilot:A",
            {
                "action_id": action_id,
                "cost_cards": [card.ref for card in candidates],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(card.tapped for card in candidates))
        self.assertEqual(
            2,
            payable.engine.state.stack[-1].context["crew"]["total_power"],
        )

    def test_noncontrolled_tapped_and_duplicate_cost_objects_are_rejected(self):
        session = self.session(70212208, players=4)
        engine = session.engine
        source, candidates, _ = self.prepare(session, powers=(2,))
        own = candidates[0]
        opposing = self.creature(engine, "B", "Opposing Pilot", 2)

        with self.assertRaisesRegex(CrewAbilityError, "other untapped"):
            prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=2,
                response={"cost_cards": [opposing.ref]},
            )
        with self.assertRaisesRegex(CrewAbilityError, "distinct"):
            prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=2,
                response={"cost_cards": [own.ref, own.ref]},
            )
        with self.assertRaisesRegex(CrewAbilityError, "only one"):
            prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=2,
                response={
                    "cost_cards": [own.ref],
                    "cost_objects": [own.ref],
                },
            )
        set_permanent_tapped(
            engine,
            own.ref,
            actor="A",
            tapped=True,
            reason="Crew fixture",
            logical_object_id=own.logical_object_id,
            log=False,
        )
        with self.assertRaisesRegex(CrewAbilityError, "other untapped"):
            prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=2,
                response={"cost_cards": [own.ref]},
            )

    def test_unresolved_effective_power_fails_closed(self):
        session = self.session(70212212)
        engine = session.engine
        source = self.card(engine, "A", "Demonic Junker")
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        undefined_ref = engine.create_token(
            "A",
            name="Undefined Crew Pilot",
            characteristics={
                "type_line": "Token Creature — Pilot",
                "power": "*",
                "toughness": "4",
            },
            reason="ordinary Crew unresolved-power fixture",
        )[0]
        self.assertEqual(0, next(row.power for row in crew_candidates(engine, "A", source)
            if row.object_ref == undefined_ref))
        engine.create_token("A", name="Unresolved Crew Pilot", characteristics={
            "type_line": "Token Creature — Pilot", "power": "unrepresented", "toughness": "4"})

        with self.assertRaisesRegex(CrewAbilityError, "power is unresolved"):
            crew_candidates(engine, "A", source)

    def test_phased_source_is_unavailable_before_cost_commitment(self):
        session = self.session(70212214)
        engine = session.engine
        source, candidates, _ = self.prepare(session, powers=(2,))
        candidate = candidates[0]
        plan = prepare_crew_cost(
            engine,
            seat="A",
            source=source,
            threshold=2,
            response={"cost_cards": [candidate.ref]},
        )

        source.phased_out = True
        self.assertEqual((), crew_candidates(engine, "A", source))
        with self.assertRaisesRegex(CrewAbilityError, "no longer available"):
            prepare_crew_cost(
                engine,
                seat="A",
                source=source,
                threshold=2,
                response={"cost_cards": [candidate.ref]},
            )
        with self.assertRaisesRegex(CrewAbilityError, "changed"):
            commit_crew_cost(engine, plan)
        self.assertFalse(candidate.tapped)

    def test_current_effective_power_and_source_exclusion_share_one_cost_owner(self):
        session = self.session(70212203)
        engine = session.engine
        source, candidates, action_id = self.prepare(
            session,
            powers=(1,),
            power_counters=(1,),
        )
        candidate = candidates[0]
        self.assertEqual(2, engine._numeric_stat(candidate.object_id, "power"))

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [candidate.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_until(
            session,
            lambda: "creature" in self.effective_types(engine, source),
        )
        set_permanent_tapped(
            engine,
            candidate.ref,
            actor="A",
            tapped=False,
            reason="Crew fixture reset",
            logical_object_id=candidate.logical_object_id,
            log=False,
        )

        current = crew_candidates(engine, "A", source)
        self.assertEqual((candidate.object_id,), tuple(row.object_id for row in current))
        self.assertEqual((2,), tuple(row.power for row in current))
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine.pump()
        hint = next(
            row
            for row in engine._priority_action_hints("A")["abilities"]
            if row["s"] == source.ref and row["a"] == "ab3"
        )
        legal_refs = hint["choose_cost"][0]["legal_refs"]
        self.assertEqual([candidate.ref], legal_refs)
        self.assertNotIn(source.ref, legal_refs)

    def test_source_departure_before_resolution_does_not_affect_returned_object(self):
        session = self.session(70212204)
        engine = session.engine
        source, candidates, action_id = self.prepare(session, powers=(2,))
        original_incarnation = source.logical_object_id
        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [candidates[0].ref]},
        )
        self.assertTrue(result.ok, result.summary)
        engine.move_card(source.object_id, "graveyard", log=False)
        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertNotEqual(original_incarnation, source.logical_object_id)

        self.resolve_until(session, lambda: not engine.state.stack)

        self.assertNotIn("creature", self.effective_types(engine, source))

    def test_source_control_change_and_phasing_use_zone_object_identity(self):
        controlled = self.session(70212210, players=4)
        engine = controlled.engine
        source, candidates, action_id = self.prepare(
            controlled,
            powers=(2,),
        )
        result = controlled.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [candidates[0].ref]},
        )
        self.assertTrue(result.ok, result.summary)
        engine.change_control(
            source.object_id,
            "B",
            reason="ordinary Crew control-change fixture",
        )

        self.resolve_until(
            controlled,
            lambda: "creature" in self.effective_types(engine, source),
        )
        self.assertEqual("B", source.controller)
        self.assertIn("artifact", self.effective_types(engine, source))

        phased = self.session(70212211)
        engine = phased.engine
        source, candidates, action_id = self.prepare(phased, powers=(2,))
        result = phased.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [candidates[0].ref]},
        )
        self.assertTrue(result.ok, result.summary)
        source.phased_out = True

        self.resolve_until(phased, lambda: not engine.state.stack)

        source.phased_out = False
        self.assertNotIn("creature", self.effective_types(engine, source))

    def test_changed_crew_oracle_fails_closed_without_runtime_reparse(self):
        session = self.session(70212205)
        engine = session.engine
        source, _, _ = self.prepare(session)
        original_data = engine._effective_card_data

        def changed(card):
            data = dict(original_data(card))
            if getattr(card, "object_id", card) == source.object_id:
                data["executable_oracle_text"] = "Crew 1"
                data["activated_abilities"] = []
            return data

        with mock.patch.object(
            engine, "_effective_card_data", side_effect=changed
        ), mock.patch(
            "quorune.compiler.activated_ability_catalog.parse_activated_abilities",
            side_effect=AssertionError("runtime activation discovery recompiled Oracle"),
        ):
            abilities = engine._activated_abilities(source)
            self.assertFalse(
                any(ability.crew_threshold is not None for ability in abilities)
            )

    def test_four_player_crew_context_is_public_seat_scoped_and_replays(self):
        session = self.session(70212206, players=4)
        engine = session.engine
        source, candidates, action_id = self.prepare(session, powers=(2,))
        candidate = candidates[0]
        hidden_object_id = engine.state.players["A"].zones["library"][-1]
        opposing_before = json.dumps(
            session.packet("pilot:D", full=True),
            sort_keys=True,
        )
        self.assertNotIn(hidden_object_id, opposing_before)
        self.assertFalse(
            any(
                row.get("s") == source.ref
                for row in engine._priority_action_hints("B")["abilities"]
            )
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {"action_id": action_id, "cost_cards": [candidate.ref]},
        )
        self.assertTrue(result.ok, result.summary)
        stack_context = engine.state.stack[-1].context["crew"]
        self.assertEqual(2, stack_context["threshold"])
        self.assertEqual(1, len(stack_context["crewed_by"]))
        self.assertEqual(
            candidate.logical_object_id,
            stack_context["crewed_by"][0]["logical_object_id"],
        )
        opposing_after = json.dumps(
            session.packet("pilot:D", full=True),
            sort_keys=True,
        )
        self.assertNotIn(hidden_object_id, opposing_after)
        self.assertNotIn(candidate.object_id, opposing_after)
        self.assertIn(candidate.ref, opposing_after)
        self.resolve_until(
            session,
            lambda: "creature" in self.effective_types(engine, source),
        )
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "ordinary-crew-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)

        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_original_pilots_pay_crew_without_changing_actual_power_and_replay(self):
        session = self.session(70212218, players=4)
        engine = session.engine
        hotshot = self.add(engine, "Hotshot Mechanic", zone="hand")
        shorikai = self.add(engine, "Shorikai, Genesis Engine")
        prototype = self.add(engine, "Prodigy's Prototype")
        prototype.acquired_control_turn_count = -1
        caravan = self.add(engine, "Cultivator's Caravan")
        caravan.acquired_control_turn_count = -1
        plain_pilot = self.creature(engine, "A", "Ordinary Pilot", 1)
        for card in (hotshot, shorikai, prototype, caravan):
            self.assert_closed(session, card)
        self.prepare_native(session, mana={"C": 1, "W": 1})
        self.checkpoint(session)

        def actions_for(card):
            actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            return [row for row in actions if row["id"].startswith(f"activate:{card.ref}:")]

        def crew_actions(card):
            return [row for row in actions_for(card)
                if any(choice["k"] == "crew" for choice in row.get("cost_summary", {}).get("choose_cost", []))]

        self.assertFalse(crew_actions(caravan))
        self.cast(session, "A", hotshot)
        self.assertIsNone(self.resolve(session))
        hotshot = engine.state.cards[hotshot.object_id]
        self.assertEqual(2, engine._numeric_stat(hotshot.object_id, "power"))
        action, = crew_actions(caravan)
        before = authoritative_state_hash(session.state)
        for seat, candidate in (("D", hotshot), ("A", plain_pilot)):
            rejected = session.act(f"pilot:{seat}", {"action_id": action["id"], "cost_cards": [candidate.ref]})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [hotshot.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(4, session.state.stack[-1].context["crew"]["total_power"])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, engine._numeric_stat(hotshot.object_id, "power"))
        self.assertFalse(engine.state.cards[plain_pilot.object_id].tapped)

        action, = actions_for(shorikai)
        hand_before = set(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("choice.apnap", self.resolve(session).kind)
        new_cards = set(session.state.players["A"].zones["hand"]) - hand_before
        self.assertEqual(2, len(new_cards))
        chosen = session.state.cards[sorted(new_cards)[0]].ref
        for seat in "BCD":
            self.assertNotIn(chosen, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        result = session.act("pilot:A", {"action_id": "choose", "cards": [chosen]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))

        def created_pilots():
            return [card for card in session.state.cards.values() if card.is_token and card.object_id != plain_pilot.object_id
                and card.zone == "battlefield" and "pilot" in self.effective_subtypes(engine, card)]

        pilot, = created_pilots()
        self.assertEqual(1, engine._numeric_stat(pilot.object_id, "power"))
        action, = crew_actions(prototype)
        result = session.act("pilot:A", {"action_id": action["id"], "cost_cards": [pilot.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(3, session.state.stack[-1].context["crew"]["total_power"])
        self.assertIsNone(self.resolve(session))
        self.until(session, lambda game: game.state.pending_decision.kind == "combat.attackers")
        result = session.act("pilot:A", {"action_id": "attack", "attackers": {prototype.ref: "B", caravan.ref: "C"}})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, len(created_pilots()))
        untapped, = [card for card in created_pilots() if not card.tapped]
        candidates = {candidate.ref: candidate.power for candidate in crew_candidates(engine, "A", caravan)}
        self.assertEqual((1, 3), (engine._numeric_stat(untapped.object_id, "power"), candidates[untapped.ref]))
        self.replay(session, load=True)

    def test_crew_bonus_uses_current_ability_and_vehicle_type(self):
        session = self.session(70212219)
        engine = session.engine
        pilot = self.add(engine, "Hotshot Mechanic")
        caravan = self.add(engine, "Cultivator's Caravan")
        loss = self.add(engine, "Dress Down", seat="B", zone="hand")
        session.state.players["B"].mana_pool.update({"C": 1, "U": 1})
        caravan.annotations["copy_overrides"] = {"type_line": "Artifact"}
        candidate, = crew_candidates(engine, "A", caravan)
        self.assertEqual(2, candidate.power)
        del caravan.annotations["copy_overrides"]
        self.prepare_native(session)
        self.checkpoint(session)
        candidate, = crew_candidates(engine, "A", caravan)
        self.assertEqual(4, candidate.power)
        self.cast(session, "B", loss)
        self.assertIsNone(self.resolve(session))
        candidate, = crew_candidates(engine, "A", caravan)
        self.assertEqual((2, 2), (engine._numeric_stat(pilot.object_id, "power"), candidate.power))


if __name__ == "__main__":
    unittest.main()
