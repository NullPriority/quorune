from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .card_programs import CARD_PROGRAM_OPERATIONS, execute_card_operation
from .architecture_cli import (
    configure_architecture_commands,
    run_architecture_command,
)
from .carddb import CardDatabase
from .codex_cli import CodexCliArenaRunner, CodexExecClient
from .arena import (
    CodexThreadRegistry,
    CoordinatorTools,
    PilotInvocationIdentity,
    SeatScopedPilotTools,
    primary_session_prompt,
    run_pilot_mcp_stdio,
)
from .model import GameConfig
from .oracle_ir import ORACLE_OPERATIONS, execute_oracle_operation
from .pilot import (
    ManualJsonPilot,
    PilotMemory,
    ScriptedPilot,
    SequentialPilotRunner,
    SubprocessJsonPilot,
)
from .preflight import semantic_preflight
from .record import (
    finalize_record,
    inspect_game,
    migrate_v2_game,
    refresh_record,
    replay_record,
    verify_record_integrity,
)
from .reusable_pieces import execute_reusable_piece_operation
from .report import review_markdown
from .review_artifacts import write_review_artifacts
from .rules_corpus import (
    CORPUS_OPERATIONS,
    RulesCorpusError,
    execute_rules_corpus_operation,
)
from .session import CommanderSession
from .util import stable_json


_ORACLE_COMMAND = "oracle"
_CLI_DESCRIPTION = "Operate the Quorune rules, replay, and local-match toolchain."


