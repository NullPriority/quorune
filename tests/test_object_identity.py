from __future__ import annotations

from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from common import keep_all, load_assets, make_session
from quorune.model import CardInstance, GameState, StackItem
from quorune.record import (
    checkpoint_envelope,
    replay_record,
)
from quorune.semantics import SemanticProgram
from quorune.targets import TargetGroup
from quorune.util import stable_json


class ObjectIdentityAndTokenLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_engine(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        return session.engine

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    def test_zone_changes_create_serialized_logical_incarnations(self):
        engine = self.make_engine(4007)
        card = self.card(engine, "A", "Sol Ring")
        initial = card.zone_change_counter
        initial_timestamp = card.zone_timestamp

        engine.move_card(card.object_id, "hand", log=False)
        self.assertEqual(initial + 1, card.zone_change_counter)
        self.assertGreater(card.zone_timestamp, initial_timestamp)
        hand_timestamp = card.zone_timestamp
        engine.move_card(card.object_id, "hand", log=False)
        self.assertEqual(initial + 1, card.zone_change_counter)
        self.assertEqual(hand_timestamp, card.zone_timestamp)
        engine.move_card(card.object_id, "exile", log=False)
        self.assertEqual(initial + 2, card.zone_change_counter)
        self.assertGreater(card.zone_timestamp, hand_timestamp)
        first_exile_timestamp = card.zone_timestamp
        engine.move_card(card.object_id, "exile", log=False)
        self.assertEqual(initial + 3, card.zone_change_counter)
        self.assertGreater(card.zone_timestamp, first_exile_timestamp)

        restored = GameState.from_dict(engine.state.to_dict())
        self.assertEqual(
            card.zone_change_counter,
            restored.cards[card.object_id].zone_change_counter,
        )
        self.assertEqual(
            card.zone_timestamp,
            restored.cards[card.object_id].zone_timestamp,
        )
        self.assertEqual(
            engine.state.timestamp_sequence,
            restored.timestamp_sequence,
        )

    def test_incarnation_counter_is_monotonic_under_zone_mutation(self):
        engine = self.make_engine(4014)
        card = self.card(engine, "A", "Sol Ring")
        randomizer = random.Random(4007)
        destinations = [
            "library",
            "hand",
            "graveyard",
            "exile",
            "command",
        ]
        expected = card.zone_change_counter

        for _ in range(100):
            origin = card.zone
            destination = randomizer.choice(destinations)
            if (
                origin != destination
                or origin in {"exile", "command"}
            ):
                expected += 1
            engine.move_card(
                card.object_id,
                destination,
                log=False,
            )
            self.assertEqual(expected, card.zone_change_counter)

    def test_draw_crosses_the_canonical_incarnation_boundary(self):
        engine = self.make_engine(4015)
        object_id = engine.state.players["A"].zones["library"][-1]
        card = engine.state.cards[object_id]
        initial = card.zone_change_counter
        card.annotations["stale_library_state"] = True
        card.counters["test"] = 1

        drawn = engine.draw("A", reason="identity regression")

        self.assertEqual([object_id], drawn)
        self.assertEqual("hand", card.zone)
        self.assertEqual(initial + 1, card.zone_change_counter)
        self.assertNotIn("stale_library_state", card.annotations)
        self.assertEqual({}, card.counters)

    def test_new_incarnation_forgets_state_except_entry_continuations(self):
        engine = self.make_engine(4016)
        card = self.card(engine, "A", "Sol Ring")
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        card.controller = "A"
        card.annotations.update(
            {
                "chosen_creature_type": "Goblin",
                "chosen_name": "Black Lotus",
                "copy_overrides": {"name": "Entry Copy"},
                "until_end_of_turn": {"power": 3},
            }
        )

        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        self.assertEqual(
            "Goblin", card.annotations["chosen_creature_type"]
        )
        self.assertEqual("Black Lotus", card.annotations["chosen_name"])
        self.assertEqual(
            {"name": "Entry Copy"}, card.annotations["copy_overrides"]
        )
        self.assertNotIn("until_end_of_turn", card.annotations)

    def test_permanent_spell_keeps_its_logical_incarnation(self):
        engine = self.make_engine(4017)
        card = self.card(engine, "A", "Sol Ring")
        engine._remove_from_zone(card)
        engine._reset_zone_change(card, "stack")
        card.zone = "stack"
        stack_incarnation = card.logical_object_id
        stack_timestamp = card.zone_timestamp

        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        self.assertEqual(
            stack_incarnation,
            card.logical_object_id,
        )
        self.assertGreater(card.zone_timestamp, stack_timestamp)

        card.counters["test"] = 1
        engine.move_card(card.object_id, "graveyard", log=False)

        self.assertNotIn("chosen_creature_type", card.annotations)
        self.assertNotIn("chosen_name", card.annotations)
        self.assertNotIn("copy_overrides", card.annotations)
        self.assertEqual({}, card.counters)

    def test_return_transformed_sets_the_face_before_enter_events(self):
        engine = self.make_engine(4017)
        record = self.db.lookup("Tithing Blade")
        card = CardInstance(
            object_id="return-transformed-timing",
            ref="A-transform",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="exile",
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players["A"].zones["exile"].append(card.object_id)
        observed_faces: list[str | None] = []
        original = engine._dispatch_zone_change_events

        def observe_enter(moved_card, *args, **kwargs):
            if kwargs.get("destination") == "battlefield":
                observed_faces.append(moved_card.active_face)
            return original(moved_card, *args, **kwargs)

        with patch.object(
            engine,
            "_dispatch_zone_change_events",
            side_effect=observe_enter,
        ):
            engine.apply_effect(
                {
                    "op": "return_transformed",
                    "card": card.ref,
                },
                actor=card.owner,
            )

        self.assertEqual(["Consuming Sepulcher"], observed_faces)
        self.assertEqual("Consuming Sepulcher", card.active_face)

    def test_target_that_leaves_and_returns_is_a_new_illegal_object(self):
        engine = self.make_engine(4008)
        target = self.card(engine, "A", "Sol Ring")
        source = self.card(engine, "B", "Force of Vigor")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine._remove_from_zone(source)
        engine._reset_zone_change(source, "stack")
        source.zone = "stack"
        source.controller = "B"
        program = SemanticProgram(
            key="test:incarnation-target",
            label="Destroy target artifact",
            oracle_id=source.oracle_id,
            ability_id="test:incarnation-target",
            effects=[{"op": "destroy", "card": "$target.0"}],
            destination="graveyard",
            target_schema={
                "zones": ["battlefield"],
                "categories": ["permanent"],
                "artifact": True,
                "count": 1,
            },
            trust_level="provisional",
        )
        engine.semantics.put(program)
        selected, grouped = engine._validate_semantic_targets(
            "B",
            program,
            [target.ref],
            source_ref=source.ref,
        )
        item = StackItem(
            stack_id="incarnation-target-test",
            ref="S-incarnation",
            kind="spell",
            controller="B",
            label=program.label,
            card_object_id=source.object_id,
            semantic_key=program.key,
            targets=selected,
            default_destination="graveyard",
            visibility=["A", "B"],
            context={
                "target_groups": grouped,
                "target_snapshots": {
                    target.ref: engine._target_snapshot(target.ref)
                },
                "targets_revalidated": False,
            },
        )
        engine.state.stack.append(item)

        engine.move_card(
            target.object_id,
            "graveyard",
            reason="flicker departure",
            log=False,
        )
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="A",
            reason="flicker return",
            log=False,
        )
        engine._prepare_stack_resolution()

        self.assertEqual("battlefield", target.zone)
        self.assertEqual("graveyard", source.zone)
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "target.illegal"
        )
        self.assertEqual(
            "object_identity_changed", event.details["reason"]
        )

    def test_logical_identity_metadata_is_not_projected_to_pilots(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=4011,
        )
        keep_all(session)
        card = self.card(session.engine, "A", "Sol Ring")
        session.engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )

        packet_text = stable_json(session.packet("pilot:B", full=True))

        self.assertNotIn(card.object_id, packet_text)
        self.assertNotIn("zone_change_counter", packet_text)
        self.assertNotIn("zone_timestamp", packet_text)
        self.assertNotIn("world_supertype_timestamp", packet_text)
        self.assertNotIn("logical_object_id", packet_text)

    def test_token_changes_zone_before_ceasing_at_next_state_check(self):
        engine = self.make_engine(1117)
        token_ref = engine.create_token(
            "A",
            name="Lifecycle Saproling",
            characteristics={
                "type_line": "Token Creature — Saproling",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        token = self.card(engine, "A", "Lifecycle Saproling")

        engine.move_card(
            token.object_id,
            "graveyard",
            reason="test sacrifice",
            semantic_events=True,
        )

        self.assertEqual("graveyard", token.zone)
        self.assertIn(
            token.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        self.assertTrue(token.has_left_battlefield)
        self.assertFalse(engine._stabilize())
        self.assertEqual("outside", token.zone)
        self.assertNotIn(
            token.object_id,
            engine.state.players["A"].zones["graveyard"],
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "state.objects_ceased"
        )
        self.assertEqual(
            [{"object": token_ref, "kind": "token", "zone": "graveyard"}],
            event.details["objects"],
        )

    def test_token_that_left_battlefield_cannot_move_again(self):
        engine = self.make_engine(1118)
        token_ref = engine.create_token(
            "A",
            name="No Return Saproling",
            characteristics={
                "type_line": "Token Creature — Saproling",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        token = self.card(engine, "A", "No Return Saproling")
        engine.move_card(
            token.object_id,
            "graveyard",
            reason="first move",
            log=False,
        )
        graveyard_incarnation = token.zone_change_counter

        engine.move_card(
            token.object_id,
            "battlefield",
            controller="A",
            reason="attempted return",
        )

        self.assertEqual("graveyard", token.zone)
        self.assertEqual(
            graveyard_incarnation, token.zone_change_counter
        )
        self.assertTrue(
            any(
                event.code == "zone.move.prevented"
                and event.details.get("object") == token_ref
                and event.details.get("rule") == "111.8"
                for event in engine.state.events
            )
        )

    def test_token_cost_cessation_replays_exactly(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
            seed=4012,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        commander_id = engine.state.players["B"].zones["command"][0]
        commander = engine.move_card(
            commander_id,
            "battlefield",
            controller="B",
            log=False,
        )
        commander.acquired_control_turn_count = (
            engine.state.players["B"].turns_begun - 1
        )
        token_ref = engine.create_token(
            "B",
            name="Replay Fodder",
            characteristics={
                "type_line": "Token Creature — Saproling",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        engine.state.priority_player = "B"
        hints = engine._priority_action_hints("B")
        action = next(
            row
            for row in hints["actions"]
            if row.get("source") == commander.ref
            and row.get("ability") == "ab2"
        )
        engine._issue_priority("B", hints)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:B",
            {
                "action_id": action["id"],
                "cost_cards": [token_ref],
                "plan": "DEVELOP_VALUE",
                "reason": "Sacrifice the token to exercise its lifecycle.",
            },
        )

        self.assertTrue(result.ok, result.summary)
        token = self.card(engine, "B", "Replay Fodder")
        self.assertEqual("outside", token.zone)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "record"
            session.save(record_dir)
            replay = replay_record(
                record_dir,
                self.db,
                verify=True,
            )
        self.assertTrue(replay["ok"], replay)

    def test_nonbattlefield_token_is_not_a_card_target(self):
        engine = self.make_engine(1116)
        token_ref = engine.create_token(
            "A",
            name="Not A Card",
            characteristics={
                "type_line": "Token Creature — Saproling",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        token = self.card(engine, "A", "Not A Card")
        engine.move_card(token.object_id, "graveyard", log=False)
        group = TargetGroup.from_mapping(
            {
                "zones": ["graveyard"],
                "categories": ["card"],
                "creature": True,
                "count": 1,
            }
        )

        self.assertNotIn(
            token_ref,
            engine._target_candidates("A", group),
        )

    def test_linked_move_requires_the_recorded_incarnation(self):
        engine = self.make_engine(4009)
        card = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(card.object_id, "graveyard", log=False)
        linked_incarnation = card.zone_change_counter
        engine.move_card(card.object_id, "exile", log=False)
        engine.move_card(card.object_id, "graveyard", log=False)

        result = engine.apply_effect(
            {
                "op": "move_if_in_zone",
                "card": card.ref,
                "from": "graveyard",
                "destination": "battlefield",
                "controller": "A",
                "expected_zone_change_counter": linked_incarnation,
                "reason": "linked return",
            },
            actor="A",
        )

        self.assertIsNone(result)
        self.assertEqual("graveyard", card.zone)
        self.assertTrue(
            any(
                event.code == "effect.linked_object_missing"
                and event.details.get("object") == card.ref
                for event in engine.state.events
            )
        )

    def test_linked_move_accepts_the_current_incarnation(self):
        engine = self.make_engine(4010)
        card = self.card(engine, "A", "Sol Ring")
        engine.move_card(card.object_id, "graveyard", log=False)

        result = engine.apply_effect(
            {
                "op": "move_if_in_zone",
                "card": card.ref,
                "from": "graveyard",
                "destination": "battlefield",
                "controller": "A",
                "expected_zone_change_counter": (
                    card.zone_change_counter
                ),
                "reason": "linked return",
            },
            actor="A",
        )

        self.assertIs(result, card)
        self.assertEqual("battlefield", card.zone)

    def test_daretti_ruling_does_not_follow_a_reentered_card(self):
        engine = self.make_engine(4013)
        card = self.card(engine, "A", "Sol Ring")
        engine.state.players["A"].stats["daretti_emblems"] = 1
        engine.move_card(
            card.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        engine.move_card(
            card.object_id,
            "graveyard",
            reason="Daretti ruling witness",
            log=False,
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        emblem_trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == "builtin:daretti-emblem"
        )
        self.assertEqual(
            card.zone_change_counter,
            emblem_trigger.context["card_zone_change_counter"],
        )
        engine._prepare_stack_resolution()
        self.assertTrue(engine.state.delayed_triggers)

        engine.move_card(card.object_id, "exile", log=False)
        engine.move_card(card.object_id, "graveyard", log=False)
        delayed = engine._matching_delayed_triggers(
            "step.begin",
            {
                "phase": "ending",
                "step": "end_step",
                "player": "A",
            },
        )
        self.assertTrue(delayed)
        engine._start_trigger_batch(
            delayed,
            after="grant_priority",
        )
        engine._prepare_stack_resolution()

        self.assertEqual("graveyard", card.zone)
        self.assertTrue(
            any(
                event.code == "effect.linked_object_missing"
                and event.details.get("object") == card.ref
                and event.details.get("reason")
                == "object_identity_changed"
                for event in engine.state.events
            )
        )


if __name__ == "__main__":
    unittest.main()
