from __future__ import annotations

from types import SimpleNamespace
import unittest

from quorune.trigger_targeting import (
    begin_pending_trigger_target_selection,
)
from quorune.selection import SelectionContinuation, SelectionContract


class _Semantics:
    @staticmethod
    def get(_key):
        return None


class _Permissions:
    def __init__(self):
        self.issued = []

    def issue(self, **kwargs):
        self.issued.append(kwargs)


class _Host:
    def __init__(self, items, schemas):
        self.state = SimpleNamespace(stack=list(items), revision=12, cards={})
        self.semantics = _Semantics()
        self.permissions = _Permissions()
        self.schemas = dict(schemas)
        self.logs = []

    @staticmethod
    def _stack_target_schema(item, _program):
        return item.context.get("target_schema_override")

    def _public_target_schema(self, _controller, schema, *, source_ref, acting_source=None):
        del source_ref,acting_source
        return self.schemas.get(schema["fixture"])

    @staticmethod
    def _stack_source_ref(item):
        return item.ref

    def _target_selection_continuation(
        self,
        *,
        actor,
        item,
        public_schema,
        trigger_creation=False,
    ):
        return SelectionContinuation(
            contract=SelectionContract.TARGETING,
            operation_id="selection.target.semantic.v1",
            actor=actor,
            state_revision=self.state.revision,
            stack_ref=item.ref,
            source_ref=self._stack_source_ref(item),
            payload={
                "public_schema": dict(public_schema),
                "trigger_creation": trigger_creation,
            },
        )

    def _log(self, *args, **kwargs):
        self.logs.append((args, kwargs))


def _item(ref: str, fixture: str):
    return SimpleNamespace(
        ref=ref,
        kind="triggered_ability",
        source_object_id=None,
        controller="B",
        label=f"Trigger {ref}",
        semantic_key=None,
        context={
            "trigger_target_selection_pending": True,
            "target_schema_override": {"fixture": fixture},
        },
    )


class TriggerTargetingTests(unittest.TestCase):
    def test_dynamic_trigger_without_registry_program_issues_seat_choice(self):
        host = _Host(
            [_item("S1", "legal")],
            {"legal": {"legal_refs": ["A"], "count": 1}},
        )

        self.assertTrue(
            begin_pending_trigger_target_selection(
                host,
                decision_role="pilot",
                log_reason_field="reason",
            )
        )

        self.assertEqual(1, len(host.permissions.issued))
        issued = host.permissions.issued[0]
        self.assertEqual(["B"], issued["actors"])
        selection = issued["continuation"]["selection"]
        self.assertEqual("targeting", selection["contract"])
        self.assertEqual("B", selection["actor"])
        self.assertEqual("S1", selection["stack_ref"])
        self.assertTrue(selection["payload"]["trigger_creation"])
        self.assertEqual(
            "Choose legal targets for Trigger S1.",
            issued["payload_by_actor"]["B"]["prompt"],
        )

    def test_invalid_mandatory_trigger_is_removed_before_next_choice(self):
        invalid = _item("S1", "missing")
        legal = _item("S2", "legal")
        host = _Host(
            [invalid, legal],
            {
                "missing": None,
                "legal": {"legal_refs": ["A"], "count": 1},
            },
        )

        self.assertTrue(
            begin_pending_trigger_target_selection(
                host,
                decision_role="pilot",
                log_reason_field="reason",
            )
        )

        self.assertEqual([legal], host.state.stack)
        self.assertEqual(1, len(host.logs))
        self.assertEqual(
            "S2",
            host.permissions.issued[0]["continuation"]["selection"][
                "stack_ref"
            ],
        )


if __name__ == "__main__":
    unittest.main()
