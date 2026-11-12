from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from common import DB_PATH, keep_all, load_assets, make_session, pass_current
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    ability_fragment_to_dict,
)
from quorune.attack_transition_model import (
    AttackKeywordTriggerOccurrence,
    AttackRecipient,
    AttackRecipientKind,
    AttackTransitionError,
    AttackTransitionEvent,
    AttackTransitionAssignment,
    AttackTransitionParticipant,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.ability_keyword_fragments import (
    AbilityKeywordFragmentLowering,
)
from quorune.mentor import (
    MentorTriggerOccurrence,
    derive_mentor_trigger_occurrences,
)
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.relative_power_target import (
    RelativePowerDepartureSnapshot,
    RelativePowerSourceSnapshot,
    RelativePowerTargetCondition,
    RelativePowerTargetError,
    current_effective_creature_power,
    pin_host_relative_power_source_departures,
    pin_relative_power_source_departures,
)
from quorune.target_predicates import (
    TargetPredicateError,
    target_predicate_matches,
)
from quorune.targets import TargetGroup
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    load_default_capability_registry,
)


def _mentor_card(text: str = "Mentor") -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000702134",
        name="Generic Mentor Fixture",
        mana_cost="{2}{W}",
        mana_value=3.0,
        type_line="Creature — Human Soldier",
        oracle_text=text,
        power="3",
        toughness="3",
        loyalty=None,
        defense=None,
        colors=("W",),
        color_identity=("W",),
        keywords=("Mentor",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _mentor_spec() -> CombatKeywordTriggerSpec:
    return CombatKeywordTriggerSpec(
        kind=CombatKeywordTriggerKind.MENTOR,
        amount=1,
    )


def _participant(
    object_id: str,
    reference: str,
    *,
    power: int,
    mentor_instances: int = 0,
) -> AttackTransitionParticipant:
    return AttackTransitionParticipant(
        object_id=object_id,
        logical_object_id=f"logical:{object_id}",
        reference=reference,
        controller="A",
        is_creature=True,
        trigger_specs=tuple(_mentor_spec() for _ in range(mentor_instances)),
        power=power if mentor_instances else None,
    )


def _event(*, mentor_instances: int = 1) -> AttackTransitionEvent:
    source = _participant(
        "mentor-source",
        "A01",
        power=3,
        mentor_instances=mentor_instances,
    )
    target = _participant("mentor-target", "A02", power=1)
    return AttackTransitionEvent.create(
        turn_sequence=3,
        priority_epoch=7,
        active_player="A",
        participants=(source, target),
        assignments=(
            AttackTransitionAssignment(
                attacker_object_id=source.object_id,
                recipient=AttackRecipient(
                    AttackRecipientKind.PLAYER,
                    "B",
                    "B",
                ),
            ),
            AttackTransitionAssignment(
                attacker_object_id=target.object_id,
                recipient=AttackRecipient(
                    AttackRecipientKind.PLAYER,
                    "B",
                    "B",
                ),
            ),
        ),
    )


class MentorModelAndCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.db = CardDatabase(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_relative_power_condition_is_strict_and_uses_lki_only_on_departure(self):
        condition = RelativePowerTargetCondition(
            source=RelativePowerSourceSnapshot(
                object_id="source",
                logical_object_id="logical:source",
                reference="A01",
                last_known_power=3,
            )
        )

        self.assertTrue(
            condition.permits(target_power=2, current_source_power=3)
        )
        self.assertFalse(
            condition.permits(target_power=3, current_source_power=3)
        )
        self.assertFalse(
            condition.permits(target_power=2, current_source_power=2)
        )
        self.assertTrue(
            condition.permits(
                target_power=2,
                current_source_power=None,
                use_last_known=True,
            )
        )
        self.assertFalse(
            condition.permits(target_power=2, current_source_power=None)
        )
        self.assertEqual(
            condition,
            RelativePowerTargetCondition.from_dict(condition.to_dict()),
        )
        for malformed in (
            {**condition.to_dict(), "unknown": True},
            {**condition.to_dict(), "schema_version": True},
            {
                **condition.to_dict(),
                "source": {
                    **condition.to_dict()["source"],
                    "last_known_power": True,
                },
            },
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(RelativePowerTargetError):
                    RelativePowerTargetCondition.from_dict(malformed)

    def test_relative_power_target_predicate_is_an_independent_closed_owner(self):
        source = CardInstance(
            object_id="source",
            ref="A01",
            oracle_id="source-oracle",
            printed_name="Mentor source",
            owner="A",
            controller="A",
            zone="battlefield",
        )
        target = CardInstance(
            object_id="target",
            ref="A02",
            oracle_id="target-oracle",
            printed_name="Mentor target",
            owner="A",
            controller="A",
            zone="battlefield",
        )
        powers = {"source": 3, "target": 2}
        source_type_line = {"value": "Creature — Human Soldier"}
        host = SimpleNamespace(
            state=SimpleNamespace(cards={source.object_id: source}),
            _numeric_stat=lambda object_id, _stat: powers[object_id],
            _effective_card_data=lambda _card: {
                "type_line": source_type_line["value"]
            },
            _type_parts=lambda type_line: (
                {
                    value.casefold()
                    for value in type_line.split(" — ", 1)[0].split()
                },
                set(),
                set(),
            ),
        )
        condition = RelativePowerTargetCondition(
            source=RelativePowerSourceSnapshot(
                object_id=source.object_id,
                logical_object_id=source.logical_object_id,
                reference=source.ref,
                last_known_power=3,
            )
        )
        group = TargetGroup.from_mapping(
            {
                "predicate": "power_less_than_source",
                "resolution_condition": condition.to_dict(),
            }
        )
        row = {"category": "permanent", "card": target}
        arguments = {
            "types": {"creature"},
            "supertypes": set(),
            "colors": set(),
            "derived": {
                "artifact": False,
                "enchantment": False,
                "land": False,
            },
        }

        self.assertTrue(target_predicate_matches(host, group, row, **arguments))
        powers["source"] = 2
        self.assertFalse(target_predicate_matches(host, group, row, **arguments))
        powers["source"] = 3
        source_type_line["value"] = "Artifact — Vehicle"
        self.assertIsNone(current_effective_creature_power(host, source))
        self.assertFalse(target_predicate_matches(host, group, row, **arguments))
        source_type_line["value"] = "Creature — Human Soldier"
        source.phased_out = True
        with self.assertRaises(TargetPredicateError):
            target_predicate_matches(host, group, row, **arguments)
        with self.assertRaises(TargetPredicateError):
            target_predicate_matches(
                host,
                TargetGroup(predicate="unrepresented"),
                row,
                **arguments,
            )

    def test_relative_power_departure_replaces_only_matching_lki_snapshot(self):
        condition = RelativePowerTargetCondition(
            source=RelativePowerSourceSnapshot(
                object_id="source",
                logical_object_id="source@0",
                reference="A01",
                last_known_power=3,
            )
        )
        original_schema = {
            "groups": [
                {
                    "id": "target",
                    "predicate": "power_less_than_source",
                    "resolution_condition": condition.to_dict(),
                }
            ]
        }
        item = SimpleNamespace(
            context={"target_schema_override": original_schema}
        )

        updated = pin_relative_power_source_departures(
            (item,),
            (
                RelativePowerDepartureSnapshot(
                    object_id="source",
                    logical_object_id="source@0",
                    last_known_power=5,
                ),
            ),
        )

        self.assertEqual(1, updated)
        pinned = RelativePowerTargetCondition.from_dict(
            item.context["target_schema_override"]["groups"][0][
                "resolution_condition"
            ]
        )
        self.assertEqual(5, pinned.source.last_known_power)
        self.assertEqual(
            3,
            original_schema["groups"][0]["resolution_condition"][
                "source"
            ]["last_known_power"],
        )
        with self.assertRaises(RelativePowerTargetError):
            pin_relative_power_source_departures(
                (item,),
                (
                    RelativePowerDepartureSnapshot(
                        "source", "source@0", 5
                    ),
                    RelativePowerDepartureSnapshot(
                        "source", "source@0", 5
                    ),
                ),
            )

    def test_relative_power_departure_preparation_is_transactional(self):
        condition = RelativePowerTargetCondition(
            source=RelativePowerSourceSnapshot(
                object_id="source",
                logical_object_id="source@0",
                reference="A01",
                last_known_power=3,
            )
        )
        original = {
            "target_schema_override": {
                "groups": [
                    {
                        "predicate": "power_less_than_source",
                        "resolution_condition": condition.to_dict(),
                    }
                ]
            }
        }
        valid_item = SimpleNamespace(context=deepcopy(original))
        malformed_item = SimpleNamespace(
            context={
                "target_schema_override": {
                    "groups": [
                        {
                            "predicate": "power_less_than_source",
                            "resolution_condition": {"malformed": True},
                        }
                    ]
                }
            }
        )

        with self.assertRaises(RelativePowerTargetError):
            pin_relative_power_source_departures(
                (valid_item, malformed_item),
                (
                    RelativePowerDepartureSnapshot(
                        "source", "source@0", 5
                    ),
                ),
            )

        self.assertEqual(original, valid_item.context)

    def test_noncreature_departure_pins_absent_power(self):
        condition = RelativePowerTargetCondition(
            source=RelativePowerSourceSnapshot(
                object_id="source",
                logical_object_id="source@0",
                reference="A01",
                last_known_power=3,
            )
        )
        item = SimpleNamespace(
            context={
                "target_schema_override": {
                    "groups": [
                        {
                            "predicate": "power_less_than_source",
                            "resolution_condition": condition.to_dict(),
                        }
                    ]
                }
            }
        )
        card = SimpleNamespace(
            object_id="source",
            logical_object_id="source@0",
            zone="battlefield",
        )
        host = SimpleNamespace(
            state=SimpleNamespace(stack=[item]),
            _effective_card_data=lambda _card: {
                "type_line": "Artifact — Vehicle"
            },
            _type_parts=lambda _type_line: (
                {"artifact"},
                {"vehicle"},
                set(),
            ),
            _numeric_stat=lambda _object_id, _stat: 5,
        )

        self.assertEqual(
            1,
            pin_host_relative_power_source_departures(host, (card,)),
        )
        pinned = RelativePowerTargetCondition.from_dict(
            item.context["target_schema_override"]["groups"][0][
                "resolution_condition"
            ]
        )
        self.assertIsNone(pinned.source.last_known_power)
        self.assertFalse(
            pinned.permits(
                target_power=-1,
                current_source_power=None,
                use_last_known=True,
            )
        )

    def test_mentor_occurrences_preserve_multiplicity_and_identity(self):
        event = _event(mentor_instances=2)
        occurrences = derive_mentor_trigger_occurrences(event)

        self.assertEqual(2, len(occurrences))
        self.assertEqual([0, 1], [value.instance_index for value in occurrences])
        self.assertEqual([3, 3], [value.source_power for value in occurrences])
        self.assertEqual(2, len({value.occurrence_id for value in occurrences}))
        self.assertEqual(
            occurrences,
            tuple(
                MentorTriggerOccurrence.from_dict(value.to_dict())
                for value in occurrences
            ),
        )

        malformed = deepcopy(occurrences[0].to_dict())
        malformed["source_power"] = True
        with self.assertRaises(AttackTransitionError):
            MentorTriggerOccurrence.from_dict(malformed)

        with self.assertRaisesRegex(
            AttackTransitionError,
            "untargeted attack-trigger kind",
        ):
            AttackKeywordTriggerOccurrence.create(
                transition_id=event.transition_id,
                kind=CombatKeywordTriggerKind.MENTOR,
                controller=occurrences[0].controller,
                source=occurrences[0].source,
                affected=(occurrences[0].source,),
                amount=1,
                instance_index=0,
            )
        malformed = deepcopy(occurrences[0].to_dict())
        malformed["source_power"] = 4
        with self.assertRaises(AttackTransitionError):
            MentorTriggerOccurrence.from_dict(malformed)

    def test_mentor_compiles_with_precise_spans_and_capability_closure(self):
        text = "Mentor, mentor"
        record = replace(_mentor_card(), oracle_text=text)
        ir = compile_oracle_card(
            record,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

        self.assertEqual("exact", ir.status)
        self.assertEqual(2, len(ir.faces[0].nodes))
        self.assertEqual(
            ["mentor", "mentor"],
            [
                text[node.span.start : node.span.end].casefold()
                for node in ir.faces[0].nodes
            ],
        )
        program = compile_card_program(
            self.db,
            _mentor_card(),
            capability_registry=self.capabilities,
            capability_profile="commander_review",
            trust_level="trusted",
        )
        self.assertEqual(
            ("counter.producer.mentor",),
            program.capability_dependencies,
        )
        self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
        self.assertTrue(program.trust_closure["trusted"])

    def test_unsupported_mentor_wording_and_blocked_dependency_fail_closed(self):
        equivalent = replace(
            _mentor_card(),
            oracle_text=(
                "Whenever this creature attacks, put a +1/+1 counter on "
                "target attacking creature with lesser power."
            ),
        )
        ir = compile_oracle_card(
            equivalent,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

        value = json.loads(
            DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8")
        )
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "target.revalidate_resolution"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        blocked = compile_oracle_card(
            _mentor_card(),
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", blocked.status)
        self.assertTrue(
            any(
                "target.revalidate_resolution" in blocker
                for residual in blocked.material_residuals
                for blocker in residual.blockers
            )
        )

    def test_mentor_compiler_mutant_is_killed(self):
        with patch(
            "quorune.compiler.ability_keyword_fragments._lower_combat_keyword_fragments",
            return_value=AbilityKeywordFragmentLowering(),
        ):
            ir = compile_oracle_card(
                _mentor_card(),
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                handler.get("handler_id") == "ability.trigger.mentor.v1"
                for node in ir.faces[0].nodes
                for handler in node.handlers
            )
        )


class MentorRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

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
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.phase_index = 5
        engine.state.phase = "combat"
        engine.state.step = "declare_attackers"
        engine.state.combat = CombatState()
        session.commands.clear()
        session.decisions.clear()
        return session

    @staticmethod
    def permanent(
        engine,
        name: str,
        *,
        power: int,
        mentor_instances: int = 0,
    ):
        fragments = [
            ability_fragment_to_dict(_mentor_spec())
            for _ in range(mentor_instances)
        ]
        ref = engine.create_token(
            "A",
            name=name,
            characteristics={
                "type_line": "Token Creature — Test",
                "power": str(power),
                "toughness": "4",
                "ability_fragments": fragments,
            },
            temporary_keywords=("Haste",),
        )[0]
        return engine._resolve_object("A", ref, zones={"battlefield"})

    def database_permanent(
        self,
        engine,
        name: str,
        *,
        ref: str,
        seat: str = "A",
    ) -> CardInstance:
        record = self.db.lookup(name)
        from common import register_current_fixture_programs
        register_current_fixture_programs(self.db, engine.semantics, (record,),
                                          profile=engine.state.config.review_profile)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones["battlefield"].append(card.object_id)
        return card

    @staticmethod
    def declare(session, *cards):
        session.engine._issue_attackers()
        return session.act(
            "pilot:A",
            {
                "a": "attack",
                "atk": {card.ref: "B" for card in cards},
            },
        )

    @staticmethod
    def choose_target(session, ref: str):
        return session.act(
            "pilot:A",
            {"action_id": "choose", "targets": [ref]},
        )

    @staticmethod
    def resolve_one(session):
        for _seat in session.engine.active_seats:
            pass_current(session)

    def test_mentor_offers_only_attacking_creatures_with_lesser_power(self):
        session = self.session(702_134_001, players=4)
        engine = session.engine
        source = self.permanent(
            engine,
            "Mentor source",
            power=3,
            mentor_instances=1,
        )
        lesser = self.permanent(engine, "Lesser attacker", power=2)
        equal = self.permanent(engine, "Equal attacker", power=3)
        greater = self.permanent(engine, "Greater attacker", power=4)
        nonattacker = self.permanent(engine, "Lesser nonattacker", power=1)

        result = self.declare(session, source, lesser, equal, greater)

        self.assertTrue(result.ok, result.summary)
        decision = engine.state.pending_decision
        self.assertEqual("semantic.target", decision.kind)
        schema = decision.payload_by_actor["A"]["target_schema"]
        self.assertEqual([lesser.ref], schema["legal_refs"])
        packet = StateProjector(self.db, engine.state)._decision("pilot:A")
        self.assertEqual(
            [lesser.ref], packet["ctx"]["target_schema"]["legal_refs"]
        )
        packet_without_capability = deepcopy(packet)
        packet_without_capability.pop("cap", None)
        self.assertNotIn(source.object_id, str(packet_without_capability))
        self.assertNotIn(
            source.logical_object_id, str(packet_without_capability)
        )
        for seat in ("B", "C", "D"):
            self.assertIsNone(
                StateProjector(self.db, engine.state)._decision(
                    f"pilot:{seat}"
                )
            )

    def test_mentor_places_replacement_aware_counter_after_target_revalidation(self):
        session = self.session(702_134_002)
        self.database_permanent(
            session.engine,
            "Doubling Season",
            ref="mentor-doubling-season",
        )
        source = self.permanent(
            session.engine,
            "Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(session.engine, "Mentored attacker", power=2)
        self.assertTrue(self.declare(session, source, target).ok)
        self.assertTrue(self.choose_target(session, target.ref).ok)

        self.resolve_one(session)

        self.assertEqual(2, target.counters["+1/+1"])

    def test_current_power_change_can_make_mentor_target_illegal(self):
        session = self.session(702_134_003)
        source = self.permanent(
            session.engine,
            "Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(session.engine, "Mentored attacker", power=2)
        self.assertTrue(self.declare(session, source, target).ok)
        self.assertTrue(self.choose_target(session, target.ref).ok)
        target.counters["+1/+1"] = 1

        self.resolve_one(session)

        self.assertEqual(1, target.counters["+1/+1"])
        self.assertFalse(session.state.stack)
        self.assertTrue(
            any(event.code == "target.illegal" for event in session.state.events)
        )

    def test_illegal_mentor_target_rejects_without_mutation(self):
        session = self.session(702_134_007)
        source = self.permanent(
            session.engine,
            "Rollback Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(session.engine, "Legal attacker", power=2)
        nonattacker = self.permanent(
            session.engine,
            "Illegal nonattacker",
            power=1,
        )
        self.assertTrue(self.declare(session, source, target).ok)
        before = authoritative_state_hash(session.state)

        result = self.choose_target(session, nonattacker.ref)

        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertEqual("semantic.target", session.state.pending_decision.kind)

    def test_departed_mentor_source_uses_public_last_known_power(self):
        session = self.session(702_134_004)
        engine = session.engine
        source = self.permanent(
            engine,
            "Departing Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(engine, "Mentored attacker", power=2)
        self.assertTrue(self.declare(session, source, target).ok)
        self.assertTrue(self.choose_target(session, target.ref).ok)
        engine.move_card(source.object_id, "graveyard")

        self.resolve_one(session)

        self.assertEqual(1, target.counters["+1/+1"])

    def test_departure_pins_power_after_trigger_creation(self):
        session = self.session(702_134_008)
        engine = session.engine
        source = self.permanent(
            engine,
            "Changing Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(engine, "Changing target", power=2)
        self.assertTrue(self.declare(session, source, target).ok)
        self.assertTrue(self.choose_target(session, target.ref).ok)
        source.counters["+1/+1"] = 1
        target.counters["+1/+1"] = 1
        engine.move_card(source.object_id, "graveyard")

        self.resolve_one(session)

        self.assertEqual(2, target.counters["+1/+1"])

    def test_no_lesser_attacker_removes_mandatory_target_trigger(self):
        session = self.session(702_134_005)
        source = self.permanent(
            session.engine,
            "Unmatched Mentor source",
            power=3,
            mentor_instances=1,
        )

        result = self.declare(session, source)

        self.assertTrue(result.ok, result.summary)
        self.assertFalse(session.state.stack)
        decision = session.state.pending_decision
        self.assertTrue(decision is None or decision.kind != "semantic.target")
        self.assertTrue(
            any(
                event.code == "stack.trigger.removed"
                for event in session.state.events
            )
        )

    def test_mentor_target_choice_and_counter_replay_exactly(self):
        session = self.session(702_134_006)
        source = self.permanent(
            session.engine,
            "Replay Mentor source",
            power=3,
            mentor_instances=1,
        )
        target = self.permanent(session.engine, "Replay target", power=2)
        session.engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        self.assertTrue(
            session.act(
                "pilot:A",
                {
                    "a": "attack",
                    "atk": {source.ref: "B", target.ref: "B"},
                },
            ).ok
        )
        self.assertTrue(self.choose_target(session, target.ref).ok)
        self.resolve_one(session)
        expected_hash = authoritative_state_hash(session.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "mentor-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