def _seat_values(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError("Seat values use SEAT=PATH_OR_MOXFIELD_URL")
        seat, source = value.split("=", 1)
        result[seat.strip()] = source.strip()
    return result


def _load(db_path: str, game_dir: str) -> tuple[CardDatabase, CommanderSession]:
    db = CardDatabase(db_path)
    session = CommanderSession.load(
        db,
        game_dir,
        semantics_path=Path(game_dir) / "semantics.json",
    )
    return db, session


def _scripted_choice(
    observation: dict[str, Any],
    decision: dict[str, Any],
    memory: PilotMemory,
) -> dict[str, Any]:
    """Conservative deterministic pilot for local characterization fixtures."""

    kind = str(decision.get("kind") or "")
    context = dict(decision.get("ctx") or {})
    actions = list(decision.get("legal_actions") or [])
    if kind == "mulligan.declare":
        action_id = "keep"
        plan = "MULLIGAN"
        reason = "Keep a functional hand without chasing an ideal synergy hand."
        return {"action_id": action_id, "plan": plan, "reason": reason}
    if kind == "mulligan.bottom":
        hand = list(context.get("hand") or [])
        count = int(context.get("count", 0))
        return {
            "action_id": "bottom",
            "cards": [item["id"] for item in hand[:count]],
            "plan": "MULLIGAN",
            "reason": "Bottom the least immediately useful cards after the counted redraw.",
        }
    if kind in {"search.fetch", "semantic.search"}:
        choices = list(context.get("search_cards") or [])
        selected = choices[0]["id"] if choices else None
        maximum = int(
            context.get("search_spec", {})
            .get("count", {})
            .get("maximum", 1)
        )
        return {
            "action_id": "choose",
            **(
                {"search_card": selected}
                if maximum == 1
                else {
                    "search_cards": [
                        item["id"] for item in choices[:maximum]
                    ]
                }
            ),
            "entry_pay_life": False,
            "plan": "FIX_COLORS",
            "reason": "Choose a legal typed source and preserve life unless untapped mana is required.",
        }
    if kind in {"semantic.choice", "semantic.target"}:
        options = list(
            context.get("options")
            or context.get("target_schema", {}).get("legal_refs")
            or []
        )
        choices: dict[str, Any] = {}
        if kind == "semantic.target":
            schema = dict(context.get("target_schema") or {})
            legal_modes = list(schema.get("legal_modes") or [])
            if legal_modes:
                mode = legal_modes[0]
                choices["modes"] = [mode]
                groups = list(
                    schema.get("mode_schemas", {})
                    .get(mode, {})
                    .get("groups", [])
                )
            else:
                groups = list(schema.get("groups") or [])
            choices["targets"] = [
                ref
                for group in groups
                for ref in list(group.get("legal_refs") or [])[
                    : int(group.get("min", 0))
                ]
            ]
        elif context.get("operation") == "choose_mana":
            choices["choice"] = "G"
        elif context.get("operation") == "choose_card_name":
            choices["card_name"] = "Sensei's Divining Top"
        elif context.get("operation") in {
            "counter_unless_pay", "ward_counter_unless_pay",
            "pay_or_lose",
        }:
            choices["pay"] = False
        elif context.get("operation") == "proliferate":
            choices["objects"] = options
        elif options:
            choices["card"] = options[0]
        return {
            "action_id": "choose",
            **choices,
            "plan": "DEVELOP_ENGINE",
            "reason": "Make the advertised semantic choice that advances the current engine line.",
        }
    if kind == "arbiter.resolve":
        return {
            "action_id": "resolve",
            "effects": [],
            "note": "Explicit one-shot provisional resolution; semantic remains unresolved.",
            "plan": "RECOVER",
            "reason": "Resolve once without registering unsupported text or inventing hidden choices.",
        }
    if kind == "combat.attackers":
        return {
            "action_id": "attack",
            "attackers": {},
            "plan": "DEVELOP_ENGINE",
            "reason": "Avoid unsupported combat risk during deterministic characterization.",
        }
    if kind == "combat.blockers":
        return {
            "action_id": "block",
            "blocks": {},
            "plan": "DEVELOP_ENGINE",
            "reason": "No profitable deterministic block is selected.",
        }
    if kind == "cleanup.discard":
        hand = list(context.get("hand") or [])
        count = int(context.get("count", 0))
        return {
            "action_id": "discard",
            "cards": [item["id"] for item in hand[:count]],
            "plan": "RECOVER",
            "reason": "Discard to the authoritative maximum-hand-size requirement.",
        }
    if kind == "state.commander_zone":
        command = next(
            (
                item
                for item in actions
                if item.get("id") == "command"
            ),
            actions[0] if actions else {"id": "command"},
        )
        return {
            "action_id": command["id"],
            "plan": "DEVELOP_ENGINE",
            "reason": "Return the commander to the command zone.",
        }
    if kind in {"state.legend", "choice.apnap", "trigger.order"}:
        options = (
            context.get("keep_one")
            or context.get("options")
            or [item["id"] for item in context.get("triggers") or []]
        )
        choices = {}
        if kind == "state.legend":
            choices["card"] = options[0]
        elif kind == "trigger.order":
            choices["triggers"] = options
        else:
            choices["cards"] = options[: int(context.get("count", 0))]
        return {
            "action_id": actions[0]["id"] if actions else "choose",
            **choices,
            "plan": "DEVELOP_ENGINE",
            "reason": "Make the deterministic required rules choice.",
        }
    land = next((item for item in actions if item.get("kind") == "play_land"), None)
    if land:
        choices = {}
        if land.get("choice_schema", {}).get("pay_life"):
            choices["pay_life"] = False
        return {
            "action_id": land["id"],
            **choices,
            "plan": "DEVELOP_MANA",
            "reason": "Make the available land drop and preserve life unless tempo requires otherwise.",
        }
    def useful_cast(item: dict[str, Any]) -> bool:
        options = list(item.get("cost_options") or [])
        candidate_options = (
            [
                option
                for option in options
                if option.get("id") == "normal"
            ]
            or options
        )
        if not candidate_options:
            return True
        option = candidate_options[0]
        schema = dict(
            option.get("target_schema")
            or item.get("target_schema")
            or {}
        )
        groups = list(schema.get("groups") or [])
        if (
            option.get("kind") == "alternate_exile"
            and groups
            and all(int(group.get("min", 0)) == 0 for group in groups)
            and not any(group.get("legal_refs") for group in groups)
        ):
            return False
        return True

    cast = next(
        (
            item
            for item in actions
            if item.get("kind") == "cast" and useful_cast(item)
        ),
        None,
    )
    if cast:
        choices = {}
        cost_options = list(cast.get("cost_options") or [])
        selected_cost = (
            next(
                (
                    option
                    for option in cost_options
                    if option.get("id") == "normal"
                ),
                None,
            )
            or (cost_options[0] if cost_options else None)
        )
        if selected_cost:
            choices["cost_option"] = selected_cost["id"]
            choice_schema = dict(
                selected_cost.get("choice_schema") or {}
            )
            if choice_schema.get("exile_card", {}).get("legal_refs"):
                choices["exile_card"] = choice_schema["exile_card"][
                    "legal_refs"
                ][0]
            if choice_schema.get("x"):
                choices["x"] = int(
                    choice_schema["x"].get("minimum", 0)
                )
        target_schema = dict(
            (selected_cost or {}).get("target_schema")
            or cast.get("target_schema")
            or {}
        )
        if target_schema:
            legal_modes = list(target_schema.get("legal_modes") or [])
            if legal_modes:
                mode = legal_modes[0]
                choices["modes"] = [mode]
                groups = list(
                    target_schema.get("mode_schemas", {})
                    .get(mode, {})
                    .get("groups", [])
                )
            else:
                groups = list(target_schema.get("groups") or [])
            choices["targets"] = [
                ref
                for group in groups
                for ref in list(group.get("legal_refs") or [])[
                    : int(group.get("min", 0))
                ]
            ]
        return {
            "action_id": cast["id"],
            **choices,
            "plan": "DEVELOP_ENGINE",
            "reason": "Deploy an affordable engine piece from the complete legal-action catalog.",
        }
    fetch = next(
        (
            item
            for item in actions
            if item.get("kind") == "activate"
            and item.get("choice_schema", {}).get("resolution_time")
        ),
        None,
    )
    if fetch:
        return {
            "action_id": fetch["id"],
            "plan": "FIX_COLORS",
            "reason": "Use the fetchland while its resolution-time typed-land search is available.",
        }
    return {
        "action_id": "pass",
        "yield": "until_public_change",
        "plan": "PASS_WITH_YIELD",
        "reason": "No meaningful development or interaction is currently advertised.",
    }


def _provider_from_spec(spec: str, output: Path, seat: str):
    if spec == "scripted":
        return ScriptedPilot(chooser=_scripted_choice)
    if spec == "manual":
        return ManualJsonPilot(
            task_path=output / "manual" / f"{seat}-task.json",
        )
    if spec.startswith("subprocess:"):
        return SubprocessJsonPilot(spec.split(":", 1)[1])
    raise ValueError(f"Unknown pilot provider {spec!r}")


def _configure_oracle_subcommands(
    oracle_sub: Any,
) -> None:
    for operation in sorted(ORACLE_OPERATIONS):
        child = oracle_sub.add_parser(operation)
        child.add_argument(
            "card",
            nargs=("?" if operation in {"parse", "explain"} else "*"),
        )
        child.add_argument(
            "--db",
            default="data/scryfall-20260728-compact.sqlite3",
        )
        child.add_argument("--commander-legal-only", action="store_true")
        child.add_argument(
            "--profile",
            choices=("traditional", "commander_duel", "commander_review"),
            default="traditional",
            help="Bind generic Oracle IR to the trusted rules capability profile",
        )
        child.add_argument("--limit", type=int)
        child.add_argument("--output")


def _run_oracle_command(args: argparse.Namespace) -> int:
    card = args.card
    if isinstance(card, list):
        if args.oracle_cmd not in {"parse", "explain"} and card:
            raise SystemExit(
                f"{_ORACLE_COMMAND} {args.oracle_cmd} does not accept a card name"
            )
        if len(card) > 1:
            raise SystemExit(
                f"{_ORACLE_COMMAND} parse/explain accept exactly one card name"
            )
        card = card[0] if card else None
    try:
        value = execute_oracle_operation(
            args.oracle_cmd,
            db_path=args.db,
            card=card,
            commander_legal_only=args.commander_legal_only,
            limit=args.limit,
            output=args.output,
            capability_profile=args.profile,
        )
    except (KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(stable_json(value))
    return 0


def _configure_contributor_tooling_parsers(sub: Any) -> None:
    card_program = sub.add_parser(
        "card",
        help="Compile and audit canonical CardProgram V2 artifacts",
    )
    card_program_sub = card_program.add_subparsers(
        dest="card_cmd",
        required=True,
    )
    for operation in sorted({*CARD_PROGRAM_OPERATIONS, "pieces"}):
        child = card_program_sub.add_parser(operation)
        if operation == "pieces":
            child.add_argument("card")
            child.add_argument("--root", default=".")
            continue
        if operation in {
            "compile",
            "explain",
            "audit",
            "diff",
            "trust-closure",
        }:
            child.add_argument("card")
        child.add_argument(
            "--db",
            default="data/scryfall-20260728-compact.sqlite3",
        )
        child.add_argument(
            "--profile",
            choices=("traditional", "commander_duel", "commander_review"),
            default="traditional",
        )
        if operation == "diff":
            child.add_argument("--against", required=True)
        if operation == "coverage":
            child.add_argument("--commander-legal-only", action="store_true")
            child.add_argument("--limit", type=int)
        child.add_argument("--output")

    pieces = sub.add_parser(
        "pieces",
        help="Inspect the pinned reusable rules-piece inventory",
    )
    pieces_sub = pieces.add_subparsers(
        dest="pieces_cmd",
        required=True,
    )
    for operation in (
        "inventory",
        "coverage",
        "show",
        "cards",
        "blockers",
        "interactions",
        "diff",
        "next",
    ):
        child = pieces_sub.add_parser(operation)
        if operation in {"show", "cards", "blockers", "interactions"}:
            child.add_argument("piece_id")
        if operation == "diff":
            child.add_argument("--against")
        child.add_argument("--root", default=".")
        child.add_argument("--limit", type=int, default=20)
    configure_architecture_commands(sub)


def _run_contributor_tooling_command(args: argparse.Namespace) -> int | None:
    architecture_result = run_architecture_command(args)
    if architecture_result is not None:
        return architecture_result
    if args.cmd not in {"pieces", "card"}:
        return None
    if args.cmd == "card" and args.card_cmd != "pieces":
        return None
    operation = args.pieces_cmd if args.cmd == "pieces" else "card"
    try:
        value = execute_reusable_piece_operation(
            operation,
            root=args.root,
            piece_id=getattr(args, "piece_id", None),
            card=getattr(args, "card", None),
            against=getattr(args, "against", None),
            limit=getattr(args, "limit", 20),
        )
    except (KeyError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(stable_json(value))
    return 0


def _run_semantic_preflight_command(args: argparse.Namespace) -> int | None:
    if args.cmd != "semantics" or args.semantics_cmd != "preflight":
        return None
    db = CardDatabase(args.db)
    try:
        result = semantic_preflight(
            db,
            args.deck,
            cache_dir=args.cache_dir,
            force_refresh=args.refresh_decks,
        )
        if args.output:
            Path(args.output).write_text(stable_json(result), encoding="utf-8")
        print(stable_json(result))
    finally:
        db.close()
    return 0


def build_parser(*, prog: str = "simctl") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description=_CLI_DESCRIPTION)
    sub = parser.add_subparsers(dest="cmd", required=True)

    new = sub.add_parser("new", help="Create a persistent multiplayer game")
    new.add_argument("--db", required=True)
    new.add_argument("--seat", action="append", required=True, help="SEAT=deck.txt or public Moxfield URL")
    new.add_argument("--commander", action="append", default=[], help="SEAT=Commander Name when a text list lacks a section")
    new.add_argument("--first")
    new.add_argument("--seed", type=int)
    new.add_argument("--out", required=True)
    new.add_argument("--cache-dir")
    new.add_argument("--refresh-decks", action="store_true")
    new.add_argument("--profile", choices=("commander_duel", "commander_multiplayer", "auto"), default="auto")
    new.add_argument("--trace-level", choices=("minimal", "standard", "debug"), default="standard")

    duel = sub.add_parser("duel", help="Create a two-player Commander game from two deck sources")
    duel.add_argument("--db", required=True)
    duel.add_argument("--first", choices=("A", "B"), default="A")
    duel.add_argument("--seed", type=int)
    duel.add_argument("--out", required=True)
    duel.add_argument("--cache-dir")
    duel.add_argument("--refresh-decks", action="store_true")
    duel.add_argument("--profile", choices=("commander_duel", "auto"), default="commander_duel")
    duel.add_argument("--trace-level", choices=("minimal", "standard", "debug"), default="standard")
    duel.add_argument("deck_a", help="Seat A deck file, Moxfield URL, or public deck id")
    duel.add_argument("deck_b", help="Seat B deck file, Moxfield URL, or public deck id")

    task = sub.add_parser("task", help="Emit the next compact permission-scoped packet")
    task.add_argument("--db", required=True)
    task.add_argument("--game", required=True)
    task.add_argument("--principal", help="Observe a specific principal instead of the next pending actor")
    task.add_argument("--full", action="store_true")
    task.add_argument("--pretty", action="store_true")

    act = sub.add_parser("act", help="Submit one compact JSON action")
    act.add_argument("--db", required=True)
    act.add_argument("--game", required=True)
    act.add_argument("--principal", required=True)
    action_group = act.add_mutually_exclusive_group(required=True)
    action_group.add_argument("--json")
    action_group.add_argument("--file")

    rules = sub.add_parser(
        "rules",
        help=(
            "Read seat-visible Oracle/rulings or manage the pinned "
            "Comprehensive Rules corpus"
        ),
    )
    rules.add_argument("--db")
    rules.add_argument("--game")
    rules.add_argument(
        "cards",
        nargs="+",
        help=(
            "Card names/refs, or one of: sync, inventory, diff, coverage, "
            "conformance, queue, next, verify, report"
        ),
    )
    rules.add_argument(
        "--root",
        default=".",
        help="Repository/output root containing rules/ and coverage/",
    )
    rules.add_argument("--cache-dir")
    rules.add_argument(
        "--source-file",
        help="Parse a local CR TXT fixture instead of downloading",
    )
    rules.add_argument(
        "--source-url",
        help="Explicit official Wizards HTTPS TXT URL",
    )
    rules.add_argument(
        "--against",
        help="Prior corpus root for rules diff",
    )
    rules.add_argument("--limit", type=int, default=20)
    rules.add_argument("--output")

    report = sub.add_parser("report", help="Produce the derived Game Record review")
    report.add_argument("--db", required=True)
    report.add_argument("--game")
    report.add_argument("record", nargs="?")

    inspect = sub.add_parser("inspect-game", help="Inspect a v2 game.json or v3 record directory")
    inspect.add_argument("path")
    inspect.add_argument("--pretty", action="store_true")

    migrate = sub.add_parser("migrate-record", help="Migrate a legacy game.json to Game Record v3")
    migrate.add_argument("game_json")
    migrate.add_argument("--output", "--out", required=True)
    migrate.add_argument("--db", required=True)
    migrate.add_argument("--trace-level", choices=("minimal", "standard", "debug"), default="standard")

    replay = sub.add_parser("replay", help="Replay and verify a Game Record v3 directory")
    replay.add_argument("record")
    replay.add_argument("--db", required=True)
    replay.add_argument("--verify", action="store_true")

    semantics = sub.add_parser("semantics", help="Inspect semantic coverage")
    semantics_sub = semantics.add_subparsers(dest="semantics_cmd", required=True)
    preflight = semantics_sub.add_parser(
        "preflight", help="Preflight a deck or public Moxfield URL"
    )
    preflight.add_argument("deck")
    preflight.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    preflight.add_argument("--cache-dir")
    preflight.add_argument("--refresh-decks", action="store_true")
    preflight.add_argument(
        "--profile",
        choices=("traditional", "commander_duel", "commander_review"),
        default="commander_review",
    )
    preflight.add_argument("--output")

    oracle = sub.add_parser(
        "oracle",
        help=(
            "Compile pinned Oracle text into typed IR and inspect "
            "fail-closed residual coverage"
        ),
    )
    oracle_sub = oracle.add_subparsers(
        dest="oracle_cmd",
        required=True,
    )
    _configure_oracle_subcommands(oracle_sub)

    _configure_contributor_tooling_parsers(sub)

    pilot_run = sub.add_parser(
        "pilot-run", help="Create or resume a provider-piloted native v3 run"
    )
    pilot_run.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    pilot_run.add_argument("--profile", choices=("commander_duel", "commander_multiplayer", "commander_review", "auto"), default="auto")
    pilot_run.add_argument("--deck", action="append", required=True)
    pilot_run.add_argument("--pilot", action="append", required=True)
    pilot_run.add_argument("--output", required=True)
    pilot_run.add_argument("--cache-dir")
    pilot_run.add_argument("--refresh-decks", action="store_true")
    pilot_run.add_argument("--first")
    pilot_run.add_argument("--seed", type=int)
    pilot_run.add_argument("--through-turn", type=int, default=8)
    pilot_run.add_argument("--max-invocations", type=int, default=200)

    inspect_decisions = sub.add_parser(
        "inspect-decisions", help="Inspect a record's durable decision audit"
    )
    inspect_decisions.add_argument("record")

    inspect_semantics = sub.add_parser(
        "inspect-semantics", help="Inspect semantic programs and review coverage"
    )
    inspect_semantics.add_argument("record")

    pilot_mcp = sub.add_parser(
        "pilot-mcp",
        help="Run a fixed-seat MCP server without exposing authoritative state",
    )
    pilot_mcp.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    pilot_mcp.add_argument(
        "--game-dir", default=os.environ.get("MTG_GAME_DIR")
    )
    pilot_mcp.add_argument("--seat", required=True)
    pilot_mcp.add_argument("--provider", default="codex_subagent")
    pilot_mcp.add_argument("--model")
    pilot_mcp.add_argument("--reasoning-effort")
    pilot_mcp.add_argument("--thread-id")
    pilot_mcp.add_argument("--thread-label")
    pilot_mcp.add_argument("--parent-session-id")
    pilot_mcp.add_argument("--provider-invoked", action="store_true")
    pilot_mcp.add_argument(
        "--provider-identity-verified", action="store_true"
    )
    pilot_mcp.add_argument(
        "--model-identity-verified", action="store_true"
    )
    pilot_mcp.add_argument("--model-configured")
    pilot_mcp.add_argument("--reasoning-effort-configured")

    pilot_tool = sub.add_parser(
        "pilot-tool",
        help="Invoke one fixed-seat pilot tool for local Codex orchestration",
    )
    pilot_tool.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    pilot_tool.add_argument("--game-dir", required=True)
    pilot_tool.add_argument("--seat", required=True)
    pilot_tool.add_argument("--provider", default="codex_subagent")
    pilot_tool.add_argument("--model")
    pilot_tool.add_argument("--reasoning-effort")
    pilot_tool.add_argument("--thread-id")
    pilot_tool.add_argument("--thread-label")
    pilot_tool.add_argument("--parent-session-id")
    pilot_tool.add_argument("--provider-invoked", action="store_true")
    pilot_tool.add_argument(
        "--provider-identity-verified", action="store_true"
    )
    pilot_tool.add_argument(
        "--model-identity-verified", action="store_true"
    )
    pilot_tool.add_argument("--model-configured")
    pilot_tool.add_argument("--reasoning-effort-configured")
    pilot_tool.add_argument(
        "operation",
        choices=(
            "get-task",
            "submit-action",
            "get-rules",
            "get-profile",
            "get-memory",
            "update-memory",
        ),
    )
    pilot_tool.add_argument("--json")
    pilot_tool.add_argument("--file")
    pilot_tool.add_argument("--ref", action="append", default=[])
    pilot_tool.add_argument("--text")

    arena_create = sub.add_parser(
        "arena-create",
        help="Create a four-seat commander_review record and primary prompt",
    )
    arena_create.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    arena_create.add_argument("--deck", action="append", required=True)
    arena_create.add_argument("--output", required=True)
    arena_create.add_argument("--cache-dir")
    arena_create.add_argument("--refresh-decks", action="store_true")
    arena_create.add_argument("--first", default="A")
    arena_create.add_argument("--seed", type=int)
    arena_create.add_argument(
        "--semantic-policy",
        choices=("trusted_only", "arbitrate_or_pause"),
        default="trusted_only",
        help=(
            "Semantic execution policy; Commander review arenas default to "
            "trusted_only so a natural game can qualify as operation evidence"
        ),
    )

    arena_status = sub.add_parser(
        "arena-status",
        help="Inspect public coordinator progress without pilot packets",
    )
    arena_status.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    arena_status.add_argument("--game", required=True)

    arena_codex_run = sub.add_parser(
        "arena-codex-run",
        help=(
            "Drive a fixed-seat arena with four persistent fast Codex CLI "
            "sessions"
        ),
    )
    arena_codex_run.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    arena_codex_run.add_argument("--game", required=True)
    arena_codex_run.add_argument("--codex-executable", default="codex")
    arena_codex_run.add_argument("--project-root", default=os.getcwd())
    arena_codex_run.add_argument("--model", default="gpt-5.6-sol")
    arena_codex_run.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh", "max", "ultra"),
        default="low",
    )
    arena_codex_run.add_argument("--service-tier", default="priority")
    arena_codex_run.add_argument(
        "--parent-session-id",
        default=(
            os.environ.get("CODEX_THREAD_ID")
            or os.environ.get("CODEX_SESSION_ID")
        ),
    )
    arena_codex_run.add_argument("--through-turn", type=int, default=8)
    arena_codex_run.add_argument("--max-invocations", type=int, default=200)
    arena_codex_run.add_argument(
        "--bootstrap-timeout", type=float, default=30
    )
    arena_codex_run.add_argument(
        "--decision-timeout", type=float, default=90
    )
    arena_codex_run.add_argument("--max-retries", type=int, default=2)
    arena_codex_run.add_argument(
        "--no-replay-verify", action="store_true"
    )

    coordinator_tool = sub.add_parser(
        "coordinator-tool",
        help="Invoke the public coordinator/arbiter surface (never a seat action)",
    )
    coordinator_tool.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    coordinator_tool.add_argument("--game", required=True)
    coordinator_tool.add_argument(
        "operation",
        choices=("status", "get-arbiter-task", "submit-arbiter"),
    )
    coordinator_tool.add_argument("--json")

    for command, help_text in (
        (
            "refresh-record",
            "Rebuild manifest/review metadata from durable journals",
        ),
        (
            "finalize-record",
            "Pause or complete, replay-verify, and finalize a record",
        ),
        (
            "verify-record",
            "Check manifest/journal integrity and exact prefix replay",
        ),
    ):
        lifecycle = sub.add_parser(command, help=help_text)
        lifecycle.add_argument("record")
        lifecycle.add_argument(
            "--db", default="data/scryfall-20260728-compact.sqlite3"
        )

    arena = sub.add_parser(
        "arena", help="Inspect or change a persistent arena lifecycle"
    )
    arena_sub = arena.add_subparsers(dest="arena_cmd", required=True)
    for operation in ("status", "resume", "finalize"):
        child = arena_sub.add_parser(operation)
        child.add_argument("record")
        child.add_argument(
            "--db", default="data/scryfall-20260728-compact.sqlite3"
        )
    arena_pause = arena_sub.add_parser("pause")
    arena_pause.add_argument("record")
    arena_pause.add_argument("--reason", required=True)
    arena_pause.add_argument(
        "--kind", default="fidelity_failure"
    )
    arena_pause.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )
    arena_abort = arena_sub.add_parser("abort")
    arena_abort.add_argument("record")
    arena_abort.add_argument("--reason", required=True)
    arena_abort.add_argument(
        "--db", default="data/scryfall-20260728-compact.sqlite3"
    )

    return parser


