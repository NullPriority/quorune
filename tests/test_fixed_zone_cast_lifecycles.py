from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase
from quorune.cast_lifecycles import (
    FIXED_CAST_LIFECYCLE_CONTEXT_FIELD,
    FIXED_CAST_LIFECYCLE_STACK_ANNOTATION,
    FIXED_ZONE_CAST_DESIGNATION_FIELD,
    FixedCastLifecycleKind,
    REBOUND_CAST_SEMANTIC_KEY,
)
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting.commit import commit_cast
from quorune.rules.casting.model import CastProposalRequest
from quorune.rules.casting.model import CastProposalError
from quorune.rules.casting.proposal import build_cast_proposal
from quorune.session import CommanderSession
from quorune.semantics import SemanticProgram
from quorune.trigger_processing import (
    begin_pending_trigger_batch,
    collect_trigger_items,
    enqueue_trigger_batch,
)
from quorune.rules.staged_cast_lifecycles import (
    build_staged_cast_offer,
    commit_staged_cast_lifecycle,
)
from scripts.build_test_database import build_fixture_database


LIFECYCLE_FIXTURE = ROOT / "tests" / "fixtures" / "fixed-zone-cast-lifecycles.json"


class FixedZoneCastLifecycleCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-zone-lifecycle.sqlite3"
        build_fixture_database([LIFECYCLE_FIXTURE], database)
        cls.db = CardDatabase(database)
        cls.capabilities = load_default_capability_registry()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.temporary.cleanup()

    def test_five_fixed_zone_cast_lifecycles_compile_through_shared_owner(self):
        expected = {
            "Foretell Lifecycle Fixture": ("foretell", "{1}{U}", None),
            "Plot Lifecycle Fixture": ("plot", "{2}{R}", None),
            "Escape Lifecycle Fixture": ("escape", "{2}{B}", 2),
            "Rebound Lifecycle Fixture": ("rebound", None, None),
            "Jump-start Lifecycle Fixture": ("jump-start", None, None),
        }
        for name, values in expected.items():
            with self.subTest(name=name):
                compiled = compile_oracle_card(
                    self.db.lookup(name),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                node = next(
                    node
                    for face in compiled.faces
                    for node in face.nodes
                    if node.event == "cast.cost"
                )
                lifecycle = node.handlers[0]["lifecycle"]
                self.assertEqual(values[0], lifecycle["kind"])
                self.assertEqual(values[1], lifecycle["cost_text"])
                self.assertEqual(values[2], lifecycle.get("exile_count"))
                self.assertIn(
                    "current_ability_fragment_required",
                    node.runtime_coverage,
                )

    def test_open_zone_cast_forms_remain_residual(self):
        base = self.db.lookup("Escape Lifecycle Fixture")
        cases = (
            ("Escape—{X}{B}, Exile two other cards from your graveyard.", ("Escape",)),
            ("Escape—{2}{B}, Exile any number of other cards from your graveyard.", ("Escape",)),
            ("Foretell {W/U}", ("Foretell",)),
            ("Plot—Sacrifice a creature", ("Plot",)),
            ("Jump-start—Pay 2 life", ("Jump-Start",)),
            ("Rebound {2}", ("Rebound",)),
        )
        for text, keywords in cases:
            with self.subTest(text=text):
                compiled = compile_oracle_card(
                    replace(base, oracle_text=text, keywords=keywords),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

        for text, keyword in (("Rebound", "Rebound"), ("Jump-start", "Jump-Start")):
            with self.subTest(text=text, type_line="Creature"):
                compiled = compile_oracle_card(
                    replace(
                        base,
                        oracle_text=text,
                        keywords=(keyword,),
                        type_line="Creature — Wizard",
                        power="2",
                        toughness="2",
                    ),
                    capability_registry=self.capabilities,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_zone_lifecycle_dependency_and_compiler_mutations_fail_closed(self):
        record = self.db.lookup("Foretell Lifecycle Fixture")

        with patch(
            "quorune.compiler.keyword_nodes.fixed_cast_lifecycle_keyword_node",
            return_value=None,
        ):
            compiled = compile_oracle_card(
                record,
                capability_registry=self.capabilities,
                capability_profile="commander_review",
            )
        self.assertNotEqual("exact", compiled.status)

        registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json").read_text(
                encoding="utf-8"
            )
        )
        capability = next(
            value
            for value in registry_value["capabilities"]
            if value["id"] == "casting.lifecycle.fixed_zone_cast"
        )
        capability["status"] = "blocked"
        capability["blockers"] = ["focused mutation"]
        blocked = CapabilityRegistry(registry_value)
        compiled = compile_oracle_card(
            record,
            capability_registry=blocked,
            capability_profile="commander_review",
        )
        self.assertNotEqual("exact", compiled.status)
        self.assertTrue(compiled.material_residuals)


class FixedZoneCastLifecycleRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-zone-lifecycle.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                LIFECYCLE_FIXTURE,
            ],
            database,
        )
        cls.db = CardDatabase(database)
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
    def tearDownClass(cls) -> None:
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
        session,
        *,
        name: str,
        ref: str,
        seat: str = "A",
        zone: str = "hand",
        register: bool = True,
    ) -> CardInstance:
        engine = session.engine
        record = self.db.lookup(name)
        card = CardInstance(
            object_id=f"zone-lifecycle:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=(list(engine.seats) if zone != "hand" else [seat]),
            revealed_to=(list(engine.seats) if zone != "hand" else []),
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        if register:
            register_generated_programs(
                self.db,
                engine.semantics,
                (record,),
                trust_level="provisional",
                capability_registry=self.capabilities,
                capability_profile="commander_review",
                promote_exact_runtime_handlers=True,
                promote_exact_trigger_programs=True,
                promote_exact_effect_programs=True,
                promote_exact_capability_declarations=True,
            )
        return card

    @staticmethod
    def prepare_main(session, *, seat: str = "A", active: str = "A") -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = active
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine._grant_priority(seat)
        engine._issue_priority(seat)

    @staticmethod
    def action(engine, *, card: CardInstance, action: str) -> dict:
        return next(
            value
            for value in engine._priority_action_hints(card.owner)["actions"]
            if value.get("card") == card.ref and value.get("action") == action
        )

    @staticmethod
    def resolve_stack(session) -> None:
        for _ in range(20):
            if not session.engine.state.stack:
                return
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            if not result.ok:
                raise AssertionError(result.summary)
        raise AssertionError("Stack did not resolve")

    def test_foretell_private_staging_persists_without_current_ability_and_replays(self):
        session = self.session(70214301, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Foretell Lifecycle Fixture",
            ref="FORETELL",
        )
        engine.state.players["A"].mana_pool.update({"C": 4, "U": 1})
        self.prepare_main(session)
        stage = self.action(
            engine,
            card=card,
            action="stage_cast_lifecycle",
        )
        result = session.act("pilot:A", {"action_id": stage["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", card.zone)
        self.assertTrue(card.face_down)
        self.assertEqual(["A"], card.known_to)
        self.assertIn(FIXED_ZONE_CAST_DESIGNATION_FIELD, card.annotations)
        self.assertIn("Foretell Lifecycle Fixture", json.dumps(session.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(
                "Foretell Lifecycle Fixture",
                json.dumps(session.packet(principal, full=True)),
            )

        engine.state.turn_sequence += 1
        self.prepare_main(session)
        with patch.object(engine, "_effective_static_component_keys", return_value=()):
            cast = self.action(engine, card=card, action="cast")
        self.assertEqual(
            {"foretell"},
            {option["id"] for option in cast["cost_options"]},
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"action_id": cast["id"], "cost_option": "foretell", "pay": "auto"},
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack(session)
        self.assertEqual("battlefield", card.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "foretell-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_plot_uses_both_special_action_and_later_cast_timing(self):
        session = self.session(70217001)
        engine = session.engine
        card = self.add_card(
            session,
            name="Plot Lifecycle Fixture",
            ref="PLOT",
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "R": 1})
        self.prepare_main(session)
        stage = self.action(engine, card=card, action="stage_cast_lifecycle")
        result = session.act("pilot:A", {"action_id": stage["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(card.face_down)
        self.assertFalse(
            any(
                action.get("card") == card.ref and action.get("action") == "cast"
                for action in engine._priority_action_hints("A")["actions"]
            )
        )
        engine.state.turn_sequence += 1
        self.prepare_main(session, active="B")
        self.assertFalse(
            any(action.get("card") == card.ref for action in engine._priority_action_hints("A")["actions"])
        )
        self.prepare_main(session)
        cast = self.action(engine, card=card, action="cast")
        plot = next(option for option in cast["cost_options"] if option["id"] == "plot")
        self.assertEqual(0, sum(plot["requirements"].values()))

    def test_staging_timing_stale_offer_and_payment_fail_without_mutation(self):
        session = self.session(70214302)
        engine = session.engine
        foretell = self.add_card(
            session,
            name="Foretell Lifecycle Fixture",
            ref="FORETELL-ROLLBACK",
        )
        plot = self.add_card(
            session,
            name="Plot Lifecycle Fixture",
            ref="PLOT-TIMING",
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "R": 1})
        self.prepare_main(session, seat="A", active="B")
        self.assertIsNone(build_staged_cast_offer(engine, "A", foretell))
        self.assertIsNone(build_staged_cast_offer(engine, "A", plot))

        self.prepare_main(session)
        offer = build_staged_cast_offer(engine, "A", foretell)
        self.assertIsNotNone(offer)
        assert offer is not None
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(GameRuleError, "stale"):
            commit_staged_cast_lifecycle(
                engine,
                seat="A",
                response={
                    **offer.to_dict(),
                    "proposal_fingerprint": "0" * 64,
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

        engine.state.players["A"].mana_pool = {
            symbol: 0 for symbol in "WUBRGC"
        }
        self.assertIsNone(build_staged_cast_offer(engine, "A", foretell))
        engine.state.players["A"].mana_pool.update({"C": 2, "R": 1})
        engine.state.stack.append(
            StackItem(
                stack_id="plot-timing-stack",
                ref="S-PLOT-TIMING",
                kind="triggered_ability",
                controller="A",
                label="Plot timing blocker",
            )
        )
        self.assertIsNone(build_staged_cast_offer(engine, "A", plot))

    def test_escape_pays_fixed_other_card_set_and_revalidates_current_ability(self):
        session = self.session(70213801)
        engine = session.engine
        source = self.add_card(
            session,
            name="Escape Lifecycle Fixture",
            ref="ESCAPE",
            zone="graveyard",
        )
        first = self.add_card(session, name="Sol Ring", ref="ESCAPE-1", zone="graveyard", register=False)
        second = self.add_card(session, name="Sol Ring", ref="ESCAPE-2", zone="graveyard", register=False)
        engine.state.players["A"].mana_pool.update({"C": 2, "B": 1})
        self.prepare_main(session)
        cast = self.action(engine, card=source, action="cast")
        option = next(value for value in cast["cost_options"] if value["id"] == "escape")
        schema = option["choice_schema"]["exile_cards"]
        self.assertEqual(2, schema["count"])
        self.assertEqual({first.ref, second.ref}, set(schema["legal_refs"]))
        request = CastProposalRequest.from_submission(
            "A",
            {
                "card": source.ref,
                "from": "graveyard",
                "cost_option": "escape",
                "exile_cards": [first.ref, second.ref],
                "pay": "auto",
            },
        )
        proposal = build_cast_proposal(engine, request)
        before = authoritative_state_hash(engine.state)
        with patch(
            "quorune.compiled_cast_lifecycles.compiled_fixed_cast_lifecycle_specs",
            return_value=(),
        ):
            with self.assertRaisesRegex(CastProposalError, "contract changed"):
                commit_cast(
                    engine,
                    proposal,
                    request.response(),
                )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {
                "card": source.ref,
                "from": "graveyard",
                "cost_option": "escape",
                "exile_cards": [first.ref, second.ref],
                "pay": "auto",
            },
        )
        self.assertEqual("stack", source.zone)
        self.assertEqual("exile", first.zone)
        self.assertEqual("exile", second.zone)
        self.assertEqual(
            "escape",
            source.annotations[FIXED_CAST_LIFECYCLE_STACK_ANNOTATION]["kind"],
        )

    def test_escape_insufficient_and_duplicate_other_card_payments_fail_closed(self):
        session = self.session(70213802)
        engine = session.engine
        source = self.add_card(
            session,
            name="Escape Lifecycle Fixture",
            ref="ESCAPE-ROLLBACK",
            zone="graveyard",
        )
        first = self.add_card(
            session,
            name="Sol Ring",
            ref="ESCAPE-ONLY",
            zone="graveyard",
            register=False,
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "B": 1})
        self.prepare_main(session)
        self.assertFalse(
            any(
                action.get("card") == source.ref
                for action in engine._priority_action_hints("A")["actions"]
            )
        )
        second = self.add_card(
            session,
            name="Sol Ring",
            ref="ESCAPE-SECOND",
            zone="graveyard",
            register=False,
        )
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._cast(
                "A",
                {
                    "card": source.ref,
                    "from": "graveyard",
                    "cost_option": "escape",
                    "exile_cards": [first.ref, first.ref],
                    "pay": "auto",
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertEqual("graveyard", second.zone)

    def test_jump_start_discards_then_self_exiles_when_countered(self):
        session = self.session(70213301)
        engine = session.engine
        source = self.add_card(
            session,
            name="Jump-start Lifecycle Fixture",
            ref="JUMP",
            zone="graveyard",
        )
        discard = self.add_card(session, name="Sol Ring", ref="JUMP-DISCARD", register=False)
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        cast = self.action(engine, card=source, action="cast")
        option = next(value for value in cast["cost_options"] if value["id"] == "jump-start")
        self.assertIn(
            discard.ref,
            option["choice_schema"]["discard_cards"]["legal_refs"],
        )
        self.assertNotIn(
            source.ref,
            option["choice_schema"]["discard_cards"]["legal_refs"],
        )
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {
                "card": source.ref,
                "from": "graveyard",
                "cost_option": "jump-start",
                "discard_cards": [discard.ref],
                "pay": "auto",
            },
        )
        item = engine.state.stack[-1]
        self.assertEqual("graveyard", discard.zone)
        engine._counter_stack_item(item.ref, reason="fixture", countered_by="B")
        self.assertEqual("exile", source.zone)

    def test_jump_start_resolution_departure_replays_exactly(self):
        session = self.session(70213302)
        engine = session.engine
        source = self.add_card(
            session,
            name="Jump-start Lifecycle Fixture",
            ref="JUMP-REPLAY",
            zone="graveyard",
        )
        discard = self.add_card(
            session,
            name="Sol Ring",
            ref="JUMP-REPLAY-DISCARD",
            register=False,
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        cast = self.action(engine, card=source, action="cast")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {
                "action_id": cast["id"],
                "cost_option": "jump-start",
                "discard_cards": [discard.ref],
                "pay": "auto",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_stack(session)
        self.assertEqual("exile", source.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "jump-start-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_rebound_schedules_identity_pinned_optional_free_cast(self):
        session = self.session(70208801)
        engine = session.engine
        source = self.add_card(
            session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND",
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast("A", {"card": source.ref, "cost_option": "normal", "pay": "auto"})
        item = engine.state.stack[-1]
        self.assertEqual(
            "rebound",
            item.context[FIXED_CAST_LIFECYCLE_CONTEXT_FIELD]["kind"],
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("exile", source.zone)
        self.assertEqual(1, len(engine.state.delayed_triggers))

        triggers = collect_trigger_items(
            engine,
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertEqual([REBOUND_CAST_SEMANTIC_KEY], [value.semantic_key for value in triggers])
        enqueue_trigger_batch(engine, triggers)
        self.assertFalse(begin_pending_trigger_batch(engine))
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        decision = engine.state.pending_decision
        self.assertEqual("selection.exile_cast", decision.kind)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action": "choose", "choice": "cast"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", source.zone)
        rebound_cast = engine.state.stack[-1]
        self.assertNotIn("rebound_from_hand", rebound_cast.context)
        self.resolve_stack(session)
        self.assertEqual("graveyard", source.zone)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            game_dir = Path(temporary) / "rebound-replay"
            session.save(game_dir)
            replay = replay_record(game_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_rebound_competing_exile_choices_checkpoint_and_replay(self):
        for branch in ("rebound", "voidwalker"):
            with self.subTest(branch=branch):
                session = self.session(
                    70208803 if branch == "rebound" else 70208804
                )
                engine = session.engine
                source = self.add_card(
                    session,
                    name="Rebound Lifecycle Fixture",
                    ref=f"REBOUND-COMPETE-{branch.upper()}",
                )
                self.add_card(
                    session,
                    name="Dauthi Voidwalker",
                    ref=f"REBOUND-VOIDWALKER-{branch.upper()}",
                    seat="B",
                    zone="battlefield",
                )
                engine.state.players["A"].mana_pool.update(
                    {"C": 2, "U": 1}
                )
                self.prepare_main(session)
                cast = self.action(engine, card=source, action="cast")
                starting_hand = len(engine.state.players["A"].zones["hand"])
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                result = session.act(
                    "pilot:A",
                    {
                        "action_id": cast["id"],
                        "cost_option": "normal",
                        "pay": "auto",
                    },
                )
                self.assertTrue(result.ok, result.summary)
                for _ in range(4):
                    decision = engine.state.pending_decision
                    if decision is not None and decision.kind == "replacement.order":
                        break
                    principal = session.pending_principals()[0]
                    result = session.act(principal, {"action_id": "pass"})
                    self.assertTrue(result.ok, result.summary)
                decision = engine.state.pending_decision
                self.assertIsNotNone(decision)
                assert decision is not None
                self.assertEqual("replacement.order", decision.kind)
                self.assertEqual(["A"], decision.actors)
                projected = StateProjector(
                    self.db, engine.state
                )._decision("pilot:A")
                self.assertIsNotNone(projected)
                assert projected is not None
                options = projected["ctx"]["options"]
                self.assertEqual(2, len(options))
                selected = next(
                    option["id"]
                    for option in options
                    if (
                        option["id"].startswith("rule:rebound:")
                        if branch == "rebound"
                        else option["id"].startswith(
                            "replacement.zone.destination.v1:"
                        )
                    )
                )
                self.assertEqual("stack", source.zone)
                self.assertFalse(engine.state.delayed_triggers)
                self.assertEqual(
                    starting_hand,
                    len(engine.state.players["A"].zones["hand"]),
                )

                with tempfile.TemporaryDirectory() as temporary:
                    game_dir = Path(temporary) / f"rebound-{branch}-choice"
                    session.save(game_dir)
                    restarted = CommanderSession.load(self.db, game_dir)
                    loaded_source = restarted.engine.state.cards[
                        source.object_id
                    ]
                    loaded_decision = StateProjector(
                        self.db, restarted.engine.state
                    )._decision("pilot:A")
                    self.assertIsNotNone(loaded_decision)
                    assert loaded_decision is not None
                    self.assertIn(
                        selected,
                        {
                            option["id"]
                            for option in loaded_decision["ctx"]["options"]
                        },
                    )
                    hand_before_resume = len(
                        restarted.engine.state.players["A"].zones["hand"]
                    )
                    result = restarted.act(
                        "pilot:A",
                        {"action_id": "choose", "replacement": selected},
                    )
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("exile", loaded_source.zone)
                    self.assertFalse(restarted.engine.state.stack)
                    self.assertEqual(
                        hand_before_resume,
                        len(
                            restarted.engine.state.players["A"].zones["hand"]
                        ),
                    )
                    applied = [
                        event.details["effect_id"]
                        for event in restarted.engine.state.events
                        if event.code == "replacement.apply"
                        and event.details.get("object") == loaded_source.ref
                    ]
                    self.assertEqual([selected], applied)
                    if branch == "rebound":
                        self.assertEqual(
                            1, len(restarted.engine.state.delayed_triggers)
                        )
                        self.assertEqual(
                            loaded_source.logical_object_id,
                            restarted.engine.state.delayed_triggers[
                                0
                            ].source_logical_object_id,
                        )
                        self.assertNotIn("void", loaded_source.counters)
                    else:
                        self.assertFalse(
                            restarted.engine.state.delayed_triggers
                        )
                        self.assertEqual(1, loaded_source.counters["void"])
                    expected_hash = authoritative_state_hash(
                        restarted.engine.state
                    )
                    restarted.save(game_dir)
                    replay = replay_record(game_dir, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_rebound_competes_with_other_destination_without_scheduling(self):
        session = self.session(70208805)
        engine = session.engine
        source = self.add_card(
            session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND-COMPETE-HAND",
        )
        replacement_source = self.add_card(
            session,
            name="Sol Ring",
            ref="REBOUND-HAND-SOURCE",
            seat="B",
            zone="battlefield",
            register=False,
        )
        program = SemanticProgram(
            key="test:rebound-hand-replacement",
            label="Put an opponent's graveyard-bound card into its hand",
            oracle_id=replacement_source.oracle_id,
            active_zone="battlefield",
            event="zone.change",
            trust_level="provisional",
            handlers=[
                {
                    "handler_id": "replacement.zone.destination.v1",
                    "schema_version": 1,
                    "event": "zone.change",
                    "condition": {
                        "destination": "graveyard",
                        "object_kind": "card",
                        "owner_relation": "opponent",
                    },
                    "destination": "hand",
                    "counters": {},
                }
            ],
        )
        engine.semantics.put(program)
        ordinary_trust = engine.semantic_program_is_current_trusted
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {"card": source.ref, "cost_option": "normal", "pay": "auto"},
        )

        with patch.object(
            engine,
            "semantic_program_is_current_trusted",
            side_effect=lambda candidate: (
                candidate is program or ordinary_trust(candidate)
            ),
        ):
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
            decision = engine.state.pending_decision
            self.assertIsNotNone(decision)
            assert decision is not None
            self.assertEqual("replacement.order", decision.kind)
            projected = StateProjector(
                self.db, engine.state
            )._decision("pilot:A")
            self.assertIsNotNone(projected)
            assert projected is not None
            options = projected["ctx"]["options"]
            selected = next(
                option["id"]
                for option in options
                if option["id"].startswith(
                    "replacement.zone.destination.v1:"
                )
            )
            result = session.act(
                "pilot:A",
                {"action_id": "choose", "replacement": selected},
            )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", source.zone)
        self.assertFalse(engine.state.delayed_triggers)

    def test_countered_and_stale_rebound_cards_do_not_receive_a_cast(self):
        session = self.session(70208802)
        engine = session.engine
        countered = self.add_card(
            session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND-COUNTERED",
        )
        stale = self.add_card(
            session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND-STALE",
        )
        engine.state.players["A"].mana_pool.update({"C": 4, "U": 2})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast("A", {"card": countered.ref, "pay": "auto"})
        engine._counter_stack_item(
            engine.state.stack[-1].ref,
            reason="counter Rebound",
            countered_by="B",
        )
        self.assertEqual("graveyard", countered.zone)
        self.assertFalse(engine.state.delayed_triggers)

        engine._cast("A", {"card": stale.ref, "pay": "auto"})
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("exile", stale.zone)
        engine.move_card(stale.object_id, "graveyard", reason="stale fixture", log=False)
        engine.move_card(stale.object_id, "exile", reason="new incarnation", log=False)
        triggers = collect_trigger_items(
            engine,
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        enqueue_trigger_batch(engine, triggers)
        self.assertFalse(begin_pending_trigger_batch(engine))
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertIsNone(engine.state.pending_decision)
        self.assertEqual("exile", stale.zone)

    def test_all_targets_illegal_and_nonhand_rebound_do_not_apply(self):
        targeted_session = self.session(70208806)
        targeted_engine = targeted_session.engine
        targeted = self.add_card(
            targeted_session,
            name="Targeted Rebound Lifecycle Fixture",
            ref="REBOUND-ILLEGAL-TARGET",
        )
        target = self.add_card(
            targeted_session,
            name="Goblin Engineer",
            ref="REBOUND-TARGET",
            seat="B",
            zone="battlefield",
            register=False,
        )
        targeted_engine.state.players["A"].mana_pool.update(
            {"C": 1, "U": 1}
        )
        self.prepare_main(targeted_session)
        targeted_engine.permissions.invalidate_current()
        targeted_engine._cast(
            "A",
            {
                "card": targeted.ref,
                "cost_option": "normal",
                "targets": [target.ref],
                "pay": "auto",
            },
        )
        targeted_engine.move_card(
            target.object_id,
            "hand",
            reason="make every Rebound target illegal",
            log=False,
        )
        targeted_engine.state.priority_player = None
        targeted_engine._prepare_stack_resolution()
        self.assertEqual("graveyard", targeted.zone)
        self.assertFalse(targeted_engine.state.delayed_triggers)
        self.assertTrue(
            any(
                event.code == "target.illegal"
                for event in targeted_engine.state.events
            )
        )

        nonhand_session = self.session(70208807)
        nonhand_engine = nonhand_session.engine
        nonhand = self.add_card(
            nonhand_session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND-NONHAND",
            zone="graveyard",
        )
        nonhand.annotations["cast_from"] = ["graveyard"]
        nonhand_engine.state.players["A"].mana_pool.update(
            {"C": 2, "U": 1}
        )
        self.prepare_main(nonhand_session)
        nonhand_engine.permissions.invalidate_current()
        nonhand_engine._cast(
            "A",
            {
                "card": nonhand.ref,
                "from": "graveyard",
                "cost_option": "normal",
                "pay": "auto",
            },
        )
        self.assertNotIn(
            FIXED_CAST_LIFECYCLE_STACK_ANNOTATION,
            nonhand.annotations,
        )
        nonhand_engine.state.priority_player = None
        nonhand_engine._prepare_stack_resolution()
        self.assertEqual("graveyard", nonhand.zone)
        self.assertFalse(nonhand_engine.state.delayed_triggers)

    def test_declined_rebound_cast_leaves_the_exiled_incarnation(self):
        session = self.session(70208808)
        engine = session.engine
        source = self.add_card(
            session,
            name="Rebound Lifecycle Fixture",
            ref="REBOUND-DECLINE",
        )
        engine.state.players["A"].mana_pool.update({"C": 2, "U": 1})
        self.prepare_main(session)
        engine.permissions.invalidate_current()
        engine._cast(
            "A",
            {"card": source.ref, "cost_option": "normal", "pay": "auto"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        triggers = collect_trigger_items(
            engine,
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        enqueue_trigger_batch(engine, triggers)
        self.assertFalse(begin_pending_trigger_batch(engine))
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        result = session.act(
            "pilot:A",
            {"action": "choose", "choice": "decline"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("exile", source.zone)
        self.assertFalse(engine.state.stack)
        self.assertFalse(
            collect_trigger_items(
                engine,
                "step.begin",
                {"phase": "beginning", "step": "upkeep", "player": "A"},
            )
        )


if __name__ == "__main__":
    unittest.main()
