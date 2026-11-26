from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from quorune.opponent_library_exile_model import (
    OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY,
    each_opponent_top_exile_effect,
    opponent_library_exile_play_effect,
)
from quorune.resolving_library_exile import (
    OPPONENT_LIBRARY_EXILE_EVENT,
    OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY,
    observe_resolved_library_exiles,
    resolving_library_exile_source,
)
from quorune.rules.opponent_library_exile_triggers import library_exile_trigger_runtime_dependencies


class ResolvingLibraryExileObservationTests(unittest.TestCase):
    def setUp(self):
        self.item = SimpleNamespace(kind="triggered_ability", controller="A", context={"currently_resolving": True})
        subscriber = SimpleNamespace(capability_dependencies=(OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY,))
        self.host = SimpleNamespace(
            state=SimpleNamespace(stack=[self.item]),
            active_seats=("A", "B", "C"),
            semantics=SimpleNamespace(programs_for_event=lambda event: [subscriber] if event == OPPONENT_LIBRARY_EXILE_EVENT else []),
            _dispatch_semantic_event=Mock(),
        )

    @staticmethod
    def move(identifier, owner, *, origin="library", destination="exile"):
        card = SimpleNamespace(
            object_id=identifier, logical_object_id=f"{identifier}@2", zone=destination,
            owner=owner, object_kind="card", is_token=False,
        )
        return card, SimpleNamespace(origin=origin)

    def test_committed_moves_group_by_opponent_library_under_resolving_controller(self):
        batch = []
        moves = (
            self.move("first", "B"),
            self.move("second", "B"),
            self.move("other", "C"),
            self.move("own", "A"),
            self.move("opponent-left", "D"),
            self.move("replacement", "B", destination="graveyard"),
            self.move("nonlibrary", "B", origin="hand"),
        )
        with resolving_library_exile_source(self.host, self.item):
            observe_resolved_library_exiles(self.host, moves, trigger_batch=batch)
        events = self.host._dispatch_semantic_event.call_args_list
        self.assertEqual([OPPONENT_LIBRARY_EXILE_EVENT] * 2, [result.args[0] for result in events])
        self.assertEqual([
            {"schema_version": 1, "effect_controller": "A", "library_owner": "B",
             "objects": [{"object_id": "first", "logical_object_id": "first@2"},
                         {"object_id": "second", "logical_object_id": "second@2"}]},
            {"schema_version": 1, "effect_controller": "A", "library_owner": "C",
             "objects": [{"object_id": "other", "logical_object_id": "other@2"}]},
        ], [event.args[1]["opponent_library_exile"] for event in events])
        self.assertTrue(all(event.kwargs["trigger_batch"] is batch for event in events))

    def test_no_scope_wrong_source_and_independent_host_do_not_claim_a_move(self):
        move = (self.move("exiled", "B"),)
        observe_resolved_library_exiles(self.host, move)
        with resolving_library_exile_source(self.host, self.item):
            unrelated = SimpleNamespace()
            observe_resolved_library_exiles(unrelated, move)
        fabricated = SimpleNamespace(**vars(self.item))
        with resolving_library_exile_source(self.host, fabricated):
            observe_resolved_library_exiles(self.host, move)
        self.item.context["currently_resolving"] = False
        with resolving_library_exile_source(self.host, self.item):
            observe_resolved_library_exiles(self.host, move)
        self.host._dispatch_semantic_event.assert_not_called()

    def test_scope_is_closed_after_resolution_exception(self):
        with self.assertRaises(RuntimeError), resolving_library_exile_source(self.host, self.item):
            raise RuntimeError("Resolution rollback")
        observe_resolved_library_exiles(self.host, (self.move("exiled", "B"),))
        self.host._dispatch_semantic_event.assert_not_called()

    def test_search_instruction_groups_multiple_selected_cards_from_one_library(self):
        with resolving_library_exile_source(self.host, self.item, group_movements=True):
            observe_resolved_library_exiles(self.host, (self.move("first", "B"),))
            observe_resolved_library_exiles(self.host, (self.move("second", "B"),))
            self.host._dispatch_semantic_event.assert_not_called()
        event = self.host._dispatch_semantic_event.call_args
        self.assertEqual([{"object_id": "first", "logical_object_id": "first@2"},
                          {"object_id": "second", "logical_object_id": "second@2"}],
                         event.args[1]["opponent_library_exile"]["objects"])
        self.host._dispatch_semantic_event.assert_called_once()

    def test_runtime_permission_requires_its_causal_controller_and_event(self):
        fields = {
            "event": OPPONENT_LIBRARY_EXILE_EVENT, "active_zone": "battlefield",
            "event_condition": {"field": "effect_controller", "op": "eq", "value": "$source.controller"},
            "target_schema": None, "cost_schema": None, "handlers": (), "trigger_limit": None,
            "effects": (opponent_library_exile_play_effect(),),
        }
        required = library_exile_trigger_runtime_dependencies(SimpleNamespace(**fields))
        self.assertIn(OPPONENT_LIBRARY_EXILE_EVENT_CAPABILITY, required)
        self.assertIn(OPPONENT_LIBRARY_EXILE_PLAY_CAPABILITY, required)
        for change in (
            {"event": "zone.change"},
            {"event_condition": None},
            {"effects": (each_opponent_top_exile_effect(),)},
            {"effects": ({"op": "sequence", "effects": [opponent_library_exile_play_effect()]},)},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                library_exile_trigger_runtime_dependencies(SimpleNamespace(**{**fields, **change}))