def _program_name(executable: str) -> str:
    return "quorune" if Path(executable).stem.lower() in {"quorune", "__main__"} else "simctl"


def main(argv: list[str] | None = None) -> int:
    args = build_parser(prog=_program_name(sys.argv[0])).parse_args(argv)
    if args.cmd == "pilot-mcp":
        if not args.game_dir:
            raise SystemExit(
                "pilot-mcp requires --game-dir or MTG_GAME_DIR"
            )
        identity = PilotInvocationIdentity(
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            thread_id=args.thread_id,
            thread_label=args.thread_label,
            parent_session_id=args.parent_session_id,
            provider_invoked=bool(args.provider_invoked),
            provider_identity_verified=bool(
                args.provider_identity_verified
            ),
            model_identity_verified=bool(args.model_identity_verified),
            model_configured=args.model_configured,
            reasoning_effort_configured=(
                args.reasoning_effort_configured
            ),
        )
        tools = SeatScopedPilotTools.open(
            game_dir=args.game_dir,
            db_path=args.db,
            seat=args.seat,
            identity=identity,
        )
        run_pilot_mcp_stdio(tools)
        return 0
    if args.cmd == "pilot-tool":
        identity = PilotInvocationIdentity(
            provider=args.provider,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            thread_id=args.thread_id,
            thread_label=args.thread_label,
            parent_session_id=args.parent_session_id,
            provider_invoked=bool(args.provider_invoked),
            provider_identity_verified=bool(
                args.provider_identity_verified
            ),
            model_identity_verified=bool(args.model_identity_verified),
            model_configured=args.model_configured,
            reasoning_effort_configured=(
                args.reasoning_effort_configured
            ),
        )
        tools = SeatScopedPilotTools.open(
            game_dir=args.game_dir,
            db_path=args.db,
            seat=args.seat,
            identity=identity,
        )
        if args.operation == "get-task":
            value = tools.get_task()
        elif args.operation == "submit-action":
            if bool(args.json) == bool(args.file):
                raise SystemExit(
                    "submit-action requires exactly one of --json or --file"
                )
            response = json.loads(
                args.json
                if args.json
                else Path(args.file).read_text(encoding="utf-8")
            )
            value = tools.submit_action(response)
        elif args.operation == "get-rules":
            value = tools.get_rules(args.ref)
        elif args.operation == "get-profile":
            value = tools.get_profile()
        elif args.operation == "get-memory":
            value = tools.get_memory()
        else:
            if args.text is None:
                raise SystemExit("update-memory requires --text")
            value = tools.update_memory(args.text)
        print(stable_json(value))
        return 0
    if args.cmd in {
        "refresh-record",
        "finalize-record",
        "verify-record",
    }:
        db = CardDatabase(args.db)
        try:
            if args.cmd == "refresh-record":
                value = refresh_record(
                    args.record, db, verify_replay=False
                )
            elif args.cmd == "finalize-record":
                value = finalize_record(args.record, db)
            else:
                value = verify_record_integrity(args.record, db)
            print(stable_json(value))
            return 0 if value.get("ok", True) else 2
        finally:
            db.close()
    if args.cmd == "arena":
        db = CardDatabase(args.db)
        try:
            if args.arena_cmd == "status":
                value = {
                    **inspect_game(args.record),
                    "integrity": verify_record_integrity(
                        args.record, db, replay=False
                    ),
                }
            elif args.arena_cmd == "finalize":
                value = finalize_record(args.record, db)
            else:
                session = CommanderSession.load(
                    db,
                    args.record,
                    semantics_path=Path(args.record) / "semantics.json",
                )
                if args.arena_cmd == "resume":
                    session.resume()
                elif args.arena_cmd == "pause":
                    session.pause(
                        {
                            "kind": str(args.kind)[:100],
                            "label": str(args.reason)[:500],
                            "decision_id": (
                                session.state.pending_decision.decision_id
                                if session.state.pending_decision
                                else None
                            ),
                            "decision_kind": (
                                session.state.pending_decision.kind
                                if session.state.pending_decision
                                else None
                            ),
                        }
                    )
                else:
                    session.abort(args.reason)
                session.save(args.record)
                value = inspect_game(args.record)
            print(stable_json(value))
        finally:
            db.close()
        return 0
    if args.cmd == "arena-create":
        sources = _seat_values(args.deck)
        if set(sources) != {"A", "B", "C", "D"}:
            raise SystemExit(
                "arena-create requires exactly A, B, C, and D deck sources"
            )
        output = Path(args.output)
        db = CardDatabase(args.db)
        try:
            session = CommanderSession.from_sources(
                db,
                sources,
                first_player=args.first,
                seed=args.seed,
                cache_dir=args.cache_dir,
                force_refresh=args.refresh_decks,
                semantics_path=output / "semantics.json",
                config=GameConfig(
                    seed=args.seed,
                    profile="commander_multiplayer",
                    auto_pass_empty_priority=True,
                    semantic_policy=args.semantic_policy,
                ),
            )
            registry = CodexThreadRegistry()
            for seat in "ABCD":
                registry.register(
                    seat=seat,
                    thread_label=f"quorune-pilot-{seat.lower()}",
                    provider="unavailable",
                    model=None,
                    reasoning_effort=None,
                    thread_id=None,
                )
            session.arena_metadata = registry.metadata()
            session.save(output)
            prompt = primary_session_prompt(output)
            (output / "PRIMARY_CODEX_PROMPT.md").write_text(
                prompt + "\n", encoding="utf-8"
            )
            print(
                stable_json(
                    {
                        "game_id": session.state.game_id,
                        "record": str(output.resolve()),
                        "profile": "commander_review",
                        "pilot_thread_count": 4,
                        "codex_subagent_run": False,
                        "primary_prompt": prompt,
                    }
                )
            )
        finally:
            db.close()
        return 0
    if args.cmd == "arena-status":
        db, session = _load(args.db, args.game)
        try:
            print(stable_json(CoordinatorTools(session).status()))
        finally:
            db.close()
        return 0
    if args.cmd == "arena-codex-run":
        client = CodexExecClient(
            project_root=args.project_root,
            executable=args.codex_executable,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            service_tier=args.service_tier,
        )
        runner = CodexCliArenaRunner(
            game_dir=args.game,
            db_path=args.db,
            client=client,
            model=args.model,
            reasoning_effort=args.reasoning_effort,
            service_tier=args.service_tier,
            parent_session_id=args.parent_session_id,
            bootstrap_timeout=args.bootstrap_timeout,
            decision_timeout=args.decision_timeout,
            max_retries=args.max_retries,
        )
        result = runner.run(
            through_turn=args.through_turn,
            max_invocations=args.max_invocations,
            verify_replay=not args.no_replay_verify,
        )
        print(stable_json(result))
        return 0
    if args.cmd == "coordinator-tool":
        db, session = _load(args.db, args.game)
        try:
            coordinator = CoordinatorTools(session)
            if args.operation == "status":
                if args.json:
                    raise SystemExit("status does not accept --json")
                value = coordinator.status()
            elif args.operation == "get-arbiter-task":
                if args.json:
                    raise SystemExit("get-arbiter-task does not accept --json")
                value = coordinator.get_arbiter_task()
            else:
                if not args.json:
                    raise SystemExit("submit-arbiter requires --json")
                value = coordinator.submit_arbiter(json.loads(args.json))
                if value.get("accepted"):
                    session.save(args.game)
            print(stable_json(value))
        finally:
            db.close()
        return 0
    semantic_preflight_result = _run_semantic_preflight_command(args)
    if semantic_preflight_result is not None:
        return semantic_preflight_result
    if args.cmd == "oracle":
        return _run_oracle_command(args)
    reusable_piece_result = _run_contributor_tooling_command(args)
    if reusable_piece_result is not None:
        return reusable_piece_result
    if args.cmd == "card":
        try:
            value = execute_card_operation(
                args.card_cmd,
                db_path=args.db,
                card=getattr(args, "card", None),
                profile=args.profile,
                against=getattr(args, "against", None),
                commander_legal_only=getattr(
                    args, "commander_legal_only", False
                ),
                limit=getattr(args, "limit", None),
                output=args.output,
            )
        except (KeyError, ValueError) as exc:
            raise SystemExit(str(exc)) from exc
        print(stable_json(value))
        return 0

    if args.cmd == "inspect-decisions":
        path = Path(args.record) / "decisions.jsonl"
        rows = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        print(stable_json({"record": args.record, "decisions": rows}))
        return 0

    if args.cmd == "inspect-semantics":
        record = Path(args.record)
        registry_path = record / "semantics.json"
        from .semantics import SemanticRegistry

        registry = SemanticRegistry(registry_path)
        review_path = record / "review.json"
        review = (
            json.loads(review_path.read_text(encoding="utf-8"))
            if review_path.exists()
            else {}
        )
        print(
            stable_json(
                {
                    "record": args.record,
                    "programs": [
                        program.to_dict() for program in registry.programs()
                    ],
                    "coverage": review.get("semantic_coverage"),
                }
            )
        )
        return 0

    if (
        args.cmd == "rules"
        and len(args.cards) == 1
        and args.cards[0] in CORPUS_OPERATIONS
        and not args.game
    ):
        try:
            value = execute_rules_corpus_operation(
                args.cards[0],
                root=args.root,
                cache_dir=args.cache_dir,
                source_file=args.source_file,
                source_url=args.source_url,
                against=args.against,
                limit=args.limit,
                output=args.output,
                card_db_path=args.db,
            )
        except RulesCorpusError as exc:
            raise SystemExit(str(exc)) from exc
        print(value if isinstance(value, str) else stable_json(value))
        return 0 if not isinstance(value, dict) or value.get("ok", True) else 2

    if args.cmd == "pilot-run":
        output = Path(args.output)
        output.mkdir(parents=True, exist_ok=True)
        db = CardDatabase(args.db)
        try:
            if (output / "manifest.json").exists():
                session = CommanderSession.load(
                    db, output, semantics_path=output / "semantics.json"
                )
            else:
                sources = _seat_values(args.deck)
                effective_profile = (
                    "commander_multiplayer"
                    if args.profile == "commander_review"
                    else args.profile
                )
                config = GameConfig(
                    seed=args.seed,
                    profile=effective_profile,
                    trace_level="standard",
                )
                session = CommanderSession.from_sources(
                    db,
                    sources,
                    first_player=args.first or next(iter(sources)),
                    seed=args.seed,
                    cache_dir=args.cache_dir,
                    force_refresh=args.refresh_decks,
                    semantics_path=output / "semantics.json",
                    config=config,
                )
            specs = _seat_values(args.pilot)
            providers = {
                f"pilot:{seat}": _provider_from_spec(spec, output, seat)
                for seat, spec in specs.items()
            }
            arbiter = ScriptedPilot(chooser=_scripted_choice, implementation_id="provisional-arbiter-v1")
            memories_path = output / "pilot-memory.json"
            memories = {}
            if memories_path.exists():
                memories = {
                    principal: PilotMemory.from_dict(value)
                    for principal, value in json.loads(
                        memories_path.read_text(encoding="utf-8")
                    ).items()
                }
            runner = SequentialPilotRunner(
                session,
                providers,
                arbiter=arbiter,
                memories=memories,
            )
            invocations = 0
            while (
                not session.state.game_over
                and session.state.turn_sequence < args.through_turn
                and invocations < args.max_invocations
            ):
                if not runner.step():
                    break
                invocations += 1
                memories_path.write_text(
                    stable_json(
                        {
                            principal: memory.to_dict()
                            for principal, memory in runner.memories.items()
                        }
                    ),
                    encoding="utf-8",
                )
                session.save(output)
            memories_path.write_text(
                stable_json(
                    {
                        principal: memory.to_dict()
                        for principal, memory in runner.memories.items()
                    }
                ),
                encoding="utf-8",
            )
            session.save(output)
            benchmark = {
                "schema_version": 1,
                "game_id": session.state.game_id,
                "through_turn_sequence": session.state.turn_sequence,
                "provider_segment": runner.metrics.to_dict(),
                "notes": {
                    "observed_tokens": (
                        "Provider-reported only; null when the adapter supplied no usage."
                    ),
                    "estimated_tokens": (
                        "Compact packet/response character estimates; never labeled observed."
                    ),
                    "resume_scope": (
                        "Provider-segment packet metrics cover this pilot-run invocation; "
                        "review.json derives durable decision/provider totals across the record."
                    ),
                },
            }
            (output / "call-benchmark.json").write_text(
                stable_json(benchmark), encoding="utf-8"
            )
            # Include the benchmark file in the stable record-size review.
            session.save(output)
            print(
                stable_json(
                    {
                        "game_id": session.state.game_id,
                        "record": str(output),
                        "turn_sequence": session.state.turn_sequence,
                        "game_over": session.state.game_over,
                        "pending": session.pending_principals(),
                        "metrics": benchmark["provider_segment"],
                    }
                )
            )
        finally:
            db.close()
        return 0
    if args.cmd == "inspect-game":
        result = inspect_game(args.path)
        print(stable_json(result) if args.pretty else json.dumps(result, separators=(",", ":"), ensure_ascii=False))
        return 0

    if args.cmd == "migrate-record":
        db = CardDatabase(args.db)
        try:
            manifest = migrate_v2_game(
                args.game_json,
                args.output,
                db,
                trace_level=args.trace_level,
                semantics_path=Path(args.output) / "semantics.json",
            )
            session = CommanderSession.load(
                db,
                args.output,
                semantics_path=Path(args.output) / "semantics.json",
            )
            write_review_artifacts(
                args.output,
                session.engine,
                decisions=session.decisions,
                manifest=manifest,
            )
            print(stable_json(inspect_game(args.output)))
        finally:
            db.close()
        return 0

    if args.cmd == "replay":
        db = CardDatabase(args.db)
        try:
            if args.verify:
                refreshed = refresh_record(
                    args.record, db, verify_replay=True
                )
                result = dict(refreshed["replay_result"] or {})
            else:
                result = replay_record(
                    args.record,
                    db,
                    semantics_path=Path(args.record) / "semantics.json",
                    verify=False,
                )
            print(stable_json(result))
            return 0 if result["ok"] else 2
        finally:
            db.close()

    if args.cmd in {"new", "duel"}:
        if args.cmd == "duel":
            sources = {"A": args.deck_a, "B": args.deck_b}
            commanders: dict[str, str] = {}
        else:
            sources = _seat_values(args.seat)
            commanders = _seat_values(args.commander)
        if not 2 <= len(sources) <= 6:
            raise SystemExit("Supply 2-6 --seat arguments; four is the Commander default")
        db = CardDatabase(args.db)
        try:
            config = GameConfig(
                seed=args.seed,
                profile=args.profile,
                trace_level=args.trace_level,
            )
            session = CommanderSession.from_sources(
                db,
                sources,
                commanders=commanders,
                first_player=args.first or next(iter(sources)),
                seed=args.seed,
                cache_dir=args.cache_dir,
                force_refresh=args.refresh_decks,
                semantics_path=Path(args.out) / "semantics.json",
                config=config,
            )
            session.save(args.out)
            print(
                stable_json(
                    {
                        "game_id": session.state.game_id,
                        "dir": args.out,
                        "decks": session.state.deck_names,
                        "pending": session.pending_principals(),
                    }
                )
            )
        finally:
            db.close()
        return 0

    if args.cmd == "rules" and (not args.db or not args.game):
        raise SystemExit(
            "Card rules lookup requires --db and --game; corpus operations "
            "use `simctl rules <operation>`"
        )
    game_path = (args.game or args.record) if args.cmd == "report" else args.game
    if not game_path:
        raise SystemExit("report requires a record directory (positional or --game)")
    db, session = _load(args.db, game_path)
    try:
        if args.cmd == "task":
            packet = session.packet(args.principal, full=args.full) if args.principal else session.next_task(full=args.full)
            session.save(game_path)
            print(stable_json(packet) if args.pretty else json.dumps(packet, separators=(",", ":"), ensure_ascii=False))
        elif args.cmd == "act":
            raw = Path(args.file).read_text(encoding="utf-8") if args.file else args.json
            response: dict[str, Any] = json.loads(raw)
            result = session.act(args.principal, response)
            session.save(game_path)
            print(stable_json({"ok": result.ok, "summary": result.summary, "events": result.event_ids, "pending": session.pending_principals()}))
            return 0 if result.ok else 2
        elif args.cmd == "rules":
            print(session.rules(args.cards))
        elif args.cmd == "report":
            manifest_path = Path(game_path) / "manifest.json"
            manifest = (
                json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest_path.exists()
                else None
            )
            review = write_review_artifacts(
                game_path,
                session.engine,
                decisions=session.decisions,
                manifest=manifest,
            )
            print(review_markdown(review), end="")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
