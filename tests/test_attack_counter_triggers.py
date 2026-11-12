from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session, pass_current
from quorune.ability_fragments import (
    CombatKeywordTriggerKind,
    CombatKeywordTriggerSpec,
    ability_fragment_to_dict,
)
from quorune.attack_counter_triggers import (
    AttackCounterTriggerOccurrence,
    AttackPlayerLifeSnapshot,
    AttackerPowerSnapshot,
    PlayerLifeTotal,
    TrainingQualification,
    derive_attack_counter_trigger_occurrences,
)
from quorune.attack_transition_model import (
    AttackRecipient,
    AttackRecipientKind,
    AttackTransitionAssignment,
    AttackTransitionError,
    AttackTransitionEvent,
    AttackTransitionParticipant,
)
from quorune.card_programs.adapters import compile_card_program
from quorune.carddb import CardRecord
from quorune.compiler.ability_keyword_fragments import (
    AbilityKeywordFragmentLowering,
)
from quorune.model import CardInstance, CombatState
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
    load_default_capability_registry,
)


def _keyword_card(
    keyword: str,
    *,
    text: str | None = None,
) -> CardRecord:
    suffix = "105" if keyword.casefold() == "dethrone" else "149"
    return CardRecord(
        oracle_id=f"00000000-0000-4000-8000-000000702{suffix}",
        name=f"Generic {keyword} Fixture",
        mana_cost="{2}{G}",
        mana_value=3.0,
        type_line="Creature — Human Soldier",
        oracle_text=text if text is not None else keyword,
        power="2",
        toughness="2",
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=(keyword,),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def _spec(kind: CombatKeywordTriggerKind) -> CombatKeywordTriggerSpec:
    return CombatKeywordTriggerSpec(kind=kind, amount=1)


def _participant(
    object_id: str,
    reference: str,
    *,
    power: int,
    kinds: tuple[CombatKeywordTriggerKind, ...] = (),
) -> AttackTransitionParticipant:
    return AttackTransitionParticipant(
        object_id=object_id,
        logical_object_id=f"logical:{object_id}",
        reference=reference,
        controller="A",
        is_creature=True,
        trigger_specs=tuple(_spec(kind) for kind in kinds),
        power=power,
    )


def _attack_event(
    participants: tuple[AttackTransitionParticipant, ...],
    recipients: tuple[AttackRecipient, ...],
) -> AttackTransitionEvent:
    return AttackTransitionEvent.create(
        turn_sequence=3,
        priority_epoch=7,
        active_player="A",
        participants=participants,
        assignments=tuple(
            AttackTransitionAssignment(
                attacker_object_id=source.object_id,
                recipient=recipient,
            )
            for source, recipient in zip(participants, recipients, strict=True)
        ),
    )


def _player_recipient(player: str) -> AttackRecipient:
    return AttackRecipient(AttackRecipientKind.PLAYER, player, player)


def _life_snapshot() -> AttackPlayerLifeSnapshot:
    return AttackPlayerLifeSnapshot(
        (
            PlayerLifeTotal("D", 20),
            PlayerLifeTotal("B", 40),
            PlayerLifeTotal("A", 50),
            PlayerLifeTotal("C", 50),
        )
    )


class AttackCounterTriggerModelAndCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_attack_counter_snapshots_are_canonical_strict_and_reconstructable(
        self,
    ):
        first = _life_snapshot()
        second = AttackPlayerLifeSnapshot(tuple(reversed(first.totals)))
        self.assertEqual(first, second)
        self.assertEqual(first, AttackPlayerLifeSnapshot.from_dict(first.to_dict()))

        source = _participant(
            "training-source",
            "A01",
            power=2,
            kinds=(CombatKeywordTriggerKind.TRAINING,),
        )
        greater = _participant("greater", "A02", power=3)
        qualification = TrainingQualification(
            source=source.identity,
            attacker_powers=(
                AttackerPowerSnapshot(greater.identity, 3),
                AttackerPowerSnapshot(source.identity, 2),
            ),
        )
        reconstructed = TrainingQualification.from_dict(
            qualification.to_dict()
        )
        self.assertEqual(qualification, reconstructed)
        self.assertEqual(
            ["A01", "A02"],
            [value.attacker.reference for value in qualification.attacker_powers],
        )
        occurrence = AttackCounterTriggerOccurrence.create(
            transition_id="attack-transition:fixture",
            kind=CombatKeywordTriggerKind.TRAINING,
            controller="A",
            source=source.identity,
            qualification=qualification,
            instance_index=0,
        )
        self.assertEqual(
            occurrence,
            AttackCounterTriggerOccurrence.from_dict(occurrence.to_dict()),
        )

        malformed = deepcopy(first.to_dict())
        malformed["totals"][0]["life"] = True
        with self.assertRaises(AttackTransitionError):
            AttackPlayerLifeSnapshot.from_dict(malformed)
        malformed = deepcopy(qualification.to_dict())
        malformed["attacker_powers"][1]["attacker"] = "A02"
        with self.assertRaises(AttackTransitionError):
            TrainingQualification.from_dict(malformed)
        malformed = deepcopy(qualification.to_dict())
        malformed["unknown"] = 1
        with self.assertRaises(AttackTransitionError):
            TrainingQualification.from_dict(malformed)
        malformed = deepcopy(occurrence.to_dict())
        malformed["source"] = "A01"
        with self.assertRaises(AttackTransitionError):
            AttackCounterTriggerOccurrence.from_dict(malformed)

    def test_dethrone_qualifies_only_direct_attacks_against_a_most_life_player(
        self,
    ):
        source = _participant(
            "dethrone-source",
            "A01",
            power=2,
            kinds=(CombatKeywordTriggerKind.DETHRONE,) * 2,
        )
        qualifying = _attack_event((source,), (_player_recipient("C"),))
        occurrences = derive_attack_counter_trigger_occurrences(
            qualifying,
            _life_snapshot(),
        )
        self.assertEqual(2, len(occurrences))
        self.assertEqual([0, 1], [value.instance_index for value in occurrences])
        self.assertEqual(2, len({value.occurrence_id for value in occurrences}))
        self.assertEqual(
            occurrences,
            tuple(
                AttackCounterTriggerOccurrence.from_dict(value.to_dict())
                for value in occurrences
            ),
        )

        lower_life = _attack_event((source,), (_player_recipient("B"),))
        self.assertEqual(
            (),
            derive_attack_counter_trigger_occurrences(
                lower_life,
                _life_snapshot(),
            ),
        )
        planeswalker = _attack_event(
            (source,),
            (
                AttackRecipient(
                    AttackRecipientKind.PLANESWALKER,
                    "Cpw",
                    "C",
                    "logical:Cpw",
                ),
            ),
        )
        self.assertEqual(
            (),
            derive_attack_counter_trigger_occurrences(
                planeswalker,
                _life_snapshot(),
            ),
        )

        tampered = deepcopy(occurrences[0].to_dict())
        tampered["qualification"]["attacked_player"] = "B"
        with self.assertRaises(AttackTransitionError):
            AttackCounterTriggerOccurrence.from_dict(tampered)

    def test_training_requires_another_strictly_greater_attacker_and_preserves_instances(
        self,
    ):
        source = _participant(
            "training-source",
            "A01",
            power=2,
            kinds=(CombatKeywordTriggerKind.TRAINING,) * 2,
        )
        greater = _participant("greater", "A02", power=3)
        event = _attack_event(
            (source, greater),
            (_player_recipient("B"), _player_recipient("B")),
        )
        occurrences = derive_attack_counter_trigger_occurrences(
            event,
            _life_snapshot(),
        )
        self.assertEqual(2, len(occurrences))
        self.assertEqual([0, 1], [value.instance_index for value in occurrences])
        self.assertEqual(2, len({value.occurrence_id for value in occurrences}))

        equal = _participant("equal", "A02", power=2)
        equal_event = _attack_event(
            (source, equal),
            (_player_recipient("B"), _player_recipient("B")),
        )
        self.assertEqual(
            (),
            derive_attack_counter_trigger_occurrences(
                equal_event,
                _life_snapshot(),
            ),
        )
        alone = _attack_event((source,), (_player_recipient("B"),))
        self.assertEqual(
            (),
            derive_attack_counter_trigger_occurrences(alone, _life_snapshot()),
        )

    def test_dethrone_and_training_compile_with_exact_spans_and_capability_closure(
        self,
    ):
        for keyword, capability in (
            ("Dethrone", "counter.producer.dethrone"),
            ("Training", "counter.producer.training"),
        ):
            with self.subTest(keyword=keyword):
                text = f"{keyword}, {keyword.casefold()}"
                record = replace(_keyword_card(keyword), oracle_text=text)
                ir = compile_oracle_card(
                    record,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", ir.status)
                self.assertEqual(1, len(ir.faces[0].nodes))
                self.assertEqual(
                    text.casefold(),
                    text[
                        ir.faces[0].nodes[0].span.start :
                        ir.faces[0].nodes[0].span.end
                    ].casefold(),
                )
                self.assertEqual(
                    [keyword.casefold(), keyword.casefold()],
                    [
                        handler["fragment"]["value"]["kind"]
                        for handler in ir.faces[0].nodes[0].handlers
                    ],
                )
                program = compile_card_program(
                    self.db,
                    _keyword_card(keyword),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                    trust_level="trusted",
                )
                self.assertEqual((capability,), program.capability_dependencies)
                self.assertEqual(
                    "capability_closed",
                    program.trust_closure["trust_basis"],
                )
                self.assertTrue(program.trust_closure["trusted"])

    def test_unsupported_attack_counter_wording_and_blocked_dependencies_fail_closed(
        self,
    ):
        equivalent = _keyword_card(
            "Dethrone",
            text=(
                "Whenever this creature attacks the player with the most "
                "life or tied for most life, put a +1/+1 counter on it."
            ),
        )
        listener = _keyword_card(
            "Training",
            text="Training\nWhenever this creature trains, draw a card.",
        )
        for record in (equivalent, listener):
            ir = compile_oracle_card(
                record,
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
            if row["id"] == "counter.producer.fixed_effect"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["test mutation"]
        registry = CapabilityRegistry(value)
        for keyword, dependency_id in (
            ("Dethrone", "counter.producer.fixed_effect"),
            ("Training", "counter.producer.fixed_effect"),
        ):
            ir = compile_oracle_card(
                _keyword_card(keyword),
                capability_registry=registry,
                capability_profile="commander_review",
            )
            self.assertNotEqual("exact", ir.status)
            self.assertTrue(
                any(
                    dependency_id in blocker
                    for residual in ir.material_residuals
                    for blocker in residual.blockers
                )
            )

    def test_attack_counter_compiler_mutant_is_killed(self):
        with patch(
            "quorune.compiler.ability_keyword_fragments._lower_combat_keyword_fragments",
            return_value=AbilityKeywordFragmentLowering(),
        ):
            for keyword, handler_id in (
                ("Dethrone", "ability.trigger.dethrone.v1"),
                ("Training", "ability.trigger.training.v1"),
            ):
                ir = compile_oracle_card(
                    _keyword_card(keyword),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertFalse(
                    any(
                        handler.get("handler_id") == handler_id
                        for node in ir.faces[0].nodes
                        for handler in node.handlers
                    )
                )


class AttackCounterTriggerRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

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
        kinds: tuple[CombatKeywordTriggerKind, ...] = (),
        card_object: bool = False,
    ):
        fragments = [
            ability_fragment_to_dict(_spec(kind)) for kind in kinds
        ]
        ref = engine.create_token(
            "A",
            name=name,
            characteristics={
                "type_line": "Creature — Test",
                "power": str(power),
                "toughness": "4",
                "ability_fragments": fragments,
            },
            temporary_keywords=("Haste",),
        )[0]
        card = engine._resolve_object("A", ref, zones={"battlefield"})
        if card_object:
            card.is_token = False
            card.object_kind = "card"
        return card

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
    def declare(session, attacks: dict[str, str]):
        session.engine._issue_attackers()
        return session.act("pilot:A", {"a": "attack", "atk": attacks})

    @staticmethod
    def order_current_triggers(session) -> None:
        decision = session.state.pending_decision
        if decision is None or decision.kind != "trigger.order":
            return
        refs = [
            row["id"]
            for row in decision.payload_by_actor["A"]["triggers"]
        ]
        result = session.act(
            "pilot:A",
            {"a": "order", "triggers": refs},
        )
        if not result.ok:
            raise AssertionError(result.summary)

    @staticmethod
    def resolve_stack(session) -> None:
        while session.state.stack:
            for _seat in tuple(session.engine.active_seats):
                if not session.state.stack:
                    break
                pass_current(session)

    def test_dethrone_uses_complete_four_player_life_snapshot_and_quantity_replacement(
        self,
    ):
        session = self.session(702_105_001)
        engine = session.engine
        engine.state.players["A"].life = 50
        engine.state.players["B"].life = 40
        engine.state.players["C"].life = 50
        engine.state.players["D"].life = 20
        self.database_permanent(
            engine,
            "Doubling Season",
            ref="dethrone-doubling-season",
        )
        source = self.permanent(
            engine,
            "Dethrone source",
            power=2,
            kinds=(CombatKeywordTriggerKind.DETHRONE,),
        )

        result = self.declare(session, {source.ref: "C"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        engine.state.players["C"].life = 1
        self.resolve_stack(session)

        self.assertEqual(2, source.counters["+1/+1"])

    def test_training_uses_declaration_power_and_quantity_replacement(self):
        session = self.session(702_149_001)
        engine = session.engine
        self.database_permanent(
            engine,
            "Doubling Season",
            ref="training-doubling-season",
        )
        source = self.permanent(
            engine,
            "Training source",
            power=2,
            kinds=(CombatKeywordTriggerKind.TRAINING,),
        )
        greater = self.permanent(engine, "Greater attacker", power=3)

        result = self.declare(
            session,
            {source.ref: "B", greater.ref: "B"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(1, len(session.state.stack))
        source.counters["+1/+1"] = 2
        self.resolve_stack(session)

        self.assertEqual(4, source.counters["+1/+1"])

    def test_attack_counter_source_identity_handles_departure_control_and_phasing(
        self,
    ):
        departed = self.session(702_105_002, players=2)
        source = self.permanent(
            departed.engine,
            "Departing Dethrone source",
            power=2,
            kinds=(CombatKeywordTriggerKind.DETHRONE,),
            card_object=True,
        )
        self.assertTrue(self.declare(departed, {source.ref: "B"}).ok)
        original_logical_id = source.logical_object_id
        departed.engine.move_card(source.object_id, "graveyard")
        departed.engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
        )
        self.assertNotEqual(original_logical_id, source.logical_object_id)
        self.resolve_stack(departed)
        self.assertEqual(0, source.counters.get("+1/+1", 0))

        controlled = self.session(702_149_002, players=2)
        training = self.permanent(
            controlled.engine,
            "Control-changing Training source",
            power=2,
            kinds=(CombatKeywordTriggerKind.TRAINING,),
        )
        greater = self.permanent(controlled.engine, "Greater attacker", power=3)
        self.assertTrue(
            self.declare(
                controlled,
                {training.ref: "B", greater.ref: "B"},
            ).ok
        )
        controlled.engine.change_control(
            training.object_id,
            "B",
            reason="focused Training regression",
        )
        self.resolve_stack(controlled)
        self.assertEqual("B", training.controller)
        self.assertEqual(1, training.counters["+1/+1"])

        phased = self.session(702_105_003, players=2)
        phased_source = self.permanent(
            phased.engine,
            "Phased Dethrone source",
            power=2,
            kinds=(CombatKeywordTriggerKind.DETHRONE,),
        )
        self.assertTrue(
            self.declare(phased, {phased_source.ref: "B"}).ok
        )
        phased_source.phased_out = True
        self.resolve_stack(phased)
        self.assertEqual(0, phased_source.counters.get("+1/+1", 0))

    def test_multiple_attack_counter_triggers_use_ordinary_ordering_and_hide_identity(
        self,
    ):
        session = self.session(702_149_003)
        engine = session.engine
        engine.state.players["C"].life = 50
        source = self.permanent(
            engine,
            "Shared attack-counter source",
            power=2,
            kinds=(
                CombatKeywordTriggerKind.DETHRONE,
                CombatKeywordTriggerKind.TRAINING,
            ),
        )
        greater = self.permanent(engine, "Ordering attacker", power=3)

        result = self.declare(
            session,
            {source.ref: "C", greater.ref: "C"},
        )
        self.assertTrue(result.ok, result.summary)
        decision = session.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("trigger.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        batch = session.state.pending_trigger_batches[0]
        self.assertEqual(2, len(batch.items))
        self.assertEqual(
            {"Dethrone", "Training"},
            {
                label
                for item in batch.items
                for label in ("Dethrone", "Training")
                if label in item["label"]
            },
        )
        for seat in engine.active_seats:
            packet = session.packet(f"pilot:{seat}", full=True)
            packet_without_capability = deepcopy(packet)
            decision_packet = packet_without_capability.get("decision")
            if isinstance(decision_packet, dict):
                decision_packet.pop("cap", None)
            packet_text = str(packet_without_capability)
            self.assertNotIn(source.object_id, packet_text)
            self.assertNotIn(source.logical_object_id, packet_text)
            self.assertNotIn(greater.object_id, packet_text)
            self.assertNotIn(greater.logical_object_id, packet_text)

    def test_attack_counter_trigger_replays_exactly(self):
        session = self.session(702_149_004)
        engine = session.engine
        engine.state.players["C"].life = 50
        source = self.permanent(
            engine,
            "Replay attack-counter source",
            power=2,
            kinds=(
                CombatKeywordTriggerKind.DETHRONE,
                CombatKeywordTriggerKind.TRAINING,
            ),
        )
        greater = self.permanent(engine, "Replay greater attacker", power=3)
        engine._issue_attackers()
        session.initial_checkpoint = checkpoint_envelope(session.state)
        result = session.act(
            "pilot:A",
            {
                "a": "attack",
                "atk": {source.ref: "C", greater.ref: "C"},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.order_current_triggers(session)
        self.resolve_stack(session)
        self.assertEqual(2, source.counters["+1/+1"])
        expected_hash = authoritative_state_hash(session.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "attack-counter-trigger"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
