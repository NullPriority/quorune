from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
import tempfile
import unittest

from common import ROOT, load_assets
from quorune import (
    CommanderSession,
    GameConfig,
    ScriptedPilot,
    SequentialPilotRunner,
)
from quorune.cli import _scripted_choice
from quorune.deck import DeckDefinition, DeckLoader
from quorune.preflight import semantic_preflight
from quorune.record import (
    authoritative_state_hash,
    pause_reason_for_state,
    replay_record,
)


@dataclass(frozen=True, slots=True)
class ReferenceDeck:
    deck_id: str
    path: str
    commander: str
    claim: str


@dataclass(frozen=True, slots=True)
class ReferenceScenario:
    scenario_id: str
    claim_scope: str
    deck_ids: tuple[str, ...]
    evidence_kind: str
    evidence_ref: str
    requirements: frozenset[str]
    expected_unsupported_stops: tuple[str, ...]
    limitations: tuple[str, ...]


REFERENCE_DECKS = (
    ReferenceDeck(
        "mishra",
        "examples/mishra-eminent-one.txt",
        "Mishra, Eminent One",
        "reviewed_compatible",
    ),
    ReferenceDeck(
        "zimone",
        "examples/zimone-and-dina.txt",
        "Zimone and Dina",
        "reviewed_compatible",
    ),
)

MATCH_READINESS_REQUIREMENTS = frozenset(
    {
        "exact_replay",
        "meaningful_composition",
        "natural_completion",
        "pending_decision_reconnect_or_load",
        "private_choice_projection",
        "supported_legal_action_availability",
        "unsupported_stop_classification",
    }
)

# This table routes bounded claims to behavioral evidence. Its existence is not
# itself evidence that a scenario passed.
REFERENCE_SCENARIOS = (
    ReferenceScenario(
        "reviewed-full-deck-pact-lifecycle",
        "reviewed_compatible",
        ("zimone", "mishra", "zimone", "mishra"),
        "python",
        (
            "test_commander_match_readiness."
            "CommanderMatchReadinessTests."
            "test_reviewed_full_decks_execute_pact_lifecycle_and_replay"
        ),
        frozenset(
            {
                "exact_replay",
                "meaningful_composition",
                "supported_legal_action_availability",
                "unsupported_stop_classification",
            }
        ),
        (),
        (
            "The bounded prefix is not a natural-completion claim.",
            "Strict capability-only match readiness remains blocked.",
        ),
    ),
    ReferenceScenario(
        "four-player-natural-runtime-completion",
        "rules_runtime_fixture",
        ("deterministic_micro_pool",) * 4,
        "python",
        (
            "test_deterministic_full_game."
            "DeterministicFourPlayerSoakTests."
            "test_trusted_only_four_player_game_reaches_natural_winner_and_replays"
        ),
        frozenset(
            {
                "exact_replay",
                "natural_completion",
                "private_choice_projection",
                "unsupported_stop_classification",
            }
        ),
        (),
        (
            "The micro-pool is rules-runtime evidence, not format legality.",
            "Duplicated decks are not matchup-strength evidence.",
        ),
    ),
    ReferenceScenario(
        "four-player-full-deck-browser-lifecycle",
        "reviewed_compatible",
        ("zimone", "mishra", "zimone", "mishra"),
        "browser",
        (
            "web/tests/four-player.spec.ts::"
            "@smoke @browser-lifecycle @reconnect @lifecycle four "
            "shared-cookie browser tabs retain isolated seats through "
            "mulligans and reconnect"
        ),
        frozenset(
            {
                "pending_decision_reconnect_or_load",
                "private_choice_projection",
                "supported_legal_action_availability",
            }
        ),
        (),
        (
            "The lifecycle journey does not play the decks to a winner.",
        ),
    ),
    ReferenceScenario(
        "private-ordered-choice-persistence",
        "rules_runtime_fixture",
        ("ordered_scry_fixture",),
        "browser",
        (
            "web/tests/ordered-scry.spec.ts::"
            "@browser-rules @scry @privacy @persistence ordered Scry is "
            "accessible, private, durable, and exactly replayable"
        ),
        frozenset(
            {
                "exact_replay",
                "pending_decision_reconnect_or_load",
                "private_choice_projection",
            }
        ),
        (),
        (
            "The choice fixture is not a full-deck match.",
        ),
    ),
)


def _strict_blocker_kind(blocker: str) -> str:
    if blocker.startswith("format_profile:"):
        return "format_capability_inventory"
    if blocker.startswith("program:"):
        return "legacy_or_mixed_program"
    if blocker.startswith("card_program:missing:"):
        return "missing_card_program"
    if blocker.startswith("card:"):
        return "unbound_card_runtime_dependency"
    return "unknown"


def _resolve_python_test(reference: str) -> object:
    module_name, class_name, method_name = reference.split(".", 2)
    module = importlib.import_module(module_name)
    case = getattr(module, class_name)
    return getattr(case, method_name)


class CommanderMatchReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.db, cls.mishra, cls.zimone = load_assets()
        cls.decks = {"mishra": cls.mishra, "zimone": cls.zimone}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()

    def test_reference_decks_are_legal_and_reviewed_compatible_only(self):
        loader = DeckLoader(self.db)
        allowed_strict_blockers = {
            "format_capability_inventory",
            "legacy_or_mixed_program",
            "missing_card_program",
            "unbound_card_runtime_dependency",
        }
        for reference in REFERENCE_DECKS:
            with self.subTest(deck=reference.deck_id):
                self.assertTrue((ROOT / reference.path).is_file())
                deck = self.decks[reference.deck_id]
                self.assertEqual("reviewed_compatible", reference.claim)
                self.assertEqual([], loader.validate_commander_deck(deck))
                report = semantic_preflight(self.db, deck)
                self.assertEqual(100, report["total_cards"])
                self.assertEqual(0, report["partial_cards"])
                self.assertEqual(0, report["unresolved_cards"])
                self.assertTrue(report["compatibility_ready"])
                self.assertFalse(report["trusted_only_ready"])
                blocker_kinds = {
                    _strict_blocker_kind(blocker)
                    for blocker in report["strict_binding_blockers"]
                }
                self.assertNotIn("unknown", blocker_kinds)
                self.assertLessEqual(blocker_kinds, allowed_strict_blockers)
                self.assertIn("format_capability_inventory", blocker_kinds)

    def test_invalid_reference_deck_is_not_admitted_by_compatibility_claim(self):
        invalid = DeckDefinition.from_dict(self.zimone.to_dict())
        removed = next(
            entry
            for entry in invalid.entries
            if entry.board == "mainboard"
        )
        invalid.entries.remove(removed)

        issues = DeckLoader(self.db).validate_commander_deck(invalid)

        self.assertTrue(
            any("expected 100" in issue for issue in issues),
            issues,
        )

    def test_reference_table_routes_to_owned_behavioral_evidence(self):
        scenario_ids = [scenario.scenario_id for scenario in REFERENCE_SCENARIOS]
        self.assertEqual(len(scenario_ids), len(set(scenario_ids)))
        self.assertEqual(
            MATCH_READINESS_REQUIREMENTS,
            frozenset(
                requirement
                for scenario in REFERENCE_SCENARIOS
                for requirement in scenario.requirements
            ),
        )
        known_decks = {reference.deck_id for reference in REFERENCE_DECKS} | {
            "deterministic_micro_pool",
            "ordered_scry_fixture",
        }
        for scenario in REFERENCE_SCENARIOS:
            with self.subTest(scenario=scenario.scenario_id):
                self.assertIn(
                    scenario.claim_scope,
                    {"reviewed_compatible", "rules_runtime_fixture"},
                )
                self.assertTrue(set(scenario.deck_ids).issubset(known_decks))
                self.assertEqual((), scenario.expected_unsupported_stops)
                self.assertTrue(scenario.limitations)
                if scenario.evidence_kind == "python":
                    self.assertTrue(
                        callable(_resolve_python_test(scenario.evidence_ref))
                    )
                    continue
                self.assertEqual("browser", scenario.evidence_kind)
                path_value, title = scenario.evidence_ref.split("::", 1)
                source = (ROOT / path_value).read_text(encoding="utf-8")
                self.assertIn(f'test("{title}"', source)

    def test_reviewed_full_decks_execute_pact_lifecycle_and_replay(self):
        session = CommanderSession.create(
            self.db,
            {
                "A": self.zimone,
                "B": self.mishra,
                "C": self.zimone,
                "D": self.mishra,
            },
            first_player="A",
            seed=20260908,
            config=GameConfig(
                seed=20260908,
                profile="commander_multiplayer",
                review_profile="commander_review",
                semantic_policy="trusted_only",
                auto_pass_empty_priority=True,
                realistic_mulligan_guard=False,
            ),
        )
        runner = SequentialPilotRunner(
            session,
            {
                f"pilot:{seat}": ScriptedPilot(chooser=_scripted_choice)
                for seat in "ABCD"
            },
            max_retries_per_decision=0,
        )

        for _ in range(9):
            self.assertTrue(runner.step())

        self.assertEqual(0, runner.metrics.failed_actions)
        self.assertEqual(0, runner.metrics.arbiter_invocations)
        self.assertFalse(session.state.players["C"].in_game)
        self.assertTrue(
            any(
                event.code == "draw.skip"
                and event.actor == "C"
                and event.details.get("reason") == "active_player_left_game"
                for event in session.state.events
            )
        )
        pause = pause_reason_for_state(session.state)
        self.assertNotEqual(
            "semantic_unsupported",
            None if pause is None else pause.get("kind"),
        )
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "reviewed-full-deck-pact-prefix"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
