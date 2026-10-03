from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path

from quorune import CardDatabase, CommanderSession, DeckLoader, GameConfig
from quorune.counter_removal import (
    commit_counter_removal_effect,
    CounterRemoval,
    plan_counter_removal_effect,
)
from quorune.model import TurnHistory
from quorune.semantics import SemanticProgram
from quorune.util import stable_json

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("MTG_CARD_DB", ROOT / "data" / "scryfall-20260728-compact.sqlite3"))


@contextmanager
def without_direct_resolution_compiler(name: str):
    """Remove a live leaf route, not an alias captured before patching."""
    from unittest.mock import patch
    from quorune.compiler import resolution_effect_templates as owner
    compiler = getattr(owner, name)
    routes = owner._DIRECT_RESOLUTION_EFFECT_COMPILERS
    remaining = tuple(route for route in routes if route is not compiler)
    if len(remaining) != len(routes)-1:
        raise AssertionError(f"Expected one live direct compiler route for {name}")
    with patch.object(owner, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", remaining):
        yield


def load_assets():
    db = CardDatabase(DB_PATH)
    loader = DeckLoader(db)
    mishra = loader.load(ROOT / "examples" / "mishra-eminent-one.txt", commander="Mishra, Eminent One", deck_name="Mishra")
    zimone = loader.load(ROOT / "examples" / "zimone-and-dina.txt", commander="Zimone and Dina", deck_name="Zimone")
    return db, mishra, zimone


def make_session(db, mishra, zimone, *, players=4, seed=1, auto_pass_empty=False):
    seats = [chr(ord("A") + i) for i in range(players)]
    decks = {seat: (mishra if i % 2 == 0 else zimone) for i, seat in enumerate(seats)}
    return CommanderSession.create(
        db,
        decks,
        first_player="A",
        seed=seed,
        config=GameConfig(seed=seed, auto_pass_empty_priority=auto_pass_empty),
    )


def keep_all(session):
    while session.state.pending_decision and session.state.pending_decision.kind == "mulligan.declare":
        for principal in list(session.pending_principals()):
            result = session.act(principal, {"a": "keep"})
            assert result.ok, result.summary


def pass_current(session, *, yield_mode=None):
    principals = session.pending_principals()
    assert principals
    principal = principals[0]
    response = {"a": "pass"}
    if yield_mode:
        response["y"] = yield_mode
    result = session.act(principal, response)
    assert result.ok, result.summary
    return principal


def register_token_quantity_multiplier(
    db,
    engine,
    source,
    capability_registry,
):
    """Register the generic trusted runtime fixture without Oracle admission."""

    capability_id = "token.creation.quantity_replacement"
    closure = capability_registry.closure(
        (capability_id,),
        profile="commander_review",
    )
    assert closure.trusted, closure.blockers
    record = db.by_oracle_id(source.oracle_id)
    oracle_hash = hashlib.sha256(
        record.oracle_text.encode("utf-8")
    ).hexdigest()
    rulings_hash = hashlib.sha256(
        stable_json(
            sorted(
                (asdict(ruling) for ruling in db.rulings(record)),
                key=lambda row: (
                    str(row["published_at"]),
                    str(row["source"]),
                    str(row["comment"]),
                    str(row["oracle_id"]),
                ),
            )
        ).encode("utf-8")
    ).hexdigest()
    program = SemanticProgram(
        key=f"fixture:{record.oracle_id}:token-quantity-replacement",
        label="Generic token quantity replacement",
        oracle_id=record.oracle_id,
        ability_id="replacement:front:token-quantity",
        active_zone="battlefield",
        event="token.create",
        trust_level="trusted",
        provenance={
            "compiler_version": "generic-runtime-fixture-v1",
            "authored_by": "test fixture",
            "face_id": "front",
            "review_status": "focused runtime fixture",
            "source_oracle_hash": oracle_hash,
            "source_rulings_hash": rulings_hash,
            "template_id": "generic-token-quantity-runtime-fixture-v1",
        },
        handlers=[
            {
                "handler_id": "replacement.token.quantity.v1",
                "schema_version": 1,
                "event": "token.create",
                "condition": {
                    "event_controller": "source_controller",
                },
                "multiplier": 2,
            }
        ],
        tests=["generic token quantity replacement runtime fixture"],
        capability_dependencies=[capability_id],
        capability_closure=closure.to_dict(),
    )
    engine.semantics.put(program)
    assert engine.semantic_program_is_current_trusted(program)
    return program


def set_fixture_turn(engine, turn_sequence: int) -> None:
    """Move a directly seeded rules fixture to a clean turn boundary."""

    engine.state.turn_sequence = int(turn_sequence)
    if engine.state.turn_history is not None:
        engine.state.turn_history = TurnHistory(
            turn_sequence=engine.state.turn_sequence
        )


def advance_fixture_turn(engine, count: int = 1) -> None:
    set_fixture_turn(engine, engine.state.turn_sequence + int(count))


def change_permanent_counter(engine, card, name: str, delta: int) -> tuple[int, int]:
    """Apply a negative fixture delta through the production removal owner."""

    if type(delta) is not int or delta >= 0:
        raise ValueError("Fixture counter removal requires a negative integer")
    result = commit_counter_removal_effect(
        engine,
        plan_counter_removal_effect(
            engine,
            CounterRemoval(
                card.object_id,
                name,
                -delta,
                expected_zone=card.zone,
                expected_logical_object_id=card.logical_object_id,
            ),
        ),
    )
    if result.counter_name == "defense" and result.before and not result.after:
        engine._queue_siege_defeated_trigger(card)
    return result.before, result.after
