from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from quorune.object_query import ObjectQueryResult
from quorune.replacement.immutable import FrozenMap
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantic_choices.context import (
    SemanticChoiceContext,
    SnapshotSemanticChoiceQuery,
)
from quorune.semantic_choices.library_and_hand import ExploreChoiceHandler
from quorune.semantic_choices.intent_replacement import (
    semantic_intent_identity,
    validate_semantic_intent_identity,
)
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_runtime.intents import (
    ExploreCompletedIntent,
    PlaceCountersIntent,
    RevealLibraryCardsIntent,
    ZoneMoveIntent,
)
from quorune.semantic_runtime.explore import (
    capture_explore_source_departure,
    explore_source_controller,
)
from quorune.semantics import SemanticProgram, SemanticRegistry
from quorune.model import CardInstance, StackItem
from tests.common import keep_all, load_assets, make_session


def _row(
    ref: str,
    *,
    zone: str,
    logical: str,
    controller: str = "A",
    owner: str = "A",
    types: tuple[str, ...] = (),
    phased_out: bool = False,
) -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id=f"object:{ref}",
        logical_object_id=logical,
        ref=ref,
        printed_name=ref,
        owner=owner,
        controller=controller,
        zone=zone,
        types=types,
        phased_out=phased_out,
    )


def _context(
    explorer: ObjectQueryResult,
    top: ObjectQueryResult | None,
    *,
    pinned_logical: str = "logical:explorer",
) -> SemanticChoiceContext:
    rows = (explorer,) if top is None else (explorer, top)
    library = () if top is None else (top.ref,)
    return SemanticChoiceContext(
        actor="A",
        stack_ref="S1",
        stack_controller="A",
        stack_label="Explore fixture",
        source_ref=explorer.ref,
        card_ref=None,
        semantic_program_id="fixture:explore",
        semantic_program_version=1,
        query=SnapshotSemanticChoiceQuery(
            seat_order=("A", "B"),
            active_order=("A", "B"),
            object_rows=rows,
            libraries_by_seat=FrozenMap({"A": library}),
        ),
        source_logical_object_id=pinned_logical,
    )


class ExploreRuleTests(unittest.TestCase):
    def setUp(self):
        self.handler = ExploreChoiceHandler()

    def test_nonland_reveal_places_counter_before_request(self):
        explorer = _row(
            "P1",
            zone="battlefield",
            logical="logical:explorer",
            types=("creature",),
        )
        top = _row(
            "C1",
            zone="library",
            logical="logical:top",
            types=("instant",),
        )
        preparation = self.handler.prepare(
            {"op": "explore", "player": "A", "card": "P1"},
            _context(explorer, top),
        )
        self.assertIsNotNone(preparation.request)
        self.assertEqual(
            (RevealLibraryCardsIntent, PlaceCountersIntent),
            tuple(type(intent) for intent in preparation.preparation_intents),
        )

    def test_old_or_phased_incarnation_cannot_receive_explore_counter(self):
        top = _row(
            "C1",
            zone="library",
            logical="logical:top",
            types=("instant",),
        )
        for explorer in (
            _row(
                "P1",
                zone="graveyard",
                logical="logical:new-zone",
                types=("creature",),
            ),
            _row(
                "P1",
                zone="battlefield",
                logical="logical:explorer",
                types=("creature",),
                phased_out=True,
            ),
        ):
            with self.subTest(zone=explorer.zone, phased=explorer.phased_out):
                preparation = self.handler.prepare(
                    {"op": "explore", "player": "A", "card": "P1"},
                    _context(explorer, top),
                )
                self.assertEqual(
                    (RevealLibraryCardsIntent,),
                    tuple(
                        type(intent)
                        for intent in preparation.preparation_intents
                    ),
                )

    def test_empty_library_still_completes_explore(self):
        explorer = _row(
            "P1",
            zone="battlefield",
            logical="logical:explorer",
            types=("creature",),
        )
        preparation = self.handler.prepare(
            {"op": "explore", "player": "A", "card": "P1"},
            _context(explorer, None),
        )
        self.assertEqual(1, len(preparation.preparation_intents))
        completed = preparation.preparation_intents[0]
        self.assertIsInstance(completed, ExploreCompletedIntent)
        self.assertEqual("empty_library", completed.result)

    def test_land_reveal_moves_to_hand_then_marks_explored(self):
        explorer = _row(
            "P1",
            zone="battlefield",
            logical="logical:explorer",
            types=("creature",),
        )
        top = _row(
            "C1",
            zone="library",
            logical="logical:top",
            types=("land",),
        )
        preparation = self.handler.prepare(
            {"op": "explore", "player": "A", "card": "P1"},
            _context(explorer, top),
        )
        self.assertEqual(
            (
                RevealLibraryCardsIntent,
                ZoneMoveIntent,
                ExploreCompletedIntent,
            ),
            tuple(type(intent) for intent in preparation.preparation_intents),
        )

    def test_source_controller_uses_current_then_departure_lki(self):
        registry = SemanticRegistry(include_builtin_packs=False)
        registry._programs["fixture:explore"] = SemanticProgram(
            key="fixture:explore",
            label="Explore fixture",
            effects=[
                {
                    "op": "explore",
                    "player": "$source.controller",
                    "card": "$source",
                }
            ],
        )
        card = SimpleNamespace(
            object_id="object:P1",
            logical_object_id="logical:one",
            ref="P1",
            controller="B",
            zone="battlefield",
        )
        item = StackItem(
            stack_id="stack:S1",
            ref="S1",
            kind="triggered_ability",
            controller="A",
            label="Explore fixture",
            source_object_id=card.object_id,
            semantic_key="fixture:explore",
            context={"source_logical_object_id": card.logical_object_id},
        )
        host = SimpleNamespace(
            semantics=registry,
            state=SimpleNamespace(
                stack=[item],
                pending_trigger_batches=[],
            ),
        )
        cards = {card.object_id: card}
        self.assertEqual("B", explore_source_controller(item, cards))
        self.assertEqual(1, capture_explore_source_departure(host, card))
        card.zone = "graveyard"
        card.controller = "A"
        card.logical_object_id = "logical:two"
        self.assertEqual("B", explore_source_controller(item, cards))


class ExploreEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.database.close()

    def _session(self, seed: int, *, players: int = 2):
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
        engine.state.stack.clear()
        engine.semantics.put(
            SemanticProgram(
                key="fixture:explore",
                label="Explore fixture",
                effects=[
                    {
                        "op": "explore",
                        "player": "$source.controller",
                        "card": "$source",
                    }
                ],
            )
        )
        return session

    @staticmethod
    def _card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def _put_on_top(engine, card) -> None:
        engine.move_card(card.object_id, "library", log=False)
        library = engine.state.players[card.owner].zones["library"]
        library.remove(card.object_id)
        library.append(card.object_id)

    @staticmethod
    def _stack_explore(engine, source, controller: str) -> None:
        ref = engine._next_ref("S")
        engine.state.stack.append(
            StackItem(
                stack_id=engine._stable_runtime_id("stack", ref),
                ref=ref,
                kind="triggered_ability",
                controller=controller,
                label="Explore fixture",
                source_object_id=source.object_id,
                semantic_key="fixture:explore",
                visibility=list(engine.seats),
                context={
                    "source_logical_object_id": source.logical_object_id,
                },
            )
        )

    def _add_permanent(
        self,
        engine,
        *,
        owner: str,
        controller: str,
        name: str,
        ref: str,
    ) -> CardInstance:
        record = self.database.lookup(name)
        card = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=owner,
            controller=controller,
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[controller].zones["battlefield"].append(
            card.object_id
        )
        return card

    def _stage_counter_replacements(self, engine) -> None:
        self._add_permanent(
            engine,
            owner="A",
            controller="A",
            name="Doubling Season",
            ref="explore-doubling",
        )
        self._add_permanent(
            engine,
            owner="A",
            controller="A",
            name="Doc Samson, Super Psychiatrist",
            ref="explore-additional",
        )

    def _select_replacement(self, session, seat: str) -> None:
        projected = StateProjector(
            self.database, session.engine.state
        )._decision(f"pilot:{seat}")
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            f"pilot:{seat}",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)

    def test_nonland_explore_uses_current_controller_and_completes(self):
        session = self._session(7014401)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        top = self._card(engine, "A", "Sol Ring")
        engine.move_card(explorer.object_id, "battlefield", controller="A")
        self._put_on_top(engine, top)
        self._stack_explore(engine, explorer, "A")

        engine._prepare_stack_resolution()
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(1, explorer.counters.get("+1/+1"))
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "top",
                "plan": "KEEP_TOP",
                "reason": "Keep the nonland card on top.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("library", top.zone)
        self.assertTrue(
            any(event.code == "explore.complete" for event in engine.state.events)
        )

    def test_departed_explorer_uses_lki_controller_without_countering_return(self):
        session = self._session(7014402)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        top = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and not card.is_commander
            and not self.database.lookup(card.printed_name).is_land
            and card.object_id != explorer.object_id
        )
        engine.move_card(explorer.object_id, "battlefield", controller="A")
        self._stack_explore(engine, explorer, "A")
        engine.change_control(explorer.object_id, "B", reason="Explore LKI")
        engine.move_card(explorer.object_id, "graveyard", reason="Explore LKI")
        self._put_on_top(engine, top)

        engine._prepare_stack_resolution()
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        self.assertEqual(["B"], engine.state.pending_decision.actors)
        self.assertNotIn("+1/+1", explorer.counters)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": "top",
                "plan": "KEEP_TOP",
                "reason": "Complete the LKI Explore instruction.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "explore-source-lki-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(1, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_four_player_explore_choice_is_controller_scoped_and_public(self):
        session = self._session(7014403, players=4)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        top = self._card(engine, "C", "Sol Ring")
        engine.move_card(explorer.object_id, "battlefield", controller="C")
        self._put_on_top(engine, top)
        self._stack_explore(engine, explorer, "C")

        engine._prepare_stack_resolution()
        decision = engine.state.pending_decision
        self.assertEqual(["C"], decision.actors)
        self.assertEqual({"C"}, set(decision.payload_by_actor))
        self.assertIsNone(session.packet("pilot:B").get("decision"))
        self.assertEqual(
            top.ref,
            decision.payload_by_actor["C"]["card"]["id"],
        )

    def test_counter_and_zone_replacements_suspend_before_explore_mutation(self):
        counter_session = self._session(7014404)
        counter_engine = counter_session.engine
        explorer = self._card(counter_engine, "A", "Goblin Engineer")
        nonland = self._card(counter_engine, "A", "Sol Ring")
        counter_engine.move_card(
            explorer.object_id, "battlefield", controller="A"
        )
        self._put_on_top(counter_engine, nonland)
        self._stage_counter_replacements(counter_engine)
        self._stack_explore(counter_engine, explorer, "A")

        counter_engine._prepare_stack_resolution()
        self.assertEqual(
            "replacement.order", counter_engine.state.pending_decision.kind
        )
        self.assertEqual("semantic_preparation", counter_engine.state.pending_decision.continuation["replacement_resume_kind"])
        self.assertNotIn("+1/+1", explorer.counters)
        self.assertEqual(
            1,
            sum(
                event.code == "library.look"
                for event in counter_engine.state.events
            ),
        )
        self._select_replacement(counter_session, "A")
        self.assertEqual(
            "semantic.choice", counter_engine.state.pending_decision.kind
        )
        self.assertGreater(explorer.counters["+1/+1"], 1)
        self.assertEqual(
            1,
            sum(
                event.code == "library.look"
                for event in counter_engine.state.events
            ),
        )

        zone_session = self._session(7014405)
        zone_engine = zone_session.engine
        explorer = self._card(zone_engine, "B", "Elves of Deep Shadow")
        land = next(
            card
            for card in zone_engine.state.cards.values()
            if card.owner == "B" and self.database.lookup(card.printed_name).is_land
        )
        zone_engine.move_card(
            explorer.object_id, "battlefield", controller="B"
        )
        self._put_on_top(zone_engine, land)
        sources = [
            self._add_permanent(
                zone_engine,
                owner="A",
                controller="A",
                name="Island",
                ref=f"hand-replacement-{index}",
            )
            for index in range(2)
        ]
        zone_engine.semantics.put(
            SemanticProgram(
                key="fixture:hand-replacement",
                label="Replace an opponent card moving to hand",
                oracle_id=sources[0].oracle_id,
                ability_id="test:explore-hand-replacement",
                active_zone="battlefield",
                event="zone.change",
                trust_level="provisional",
                handlers=[
                    {
                        "handler_id": "replacement.zone.destination.v1",
                        "schema_version": 1,
                        "event": "zone.change",
                        "condition": {
                            "destination": "hand",
                            "object_kind": "card",
                            "owner_relation": "opponent",
                        },
                        "destination": "exile",
                        "counters": {},
                    }
                ],
            )
        )
        original_trust = zone_engine.semantic_program_is_current_trusted
        zone_engine.semantic_program_is_current_trusted = lambda program: (
            program.key == "fixture:hand-replacement"
            or original_trust(program)
        )
        self._stack_explore(zone_engine, explorer, "B")

        zone_engine._prepare_stack_resolution()
        decision = zone_engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["B"], decision.actors)
        self.assertEqual("library", land.zone)
        projected = StateProjector(self.database, zone_engine.state)
        packet = projected._decision("pilot:B")
        self.assertIsNone(projected._decision("pilot:A"))
        self.assertNotIn("semantic_intent", json.dumps(packet, sort_keys=True))
        self._select_replacement(zone_session, "B")
        self.assertEqual("exile", land.zone)
        self.assertFalse(zone_engine.state.stack)
        self.assertEqual(
            1,
            sum(event.code == "library.look" for event in zone_engine.state.events),
        )

    def test_explore_preparation_replacement_resume_replays_exactly(self):
        session = self._session(7014406)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        nonland = self._card(engine, "A", "Sol Ring")
        engine.move_card(explorer.object_id, "battlefield", controller="A")
        self._put_on_top(engine, nonland)
        self._stage_counter_replacements(engine)
        self._stack_explore(engine, explorer, "A")
        engine._prepare_stack_resolution()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        self._select_replacement(session, "A")
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "top",
                "plan": "KEEP_TOP",
                "reason": "Keep the revealed nonland on top.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "explore-preparation-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_explore_graveyard_zone_replacement_suspends_and_replays(self):
        session = self._session(7014408)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        nonland = self._card(engine, "A", "Sol Ring")
        engine.move_card(explorer.object_id, "battlefield", controller="A")
        self._put_on_top(engine, nonland)
        for index in range(2):
            self._add_permanent(
                engine,
                owner="B",
                controller="B",
                name="Dauthi Voidwalker",
                ref=f"explore-voidwalker-{index}",
            )
        self._stack_explore(engine, explorer, "A")
        engine._prepare_stack_resolution()
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "graveyard",
                "plan": "MILL_TOP",
                "reason": "Move the revealed nonland to the graveyard.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        decision = engine.state.pending_decision
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(
            "semantic_intent_completion",
            decision.continuation["replacement_resume_kind"],
        )
        self.assertEqual("library", nonland.zone)
        self._select_replacement(session, "A")
        self.assertEqual("exile", nonland.zone)
        self.assertEqual(1, nonland.counters["void"])
        self.assertFalse(engine.state.stack)
        expected_hash = authoritative_state_hash(engine.state)

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "explore-completion-replay"
            session.save(record_dir)
            replay = replay_record(record_dir, self.database, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_tampered_explore_preparation_identity_rolls_back(self):
        session = self._session(7014407)
        engine = session.engine
        explorer = self._card(engine, "A", "Goblin Engineer")
        nonland = self._card(engine, "A", "Sol Ring")
        engine.move_card(explorer.object_id, "battlefield", controller="A")
        self._put_on_top(engine, nonland)
        self._stage_counter_replacements(engine)
        self._stack_explore(engine, explorer, "A")
        engine._prepare_stack_resolution()
        decision = engine.state.pending_decision
        decision.continuation["semantic_intent"]["amount"] = 2
        packet = StateProjector(self.database, engine.state)._decision("pilot:A")
        selected = packet["ctx"]["options"][0]["id"]
        before = authoritative_state_hash(engine.state)
        capability = engine.permissions.capability_for("pilot:A")
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("changed before replacement resume", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertNotIn("+1/+1", explorer.counters)

        decision = engine.state.pending_decision
        decision.continuation["semantic_intent"]["amount"] = 1
        decision.continuation["semantic_intent_kind"] = "future_intent"
        before = authoritative_state_hash(engine.state)
        capability = engine.permissions.capability_for("pilot:A")
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )
        self.assertFalse(rejected.ok)
        self.assertIn("fields are malformed", rejected.summary)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertNotIn("+1/+1", explorer.counters)

    def test_zone_move_intent_identity_is_strict_and_material(self):
        intent = ZoneMoveIntent(
            actor="A",
            object_ref="A01",
            expected_zones=("library",),
            destination="hand",
            reason="explore",
            owned_only=True,
        )
        kind, identity = semantic_intent_identity(intent)
        self.assertEqual("zone_move", kind)
        self.assertEqual(identity, validate_semantic_intent_identity(kind, identity))
        for label, mutate, message in (
            (
                "unknown field",
                lambda row: row.update({"future": True}),
                "unknown future",
            ),
            (
                "boolean predicate",
                lambda row: row.update({"owned_only": 1}),
                "must be a boolean",
            ),
            (
                "changed destination",
                lambda row: row.update({"destination": "graveyard"}),
                None,
            ),
        ):
            with self.subTest(label=label):
                changed = copy.deepcopy(identity)
                mutate(changed)
                if message is None:
                    self.assertNotEqual(
                        identity,
                        validate_semantic_intent_identity(kind, changed),
                    )
                else:
                    with self.assertRaisesRegex(SemanticChoiceError, message):
                        validate_semantic_intent_identity(kind, changed)


if __name__ == "__main__":
    unittest.main()
