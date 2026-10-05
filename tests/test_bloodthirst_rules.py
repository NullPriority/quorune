from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.bloodthirst import (
    BLOODTHIRST_HANDLER_ID,
    BloodthirstError,
    BloodthirstSpec,
)
from quorune.carddb import CardDatabase
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.semantic_runtime import (
    runtime_component_inventory,
    zone_replacements,
)
from quorune.semantic_runtime.zone_replacements import (
    capture_zone_change_replacement_snapshot,
    prepare_zone_change_replacement_snapshot,
)
from quorune.semantic_runtime.conditional_entry_counters import (
    ConditionalSelfEntryCounterHandler,
    ConditionalSelfEntryCounterNode,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.zone_replacement_model import (
    ZoneChangeSubjectSnapshot,
    ZoneChangeReplacementSnapshot,
    ZoneReplacementError,
)
from quorune.turn_history import opponent_was_dealt_damage_this_turn
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "bloodthirst.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "counter-replacement-cards.json",
            ROOT / "tests" / "fixtures" / "bloodthirst-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class BloodthirstCompilerAndModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
        cls.card = cls.db.lookup("Bloodscale Prowler")
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_fixed_bloodthirst_compiles_with_precise_spans(self):
        scab_clan = self.db.lookup("Scab-Clan Mauler")
        ir = compile_oracle_card(
            scab_clan,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )
        node = next(
            node
            for node in ir.faces[0].nodes
            if node.template_id
            == "bloodthirst-opponent-damage-entry-counter-v1"
        )
        self.assertEqual("exact", ir.status)
        self.assertEqual(("counter.producer.bloodthirst",), node.capability_dependencies)
        self.assertEqual("Bloodthirst 2", scab_clan.oracle_text[node.span.start:node.span.end])
        self.assertEqual(2, node.handlers[0]["amount"])

        repeated = replace(
            self.card,
            oracle_text="Bloodthirst 1, Bloodthirst 3",
            keywords=("Bloodthirst",),
        )
        nodes = [
            node
            for node in compile_oracle_card(
                repeated,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            ).faces[0].nodes
            if node.template_id
            == "bloodthirst-opponent-damage-entry-counter-v1"
        ]
        self.assertEqual([1, 3], [node.handlers[0]["amount"] for node in nodes])
        self.assertEqual(
            ["Bloodthirst 1", "Bloodthirst 3"],
            [repeated.oracle_text[node.span.start:node.span.end] for node in nodes],
        )
        programs = [
            program
            for program in generated_programs(
                self.db,
                repeated,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "bloodthirst-opponent-damage-entry-counter-v1"
        ]
        self.assertEqual(2, len(programs))
        self.assertEqual(2, len({program.key for program in programs}))

    def test_bloodthirst_models_and_descriptors_reject_malformed_values(self):
        descriptor = BloodthirstSpec(2).handler_descriptor()
        node = ConditionalSelfEntryCounterHandler().validate(descriptor)
        self.assertEqual(
            ConditionalSelfEntryCounterNode(
                "opponent_was_dealt_damage_this_turn",
                "+1/+1",
                2,
                "702.54a",
            ),
            node,
        )
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:B1",
            object_ref="B1",
            logical_object_id="logical:B1",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
            opponent_was_dealt_damage_this_turn=True,
        )
        effect = ConditionalSelfEntryCounterHandler().subject_replacement_effect(
            descriptor,
            subject=subject,
            component_id="program:bloodthirst:0",
        )
        self.assertEqual(
            {"eq": True},
            effect.conditions["opponent_was_dealt_damage_this_turn"],
        )
        for constructor, args, error in (
            (BloodthirstSpec, (0,), BloodthirstError),
            (BloodthirstSpec, (True,), BloodthirstError),
            (
                ConditionalSelfEntryCounterNode,
                ("unknown", "+1/+1", 1, "702.54a"),
                SemanticNodeError,
            ),
            (
                ConditionalSelfEntryCounterNode,
                ("opponent_was_dealt_damage_this_turn", "+1/+1", True, "702.54a"),
                SemanticNodeError,
            ),
        ):
            with self.subTest(constructor=constructor.__name__):
                with self.assertRaises(error):
                    constructor(*args)
        with self.assertRaises(SemanticNodeError):
            ConditionalSelfEntryCounterHandler().validate(
                {**descriptor, "unknown": True}
            )
        with self.assertRaises(ZoneReplacementError):
            replace(subject, opponent_was_dealt_damage_this_turn=1)

    def test_bloodthirst_runtime_component_is_registered_once(self):
        rows = [
            row
            for row in runtime_component_inventory()
            if row["handler_id"] == BLOODTHIRST_HANDLER_ID
        ]
        self.assertEqual(1, len(rows))
        self.assertEqual(
            "replacement.zone.conditional-self-entry-counter",
            rows[0]["family"],
        )
        self.assertEqual(
            ["counter.producer.bloodthirst"],
            rows[0]["capability_dependencies"],
        )

    def test_multiple_bloodthirst_instances_apply_independently(self):
        subject = ZoneChangeSubjectSnapshot(
            object_id="object:B2",
            object_ref="B2",
            logical_object_id="logical:B2",
            owner="A",
            controller="A",
            origin="stack",
            destination="battlefield",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
            opponent_was_dealt_damage_this_turn=True,
        )
        handler = ConditionalSelfEntryCounterHandler()
        effects = tuple(
            handler.subject_replacement_effect(
                BloodthirstSpec(amount).handler_descriptor(),
                subject=subject,
                component_id=f"program:bloodthirst:{index}",
            )
            for index, amount in enumerate((1, 2))
        )
        snapshot = ZoneChangeReplacementSnapshot(
                revision=0,
                event_sequence=0,
                apnap_order=("A", "B"),
                source_refs=(),
                subjects=(subject,),
                effects=effects,
            )
        prepared = prepare_zone_change_replacement_snapshot(
            snapshot,
            selections=(effects[0].effect_id,),
        )[subject.object_id]
        self.assertEqual(
            [1, 2],
            sorted(int(event.payload["amount"]) for event in prepared.counter_events),
        )

    def test_bloodthirst_x_and_invalid_values_remain_material_residuals(self):
        cases = (
            self.db.lookup("Indoraptor, the Perfect Hybrid"),
            replace(self.card, oracle_text="Bloodthirst 0"),
            replace(self.card, oracle_text="Bloodthirst -1"),
        )
        for card in cases:
            with self.subTest(text=card.oracle_text):
                ir = compile_oracle_card(
                    card,
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(
                    any(
                        "bloodthirst-unsupported-wording" in blocker
                        for residual in ir.material_residuals
                        for blocker in residual.blockers
                    )
                )

    def test_bloodthirst_dependency_and_compiler_mutations_fail_closed(self):
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        capability = next(
            row
            for row in value["capabilities"]
            if row["id"] == "counter.producer.bloodthirst"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["test mutation"]
        ir = compile_oracle_card(
            self.card,
            capability_registry=CapabilityRegistry(value),
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(
            any(
                "counter.producer.bloodthirst" in blocker
                for residual in ir.material_residuals
                for blocker in residual.blockers
            )
        )
        with patch("quorune.oracle_ir.bloodthirst_keyword_node", return_value=None):
            mutant = compile_oracle_card(
                self.card,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertFalse(
            any(
                node.template_id
                == "bloodthirst-opponent-damage-entry-counter-v1"
                for node in mutant.faces[0].nodes
            )
        )

    def test_turn_history_query_is_current_turn_and_positive_damage_only(self):
        from quorune.model import TurnHistory, TurnHistoryEvent

        history = TurnHistory(
            turn_sequence=5,
            events=[
                TurnHistoryEvent(kind="player_damaged", target="B", amount=0),
                TurnHistoryEvent(kind="player_damaged", target="C", amount=2),
            ],
        )
        self.assertTrue(
            opponent_was_dealt_damage_this_turn(
                history,
                turn_sequence=5,
                player="A",
                active_players=("A", "B", "C", "D"),
            )
        )
        self.assertFalse(
            opponent_was_dealt_damage_this_turn(
                history,
                turn_sequence=6,
                player="A",
                active_players=("A", "B", "C", "D"),
            )
        )


class BloodthirstRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls):
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
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def add_card(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
        controller: str | None = None,
        zone: str = "stack",
    ) -> CardInstance:
        record = self.db.lookup(name)
        current_controller = controller or seat
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=current_controller,
            zone=zone,
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        if zone in engine.state.players[seat].zones:
            engine.state.players[seat].zones[zone].append(card.object_id)
        return card

    def register_bloodthirst(
        self,
        engine,
        card: CardInstance,
        *,
        repeated: bool = False,
    ):
        record = self.db.by_oracle_id(card.oracle_id)
        if repeated:
            record = replace(
                record,
                oracle_text="Bloodthirst 1, Bloodthirst 2",
                keywords=("Bloodthirst",),
            )
        programs = [
            program
            for program in generated_programs(
                self.db,
                record,
                trust_level="trusted",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "bloodthirst-opponent-damage-entry-counter-v1"
        ]
        self.assertEqual(2 if repeated else 1, len(programs))
        for program in programs:
            engine.semantics.put(program)
        return programs

    def begin_entry(self, session, card: CardInstance) -> None:
        item = StackItem(
            stack_id=f"stack:{card.ref}",
            ref=f"S-{card.ref}",
            kind="spell",
            controller=card.controller,
            label=card.printed_name,
            card_object_id=card.object_id,
            default_destination="battlefield",
            visibility=list(session.engine.seats),
        )
        session.engine.state.stack.append(item)
        session.engine._continue_resolution(
            stack_ref=item.ref,
            effects=[],
            destination=None,
            note="Bloodthirst entry fixture",
        )

    @staticmethod
    def replacement_options(session, seat: str):
        decision = StateProjector(session.engine.card_db, session.state)._decision(
            f"pilot:{seat}"
        )
        return decision, [option["id"] for option in decision["ctx"]["options"]]

    def choose(self, session, seat: str, selection: str):
        result = session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "choices": {"replacement": selection}},
        )
        self.assertTrue(result.ok, result.summary)

    def test_opponent_damage_creates_mandatory_replacement_aware_entry_counters(self):
        session = self.session(7025401)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Scab-Clan Mauler",
            ref="qualified",
        )
        self.register_bloodthirst(engine, card)
        engine._record_turn_history(
            "player_damaged", actor="A", target="B", target_kind="player", amount=1
        )
        self.begin_entry(session, card)
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(2, card.counters["+1/+1"])

    def test_direct_nonspell_entry_uses_same_compiled_replacement(self):
        session = self.session(7025402)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Bloodscale Prowler",
            ref="direct-entry",
            zone="graveyard",
        )
        self.register_bloodthirst(engine, card)
        engine._record_turn_history(
            "player_damaged", actor="B", target="B", target_kind="player", amount=1
        )
        engine.move_card(card.object_id, "battlefield")
        self.assertEqual("battlefield", card.zone)
        self.assertEqual(1, card.counters["+1/+1"])

    def test_self_damage_no_damage_and_prior_turn_damage_do_not_qualify(self):
        for offset, target, amount, stale in (
            (0, None, 0, False),
            (1, "A", 3, False),
            (2, "B", 3, True),
        ):
            with self.subTest(target=target, amount=amount, stale=stale):
                session = self.session(7025410 + offset)
                engine = session.engine
                card = self.add_card(
                    engine,
                    seat="A",
                    name="Bloodscale Prowler",
                    ref=f"negative-{offset}",
                )
                self.register_bloodthirst(engine, card)
                if target is not None:
                    engine._record_turn_history(
                        "player_damaged",
                        actor="B",
                        target=target,
                        target_kind="player",
                        amount=amount,
                    )
                if stale:
                    engine.state.turn_sequence += 1
                    engine.state.turn_history.turn_sequence = (
                        engine.state.turn_sequence
                    )
                    engine.state.turn_history.events.clear()
                self.begin_entry(session, card)
                self.assertEqual("battlefield", card.zone)
                self.assertNotIn("+1/+1", card.counters)

    def test_bloodthirst_counter_uses_canonical_quantity_replacement(self):
        session = self.session(7025420)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            name="Scab-Clan Mauler",
            ref="doubled",
        )
        doubling = CardInstance(
            object_id="fixture:doubling",
            ref="doubling",
            oracle_id=self.db.lookup("Doubling Season").oracle_id,
            printed_name="Doubling Season",
            owner="A",
            controller="A",
            zone="battlefield",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[doubling.object_id] = doubling
        engine.state.players["A"].zones["battlefield"].append(doubling.object_id)
        self.register_bloodthirst(engine, card)
        engine._record_turn_history(
            "player_damaged", target="B", target_kind="player", amount=1
        )
        self.begin_entry(session, card)
        while engine.state.pending_decision is not None:
            _, options = self.replacement_options(session, "A")
            self.choose(session, "A", options[0])
        self.assertEqual(4, card.counters["+1/+1"])

    def test_four_player_destination_controller_fact_is_scoped_and_replays(self):
        session = self.session(7025430, players=4)
        engine = session.engine
        card = self.add_card(
            engine,
            seat="A",
            controller="C",
            name="Bloodscale Prowler",
            ref="controlled-repeated",
        )
        self.register_bloodthirst(engine, card)
        engine._record_turn_history(
            "player_damaged", actor="D", target="B", target_kind="player", amount=1
        )
        snapshot = capture_zone_change_replacement_snapshot(
            engine,
            ((card.object_id, "battlefield"),),
            destination_controllers={card.object_id: "C"},
        )
        self.assertTrue(
            snapshot.subjects[0].opponent_was_dealt_damage_this_turn
        )
        bloodthirst_effects = [
            effect
            for effect in snapshot.effects
            if effect.source_id == card.ref
            and "conditional-self-entry-counter" in effect.effect_id
        ]
        self.assertEqual(
            1,
            len(bloodthirst_effects),
            [(effect.effect_id, effect.source_id) for effect in snapshot.effects],
        )
        engine.state.stack.append(
            StackItem(
                stack_id=f"stack:{card.ref}",
                ref=f"S-{card.ref}",
                kind="spell",
                controller=card.controller,
                label=card.printed_name,
                card_object_id=card.object_id,
                default_destination="battlefield",
                visibility=list(engine.seats),
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        projected = {
            seat: json.dumps(
                StateProjector(self.db, engine.state)._snapshot(
                    f"pilot:{seat}"
                ),
                sort_keys=True,
            )
            for seat in engine.seats
        }
        for seat in engine.seats:
            hidden_ids = set(engine.state.players[seat].zones["hand"])
            for other in set(engine.seats) - {seat}:
                self.assertTrue(
                    all(object_id not in projected[other] for object_id in hidden_ids)
                )
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:C", {"action_id": "pass"})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        for seat in engine.seats:
            result = session.act(f"pilot:{seat}", {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        current = engine.state.cards[card.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual("C", current.controller)
        self.assertEqual(1, current.counters["+1/+1"])
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "bloodthirst-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_bloodthirst_runtime_mutation_is_killed(self):
        def assert_counter(seed: int) -> None:
            session = self.session(seed)
            engine = session.engine
            card = self.add_card(
                engine,
                seat="A",
                name="Bloodscale Prowler",
                ref=f"mutant-{seed}",
            )
            self.register_bloodthirst(engine, card)
            engine._record_turn_history(
                "player_damaged", target="B", target_kind="player", amount=1
            )
            self.begin_entry(session, card)
            self.assertEqual(1, card.counters.get("+1/+1", 0))

        assert_counter(7025440)
        original = zone_replacements._zone_change_snapshot_effects

        def remove_bloodthirst_effects(host, subjects, active_sources, prospective_objects):
            return tuple(
                effect
                for effect in original(host, subjects, active_sources, prospective_objects)
                if not effect.effect_id.startswith(
                    "replacement.zone.conditional-self-entry-counter.v1:"
                )
            )

        with patch(
            "quorune.semantic_runtime.zone_replacements."
            "_zone_change_snapshot_effects",
            side_effect=remove_bloodthirst_effects,
        ):
            with self.assertRaises(AssertionError):
                assert_counter(7025441)


if __name__ == "__main__":
    unittest.main()
