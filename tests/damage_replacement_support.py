from __future__ import annotations

import json
from pathlib import Path
import random
import tempfile
import unittest

from common import ROOT, keep_all, make_session
from scripts.build_test_database import build_fixture_database
from quorune.carddb import CardDatabase
from quorune.damage import (
    commit_prepared_damage_batch,
    damage_proposal,
    DamageEvent,
    DamageError,
    DamageRecipientSnapshot,
    prepare_damage_batch,
)
from quorune.deck import DeckLoader
from quorune.engine import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    load_default_capability_registry,
)
from quorune.replacement_effects import (
    ReplacementChoiceRequired,
    resolve_replacements,
)
from quorune.semantic_runtime import (
    DamageQuantityReplacementHandler,
    DamageReplacementSourceContext,
    FixedDamagePreventionHandler,
    SemanticNodeError,
    default_damage_replacement_registry,
)
from quorune.semantics import SemanticProgram


import atexit

def damage_condition(
    *,
    source_controller_relation: str = "any",
    target_controller_relation: str = "any",
    target_kinds: list[str] | None = None,
    source_types_all: list[str] | None = None,
    target_types_all: list[str] | None = None,
    combat: bool | None = None,
) -> dict:
    return {
        "source_controller_relation": source_controller_relation,
        "target_controller_relation": target_controller_relation,
        "target_kinds": list(target_kinds or []),
        "source_types_all": list(source_types_all or []),
        "target_types_all": list(target_types_all or []),
        "combat": combat,
    }


def quantity_descriptor(
    *,
    multiplier: int = 2,
    additional: int = 0,
    condition: dict | None = None,
) -> dict:
    return {
        "handler_id": "replacement.damage.quantity.v1",
        "schema_version": 1,
        "event": "damage",
        "condition": condition or damage_condition(),
        "modification": {
            "multiplier": multiplier,
            "additional": additional,
        },
    }


def additive_quantity_descriptor(
    *,
    additional: int = 1,
    source_controller_relation: str = "source_controller",
    target_controller_relation: str = "opponent",
    target_kinds: list[str] | None = None,
    source_types_all: list[str] | None = None,
    source_types_any: list[str] | None = None,
    source_colors_all: list[str] | None = None,
    target_types_all: list[str] | None = None,
    combat: bool | None = None,
    exclude_source_ref: bool = False,
) -> dict:
    return {
        "handler_id": "replacement.damage.quantity.v2",
        "schema_version": 2,
        "event": "damage",
        "condition": {
            "source_controller_relation": source_controller_relation,
            "target_controller_relation": target_controller_relation,
            "target_kinds": list(target_kinds or []),
            "source_types_all": list(source_types_all or []),
            "source_types_any": list(source_types_any or []),
            "source_colors_all": list(source_colors_all or []),
            "target_types_all": list(target_types_all or []),
            "combat": combat,
            "exclude_source_ref": exclude_source_ref,
        },
        "modification": {"multiplier": 1, "additional": additional},
    }


def prevention_descriptor(
    *,
    amount: int = 1,
    condition: dict | None = None,
) -> dict:
    return {
        "handler_id": "prevention.damage.fixed.v1",
        "schema_version": 1,
        "event": "damage",
        "condition": condition or damage_condition(),
        "modification": {"amount": amount},
    }




class DamageReplacementPipelineBase(unittest.TestCase):
    """Shared fixture and helpers for focused damage replacement shards."""

    _fixture_temporary = None
    db = None
    mishra = None
    zimone = None

    @classmethod
    def setUpClass(cls):
        base = DamageReplacementPipelineBase
        if base._fixture_temporary is None:
            base._fixture_temporary = tempfile.TemporaryDirectory()
            # The shared SQLite connection must close before Windows can
            # remove the file. Own cleanup explicitly instead of allowing
            # TemporaryDirectory's weakref finalizer to race our atexit hook.
            base._fixture_temporary._finalizer.detach()
            database = (
                Path(base._fixture_temporary.name)
                / "damage-replacements.sqlite3"
            )
            build_fixture_database(
                [
                    ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "damage-replacement-cards.json",
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "prevention-immediate-cards.json",
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "fixed-all-damage-prevention-cards.json",
                    ROOT
                    / "tests"
                    / "fixtures"
                    / "counter-replacement-cards.json",
                ],
                database,
            )
            base.db = CardDatabase(database)
            loader = DeckLoader(base.db)
            base.mishra = loader.load(
                ROOT / "examples" / "mishra-eminent-one.txt",
                commander="Mishra, Eminent One",
                deck_name="Mishra",
            )
            base.zimone = loader.load(
                ROOT / "examples" / "zimone-and-dina.txt",
                commander="Zimone and Dina",
                deck_name="Zimone",
            )
        cls.db = base.db
        cls.mishra = base.mishra
        cls.zimone = base.zimone

    @classmethod
    def tearDownClass(cls):
        # The module-global fixture is closed once at interpreter shutdown so
        # separate unittest shards do not rebuild the SQLite database.
        pass

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
        session.commands.clear()
        session.decisions.clear()
        return session


    def add_permanent(
        self,
        engine,
        *,
        seat: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.db.lookup(name)
        # Direct fixture injection bypasses CommanderSession's deck compiler.
        # Register the same exact per-card runtime descriptors a real loaded
        # deck receives before placing this object into authoritative state.
        register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_capability_declarations=True,
        )
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


    def stage_sources(self, engine):
        furnace = self.add_permanent(
            engine,
            seat="A",
            name="Furnace of Rath",
            ref="a-furnace",
        )
        defender = self.add_permanent(
            engine,
            seat="B",
            name="Daunting Defender",
            ref="b-defender",
        )
        # Keep this witness on the battlefield after Furnace raises the final
        # damage amount above Daunting Defender's printed toughness.
        defender.counters["+1/+1"] = 5
        source = self.add_permanent(
            engine,
            seat="A",
            name="Mishra, Eminent One",
            ref="a-source",
        )
        return furnace, defender, source


    def proposal(
        self,
        engine,
        *,
        source: CardInstance,
        target: CardInstance | str,
        amount: int = 3,
        event_id: str = "damage:test",
        unpreventable: bool = False,
        combat: bool = False,
    ):
        return damage_proposal(
            engine,
            proposal_id=event_id,
            actor=source.controller,
            source_ref=source.ref,
            target=target.ref if isinstance(target, CardInstance) else target,
            amount=amount,
            combat=combat,
            reason="damage replacement test",
            unpreventable=unpreventable,
        )


    @staticmethod
    def _property_event(amount: int, index: int):
        from quorune.replacement_effects import ReplaceableEvent

        return ReplaceableEvent(
            event_id=f"damage:property:{index}",
            kind="damage",
            affected_player="B",
            payload={
                "source_controller": "A",
                "target_controller": "B",
                "target_kind": "player",
                "source_characteristics": [],
                "target_characteristics": [],
                "combat": False,
                "amount": amount,
                "prevented": 0,
                "unpreventable": False,
            },
        )



def _close_damage_replacement_fixture():
    base = DamageReplacementPipelineBase
    if base.db is not None:
        base.db.close()
        base.db = None
    if base._fixture_temporary is not None:
        base._fixture_temporary.cleanup()
        base._fixture_temporary = None


atexit.register(_close_damage_replacement_fixture)
