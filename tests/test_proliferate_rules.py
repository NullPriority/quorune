from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from quorune.errors import GameRuleError
from quorune.carddb import CardRecord
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import ORACLE_COMPILER_VERSION, generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity,
    validate_semantic_intent_identity,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime.intents import (
    ProliferateIntent,
    ProliferateSubject,
)
from quorune.semantics import SemanticProgram
from quorune.rules.capabilities import load_default_capability_registry
from tests.common import keep_all, load_assets, make_session


def generated_proliferate_trigger_record() -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-9000-000000701340",
        name="Generic Proliferate Trigger Fixture",
        mana_cost="{5}{U}",
        mana_value=6.0,
        type_line="Creature — Leviathan",
        oracle_text=(
            "When this creature enters, proliferate. (Choose any number of "
            "permanents and/or players, then give each another counter of "
            "each kind already there.)"
        ),
        power="5",
        toughness="6",
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=("Proliferate",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class ProliferateEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.database,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.stack.clear()
        engine.semantics.put(
            SemanticProgram(
                key="fixture:proliferate",
                label="Proliferate fixture",
                effects=[{"op": "proliferate"}],
            )
        )
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def add_permanent(
        self,
        engine,
        *,
        owner: str,
        name: str,
        ref: str,
    ):
        card = next(
            (
                candidate
                for candidate in engine.state.cards.values()
                if candidate.owner == owner and candidate.printed_name == name
            ),
            None,
        )
        if card is None:
            record = self.database.lookup(name)
            card = CardInstance(
                object_id=f"fixture:{ref}",
                ref=ref,
                oracle_id=record.oracle_id,
                printed_name=record.name,
                owner=owner,
                controller=owner,
                zone="battlefield",
                zone_timestamp=engine.state.event_sequence + 1,
                known_to=list(engine.seats),
                revealed_to=list(engine.seats),
            )
            engine.state.cards[card.object_id] = card
            engine.state.players[owner].zones["battlefield"].append(
                card.object_id
            )
        else:
            engine.move_card(card.object_id, "battlefield", controller=owner)
            card.ref = ref
        return card

    def stack_proliferate(self, engine, *, actor: str = "A") -> None:
        source = next(
            (
                card
                for card in engine.state.cards.values()
                if card.controller == actor and card.zone == "battlefield"
            ),
            None,
        )
        ref = engine._next_ref("S")
        engine.state.stack.append(
            StackItem(
                stack_id=engine._stable_runtime_id("stack", ref),
                ref=ref,
                kind="activated_ability",
                controller=actor,
                label="Proliferate fixture",
                source_object_id=(source.object_id if source is not None else None),
                semantic_key="fixture:proliferate",
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": (
                        source.logical_object_id if source is not None else None
                    )
                },
            )
        )
        engine._prepare_stack_resolution()

    @staticmethod
    def choose(session, seat: str, refs: list[str]):
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "objects": refs,
                "plan": "PROLIFERATE",
                "reason": "Increase every selected counter kind once.",
            },
        )
        return result

    def choose_replacement(self, session, seat: str) -> None:
        engine = session.engine
        packet = StateProjector(
            self.database, engine.state
        )._decision(f"pilot:{seat}")
        selected = packet["ctx"]["options"][0]["id"]
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "replacement": selected,
                "plan": "ORDER_REPLACEMENTS",
                "reason": "Resolve the applicable replacement order.",
            },
        )
        self.assertTrue(result.ok, result.summary)

    def test_proliferate_choice_increases_each_existing_counter_kind_once(self):
        session = self.session(7013401)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="B", name="Sol Ring", ref="proliferate-target"
        )
        target.counters.update({"charge": 2, "stun": 1})
        engine.state.players["A"].poison = 2
        engine.state.players["A"].energy = 3
        engine.state.players["A"].counters["experience"] = 4
        self.stack_proliferate(engine)

        result = self.choose(
            session,
            "A",
            ["A", target.ref],
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual({"charge": 3, "stun": 2}, target.counters)
        self.assertEqual(3, engine.state.players["A"].poison)
        self.assertEqual(4, engine.state.players["A"].energy)
        self.assertEqual(5, engine.state.players["A"].counters["experience"])
        events = [
            event
            for event in engine.state.events
            if event.code == "counter.add"
        ]
        self.assertEqual(5, len(events))
        self.assertEqual(
            [
                ("charge", target.ref),
                ("stun", target.ref),
                ("energy", "A"),
                ("experience", "A"),
                ("poison", "A"),
            ],
            [
                (
                    event.details["counter"],
                    event.details.get("object", event.details.get("player")),
                )
                for event in events
            ],
        )

    def test_generated_proliferate_trigger_runs_without_name_dispatch(self):
        session = self.session(7013407)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="B", name="Sol Ring", ref="generated-target"
        )
        target.counters["charge"] = 1
        record = generated_proliferate_trigger_record()
        from unittest.mock import patch
        by_oracle_id = self.database.by_oracle_id
        self.enterContext(patch.object(self.database, 'by_oracle_id', side_effect=lambda identity:
            record if identity == record.oracle_id else by_oracle_id(identity)))
        generated = next(
            program
            for program in generated_programs(
                self.database,
                record,
                trust_level="trusted",
                capability_registry=load_default_capability_registry(),
                capability_profile="commander_review",
            )
            if program.provenance.get("template_id")
            == "proliferate-once-v1"
        )
        for existing in engine.semantics.programs_for_oracle(
            record.oracle_id
        ):
            engine.semantics.remove(existing.key)
        engine.semantics.put(generated)
        source = CardInstance(
            object_id="fixture:generated-proliferate-source",
            ref="generated-proliferate-source",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="hand",
            known_to=["A"],
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["hand"].append(source.object_id)

        engine.move_card(
            source.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(generated.key, engine.state.stack[-1].semantic_key)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        result = self.choose(session, "A", [target.ref])

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(2, target.counters["charge"])
        self.assertEqual(
            ORACLE_COMPILER_VERSION,
            generated.provenance["authored_by"],
        )

    def test_proliferate_empty_choice_resolves_without_counter_mutation(self):
        session = self.session(7013406)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="B", name="Sol Ring", ref="unchosen-target"
        )
        target.counters["charge"] = 1
        self.stack_proliferate(engine)
        before = dict(target.counters)

        result = self.choose(session, "A", [])

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(before, target.counters)
        self.assertFalse(engine.state.stack)

    def test_proliferate_counter_replacements_suspend_whole_batch_before_mutation(
        self,
    ):
        session = self.session(7013402)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="A", name="Sol Ring", ref="replacement-target"
        )
        target.counters.update({"charge": 1, "stun": 1})
        engine.state.players["A"].poison = 1
        self.add_permanent(
            engine, owner="A", name="Doubling Season", ref="proliferate-double"
        )
        self.add_permanent(
            engine,
            owner="A",
            name="Doc Samson, Super Psychiatrist",
            ref="proliferate-add",
        )
        self.stack_proliferate(engine)

        result = self.choose(session, "A", [target.ref, "A"])

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual({"charge": 1, "stun": 1}, target.counters)
        self.assertEqual(1, engine.state.players["A"].poison)

    def test_proliferate_rejects_stale_subject_snapshot_without_mutation(self):
        session = self.session(7013403)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="B", name="Sol Ring", ref="stale-target"
        )
        target.counters["charge"] = 1
        stale = ProliferateIntent(
            actor="A",
            subjects=(
                ProliferateSubject(
                    subject_kind="permanent",
                    subject_id=target.object_id,
                    ref=target.ref,
                    counter_names=("charge",),
                    logical_object_id=target.logical_object_id,
                ),
            ),
            reason="Stale Proliferate fixture",
        )
        target.counters["stun"] = 1
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(GameRuleError, "counter set changed"):
            engine.proliferate_intent(stale)
        self.assertEqual({"charge": 1, "stun": 1}, target.counters)
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_four_player_proliferate_choice_is_public_and_actor_scoped(self):
        session = self.session(7013404, players=4)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="C", name="Sol Ring", ref="public-target"
        )
        target.counters["charge"] = 1
        engine.state.players["D"].counters["experience"] = 1
        self.stack_proliferate(engine)

        projector = StateProjector(self.database, engine.state)
        packet = projector._decision("pilot:A")
        self.assertIsNotNone(packet)
        self.assertIsNone(projector._decision("pilot:B"))
        self.assertEqual(
            [target.ref, "D"],
            packet["legal_actions"][0]["choice_schema"]["legal_refs"],
        )
        # Capability secrets are opaque random encodings. A short private ref
        # can occur inside one by chance without being projected as data.
        public_packet = copy.deepcopy(packet)
        public_packet.pop("cap", None)
        serialized = json.dumps(public_packet, sort_keys=True)
        for seat in engine.seats:
            for object_id in engine.state.players[seat].zones["hand"]:
                self.assertNotIn(engine.state.cards[object_id].ref, serialized)

    def test_proliferate_replacement_resume_replays_exactly(self):
        session = self.session(7013405)
        engine = session.engine
        target = self.add_permanent(
            engine, owner="A", name="Sol Ring", ref="replay-target"
        )
        target.counters.update({"charge": 1, "stun": 1})
        engine.state.players["A"].poison = 1
        self.add_permanent(
            engine, owner="A", name="Doubling Season", ref="replay-double"
        )
        self.add_permanent(
            engine,
            owner="A",
            name="Doc Samson, Super Psychiatrist",
            ref="replay-add",
        )
        self.stack_proliferate(engine)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = self.choose(session, "A", ["A", target.ref])
        self.assertTrue(result.ok, result.summary)
        self.choose_replacement(session, "A")
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "proliferate-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_proliferate_intent_identity_is_strict_and_material(self):
        intent = ProliferateIntent(
            actor="A",
            subjects=(
                ProliferateSubject(
                    subject_kind="player",
                    subject_id="A",
                    ref="A",
                    counter_names=("experience", "poison"),
                ),
            ),
            reason="Proliferate fixture",
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))

        unknown = copy.deepcopy(identity)
        unknown["future"] = True
        with self.assertRaisesRegex(SemanticChoiceError, "unknown future"):
            validate_semantic_intent_identity(kind, unknown)

        changed = copy.deepcopy(identity)
        changed["subjects"][0]["counter_names"] = ["poison"]
        self.assertNotEqual(
            identity,
            validate_semantic_intent_identity(kind, changed),
        )


if __name__ == "__main__":
    unittest.main()
