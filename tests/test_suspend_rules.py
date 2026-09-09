from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.deck import DeckLoader
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.errors import GameRuleError
from quorune.haste import has_effective_haste
from quorune.model import CardInstance
from quorune.oracle_ir import (
    compile_oracle_card,
    register_generated_programs,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    load_default_capability_registry,
)
from quorune.session import CommanderSession
from quorune.suspend import (
    build_suspend_offer,
    commit_suspend,
    SUSPEND_CAST_SEMANTIC_KEY,
    suspend_upkeep_trigger_items,
)
from quorune.trigger_processing import (
    begin_pending_trigger_batch,
    collect_trigger_items,
    enqueue_trigger_batch,
)
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


EXPECTED_SUSPEND_BEHAVIOR = (
    {
        "authority": ("702.62a", "702.62c"),
        "minimal_state": (
            "the card is in its owner's hand and the current priority window "
            "would allow that face to begin being cast"
        ),
        "legal_options": (
            "one special action to pay the printed Suspend cost and exile "
            "the card; targets, the printed mana cost, and spell payment do "
            "not gate that action"
        ),
        "result": (
            "the action uses no stack and the new face-up exile incarnation "
            "receives the fixed number of time counters"
        ),
        "counterexample": (
            "sorcery timing still prevents the special action during another "
            "player's turn unless a current typed timing permission applies"
        ),
    },
    {
        "authority": ("702.62a", "702.62b"),
        "minimal_state": (
            "the owner's upkeep begins while the current exile incarnation "
            "has Suspend and at least one time counter"
        ),
        "legal_options": "the mandatory upkeep trigger is placed through APNAP",
        "result": "resolution removes exactly one time counter",
        "counterexample": (
            "countering the upkeep trigger removes no counter and the card "
            "can trigger again on its owner's next upkeep"
        ),
    },
    {
        "authority": ("702.62a", "702.62d"),
        "minimal_state": (
            "the last time counter is removed from the same exiled card "
            "incarnation by any represented counter-removal producer"
        ),
        "legal_options": (
            "the owner may cast it without paying its mana cost using current "
            "targets and mandatory additional costs, or decline"
        ),
        "result": (
            "a cast creature receives identity-pinned Haste until its "
            "controller changes"
        ),
        "counterexample": (
            "a countered last-counter trigger, a declined cast, an illegal "
            "target set, or a stale exile incarnation remains exiled with no "
            "time counters"
        ),
    },
)


def record(
    text: str,
    suffix: int,
    *,
    type_line: str = "Creature — Beast",
    mana_cost: str = "{4}{G}",
) -> CardRecord:
    return CardRecord(
        oracle_id=f"18000000-0000-4000-8000-{suffix:012d}",
        name=f"Suspend Compiler Fixture {suffix}",
        mana_cost=mana_cost,
        mana_value=5.0,
        type_line=type_line,
        oracle_text=text,
        power="5" if "Creature" in type_line else None,
        toughness="5" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("G",),
        color_identity=("G",),
        keywords=("Suspend",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "fixed-suspend.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "suspend-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


class SuspendCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()

    def compile(self, fixture: CardRecord):
        return compile_oracle_card(
            fixture,
            capability_registry=self.capabilities,
            capability_profile="commander_review",
        )

    def test_expected_suspend_contract_is_independent_of_implementation(self):
        self.assertEqual(3, len(EXPECTED_SUSPEND_BEHAVIOR))
        for expectation in EXPECTED_SUSPEND_BEHAVIOR:
            with self.subTest(authority=expectation["authority"]):
                self.assertTrue(expectation["minimal_state"])
                self.assertTrue(expectation["legal_options"])
                self.assertTrue(expectation["result"])
                self.assertTrue(expectation["counterexample"])

    def test_fixed_suspend_compiles_to_the_shared_cast_lifecycle(self):
        cases = (
            ("Suspend 3—{1}{U}", 3, "{1}{U}"),
            ("Suspend 10—{R}", 10, "{R}"),
            ("Suspend 3—{0}", 3, "{0}"),
            ("Suspend 1—{W}{U}{B}{R}{G}", 1, "{W}{U}{B}{R}{G}"),
        )
        for index, (text, count, cost) in enumerate(cases, start=1):
            with self.subTest(text=text):
                compiled = self.compile(record(text, index))
                self.assertEqual("exact", compiled.status, compiled.to_dict())
                node = compiled.faces[0].nodes[0]
                self.assertEqual("fixed-public-cast-lifecycle-v1", node.template_id)
                self.assertEqual("cast.cost", node.event)
                self.assertEqual("all", node.active_zone)
                self.assertIn(
                    "current_ability_fragment_required",
                    node.runtime_coverage,
                )
                descriptor = node.handlers[0]
                self.assertTrue(descriptor["requires_complete_card_program"])
                lifecycle = descriptor["lifecycle"]
                self.assertEqual("suspend", lifecycle["kind"])
                self.assertEqual(count, lifecycle["counter_count"])
                self.assertEqual(cost, lifecycle["cost_text"])

    def test_dynamic_modified_and_land_suspend_remain_residual(self):
        base = record("Suspend 3—{1}{U}", 100)
        cases = (
            replace(base, oracle_text="Suspend X—{X}{U}"),
            replace(base, oracle_text="Suspend 3—{W/U}"),
            replace(base, oracle_text="Suspend 3—{U/P}"),
            replace(base, oracle_text="Suspend 3—{S}"),
            replace(base, oracle_text="Suspend 0—{U}"),
            replace(base, oracle_text="Suspend 3"),
            replace(base, oracle_text="Suspend 3—Sacrifice a creature"),
            replace(base, oracle_text="Suspend 3—{U} only during your turn"),
            replace(
                base,
                oracle_text="Suspend 3—{U}\nSuspend 2—{R}",
            ),
            replace(base, type_line="Land", power=None, toughness=None),
        )
        for fixture in cases:
            with self.subTest(text=fixture.oracle_text, type_line=fixture.type_line):
                compiled = self.compile(fixture)
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

    def test_suspend_prefixed_card_name_does_not_reclassify_spell_text(self):
        fixture = replace(
            record("Suspend 1—{R}", 200, type_line="Instant"),
            name="Suspend Bolt Fixture",
            oracle_text=(
                "Suspend 1—{R}\n"
                "Suspend Bolt Fixture deals 2 damage to target creature."
            ),
            mana_cost="{5}{R}",
            mana_value=6.0,
            power=None,
            toughness=None,
            colors=("R",),
            color_identity=("R",),
        )
        compiled = self.compile(fixture)
        self.assertEqual("exact", compiled.status, compiled.to_dict())
        self.assertEqual(
            ("fixed-public-cast-lifecycle-v1", "damage-creature-v1"),
            tuple(node.template_id for node in compiled.faces[0].nodes),
        )

    def test_suspend_dependency_and_compiler_mutations_fail_closed(self):
        fixture = record("Suspend 3—{1}{U}", 201)
        for dependency_id in (
            "casting.permission.one_shot_exile_without_mana",
            "continuous.resolution.fixed_keyword_until_control_change",
            "counter.placement.quantity_replacement",
            "counter.removal.rule_generated",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
        ):
            raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
            dependency = next(
                row
                for row in raw["capabilities"]
                if row["id"] == dependency_id
            )
            dependency["status"] = "blocked"
            dependency["blockers"] = ["test mutation"]
            registry = CapabilityRegistry(raw)
            registry.mark_evidence_verified("0" * 64)
            with self.subTest(dependency=dependency_id):
                compiled = compile_oracle_card(
                    fixture,
                    capability_registry=registry,
                    capability_profile="commander_review",
                )
                self.assertNotEqual("exact", compiled.status)
                self.assertTrue(compiled.material_residuals)

        with patch(
            "quorune.compiler.keyword_nodes.fixed_cast_lifecycle_keyword_node",
            return_value=None,
        ):
            mutant = self.compile(fixture)
        self.assertNotEqual("exact", mutant.status)


class SuspendRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.capabilities = load_default_capability_registry()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = focused_database(cls.temporary.name)
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
    ) -> CardInstance:
        engine = session.engine
        card_record = self.db.lookup(name)
        self.assertIsNotNone(card_record, name)
        public = zone in {"battlefield", "graveyard", "exile", "command"}
        card = CardInstance(
            object_id=f"suspend:{ref}",
            ref=ref,
            oracle_id=card_record.oracle_id,
            printed_name=card_record.name,
            owner=seat,
            controller=seat,
            zone=zone,
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats) if public else [seat],
            revealed_to=list(engine.seats) if public else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db,
            engine.semantics,
            (card_record,),
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
    def prepare_priority(session, seat: str = "A", *, active: str = "A") -> None:
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.active_player = active
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = seat
        engine.state.priority_passes = []
        engine._grant_priority(seat)
        engine._issue_priority(seat)

    @staticmethod
    def suspend_action(session, card: CardInstance) -> dict:
        decision = session.packet(f"pilot:{card.owner}", full=True)["decision"]
        if decision is None:
            raise AssertionError("Suspend fixture has no priority decision")
        return next(
            action
            for action in decision["ctx"]["legal"]["actions"]
            if action.get("action") == "suspend"
            and action.get("card") == card.ref
        )

    def commit_suspend_action(self, session, card: CardInstance) -> None:
        action = self.suspend_action(session, card)
        result = session.act(
            f"pilot:{card.owner}",
            {"action_id": action["id"], "pay": "auto"},
        )
        self.assertTrue(result.ok, result.summary)

    def place_upkeep_trigger(self, session, card: CardInstance):
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        items = suspend_upkeep_trigger_items(
            engine,
            active_player=card.owner,
        )
        self.assertEqual(1, len(items))
        enqueue_trigger_batch(engine, items)
        self.assertFalse(begin_pending_trigger_batch(engine))
        self.assertIs(items[0], items[0])
        return engine.state.stack[-1]

    def resolve_upkeep_to_cast_choice(self, session, card: CardInstance) -> None:
        engine = session.engine
        upkeep = self.place_upkeep_trigger(session, card)
        engine._prepare_stack_resolution()
        self.assertNotIn(upkeep, engine.state.stack)
        cast_trigger = next(
            item
            for item in engine.state.stack
            if item.semantic_key == SUSPEND_CAST_SEMANTIC_KEY
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertIn(cast_trigger, engine.state.stack)

    def test_suspend_offer_commit_uses_timing_not_targets_or_printed_cost(self):
        session = self.session(7026201)
        engine = session.engine
        card = self.add_card(
            session,
            name="Suspend Bolt Fixture",
            ref="SUSPEND-BOLT",
        )
        engine.state.players["A"].mana_pool.update({"R": 1})
        self.prepare_priority(session, active="B")
        action = self.suspend_action(session, card)
        self.assertEqual({"GENERIC": 0, "R": 1, "W": 0, "U": 0, "B": 0, "G": 0, "C": 0}, action["requirements"])
        self.assertFalse(
            any(
                value.get("action") == "cast" and value.get("card") == card.ref
                for value in session.state.pending_decision.payload_by_actor["A"]["legal"]["actions"]
            )
        )
        stack_before = tuple(engine.state.stack)
        self.commit_suspend_action(session, card)
        self.assertEqual("exile", card.zone)
        self.assertEqual(1, card.counters["time"])
        self.assertEqual(stack_before, tuple(engine.state.stack))
        self.assertEqual(0, engine.state.players["A"].mana_pool["R"])

    def test_sorcery_timing_flash_partial_stale_and_unpayable_fail_closed(self):
        session = self.session(7026202)
        engine = session.engine
        ordinary = self.add_card(
            session,
            name="Suspend Creature Fixture",
            ref="TIMING-ORDINARY",
        )
        flash = self.add_card(
            session,
            name="Flash Suspend Creature Fixture",
            ref="TIMING-FLASH",
        )
        partial = self.add_card(
            session,
            name="Partial Suspend Fixture",
            ref="TIMING-PARTIAL",
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        self.prepare_priority(session, active="B")
        action_refs = {
            action.get("card")
            for action in session.state.pending_decision.payload_by_actor["A"]["legal"]["actions"]
            if action.get("action") == "suspend"
        }
        self.assertNotIn(ordinary.ref, action_refs)
        self.assertIn(flash.ref, action_refs)
        self.assertNotIn(partial.ref, action_refs)

        offer = build_suspend_offer(engine, "A", flash)
        self.assertIsNotNone(offer)
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(GameRuleError, "stale"):
            commit_suspend(
                engine,
                seat="A",
                response={
                    "card": flash.ref,
                    "ability": "ab2",
                    "proposal_fingerprint": "0" * 64,
                    "expiry_revision": engine.state.revision,
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

        engine.state.players["A"].mana_pool = {
            symbol: 0 for symbol in "WUBRGC"
        }
        before = authoritative_state_hash(engine.state)
        with self.assertRaisesRegex(GameRuleError, "cannot currently"):
            commit_suspend(
                engine,
                seat="A",
                response={"card": flash.ref, "ability": "ab2"},
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))

    def test_upkeep_counter_trigger_countering_and_stale_incarnation(self):
        session = self.session(7026203)
        engine = session.engine
        card = self.add_card(
            session,
            name="Suspend Creature Fixture",
            ref="UPKEEP-SUSPEND",
        )
        engine.state.players["A"].mana_pool.update({"U": 1})
        self.prepare_priority(session)
        self.commit_suspend_action(session, card)
        upkeep = self.place_upkeep_trigger(session, card)
        engine._counter_stack_item(
            upkeep.ref,
            reason="Suspend upkeep trigger countered fixture",
            countered_by="B",
        )
        self.assertEqual(1, card.counters["time"])

        self.resolve_upkeep_to_cast_choice(session, card)
        self.assertEqual(0, card.counters.get("time", 0))
        self.assertEqual("selection.exile_cast", engine.state.pending_decision.kind)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        cast_trigger = next(
            item for item in engine.state.stack if item.semantic_key == SUSPEND_CAST_SEMANTIC_KEY
        )
        old_identity = card.logical_object_id
        engine.move_card(card.object_id, "hand", log=False)
        engine.move_card(card.object_id, "exile", log=False)
        self.assertNotEqual(old_identity, card.logical_object_id)
        engine._prepare_stack_resolution()
        self.assertNotIn(cast_trigger, engine.state.stack)
        self.assertEqual("exile", card.zone)

    def test_suspend_cast_decline_success_haste_and_control_change(self):
        for seed, choice in ((7026204, "decline"), (7026205, "cast")):
            with self.subTest(choice=choice):
                session = self.session(seed)
                engine = session.engine
                card = self.add_card(
                    session,
                    name="Suspend Creature Fixture",
                    ref=f"CAST-{choice.upper()}",
                )
                engine.state.players["A"].mana_pool.update({"U": 1})
                self.prepare_priority(session)
                self.commit_suspend_action(session, card)
                self.resolve_upkeep_to_cast_choice(session, card)
                result = session.act(
                    "pilot:A",
                    {"action_id": choice},
                )
                self.assertTrue(result.ok, result.summary)
                if choice == "decline":
                    self.assertEqual("exile", card.zone)
                    self.assertFalse(card.counters)
                    continue
                self.assertEqual("stack", card.zone)
                spell = engine.state.stack[-1]
                engine.permissions.invalidate_current()
                engine.state.pending_decision = None
                engine.state.priority_player = None
                engine._begin_resolve_item(
                    spell,
                    [],
                    spell.default_destination,
                    note="Suspend creature resolves",
                )
                self.assertEqual("battlefield", card.zone)
                self.assertTrue(has_effective_haste(engine, card))
                engine.change_control(
                    card.object_id,
                    "A",
                    reason="No-op control assignment fixture",
                )
                self.assertTrue(has_effective_haste(engine, card))
                engine.change_control(
                    card.object_id,
                    "B",
                    reason="Suspend Haste duration fixture",
                )
                self.assertFalse(has_effective_haste(engine, card))

    def test_noncreature_suspend_spell_does_not_gain_latent_haste(self):
        session = self.session(7026210)
        engine = session.engine
        card = self.add_card(
            session,
            name="Suspend Artifact Fixture",
            ref="NONCREATURE-SUSPEND",
        )
        self.prepare_priority(session)
        self.commit_suspend_action(session, card)
        self.resolve_upkeep_to_cast_choice(session, card)
        result = session.act("pilot:A", {"action_id": "cast"})
        self.assertTrue(result.ok, result.summary)
        spell = engine.state.stack[-1]
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._begin_resolve_item(
            spell,
            [],
            spell.default_destination,
            note="Suspend noncreature permanent resolves",
        )
        self.assertEqual("battlefield", card.zone)
        self.assertFalse(has_effective_haste(engine, card))

    def test_targetless_final_cast_and_countered_cast_trigger_remain_exiled(self):
        for index, mode in enumerate(("countered", "no_target")):
            with self.subTest(mode=mode):
                session = self.session(7026206 + index)
                engine = session.engine
                card = self.add_card(
                    session,
                    name="Suspend Bolt Fixture",
                    ref=f"{mode.upper()}-SUSPEND",
                )
                engine.state.players["A"].mana_pool.update({"R": 1})
                self.prepare_priority(session, active="B")
                self.commit_suspend_action(session, card)
                upkeep = self.place_upkeep_trigger(session, card)
                engine._prepare_stack_resolution()
                self.assertNotIn(upkeep, engine.state.stack)
                cast_trigger = next(
                    item
                    for item in engine.state.stack
                    if item.semantic_key == SUSPEND_CAST_SEMANTIC_KEY
                )
                if mode == "countered":
                    engine._counter_stack_item(
                        cast_trigger.ref,
                        reason="Suspend cast trigger countered fixture",
                        countered_by="B",
                    )
                else:
                    engine.state.priority_player = None
                    engine._prepare_stack_resolution()
                    self.assertNotIn(cast_trigger, engine.state.stack)
                    self.assertIsNone(engine.state.pending_decision)
                self.assertEqual("exile", card.zone)
                self.assertFalse(card.counters)

    def test_current_ability_removal_and_restoration_gate_suspend_lifecycle(self):
        session = self.session(7026208)
        engine = session.engine
        card = self.add_card(
            session,
            name="Flash Suspend Creature Fixture",
            ref="REMOVED-SUSPEND",
        )
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        self.prepare_priority(session, active="B")
        effect = ContinuousEffect(
            effect_id="fixture:remove-suspend",
            source_id="fixture:ability-removal",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation("remove_all_abilities"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(
                ContinuousObjectIdentity(
                    object_id=card.object_id,
                    logical_object_id=card.logical_object_id,
                ),
            ),
        )
        commit_continuous_effect(engine.state, effect)
        self.assertIsNone(build_suspend_offer(engine, "A", card))
        engine.state.continuous_effects.clear()
        self.assertIsNotNone(build_suspend_offer(engine, "A", card))
        self.commit_suspend_action(session, card)

        exile_effect = ContinuousEffect(
            effect_id="fixture:remove-suspend-exile",
            source_id="fixture:ability-removal",
            layer=Layer.ABILITY,
            sublayer="6",
            timestamp=engine._next_zone_timestamp(),
            operations=(ContinuousOperation("remove_all_abilities"),),
            origin=ContinuousEffectOrigin.RESOLUTION,
            duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
            locked_objects=(
                ContinuousObjectIdentity(
                    object_id=card.object_id,
                    logical_object_id=card.logical_object_id,
                ),
            ),
        )
        commit_continuous_effect(engine.state, exile_effect)
        self.assertEqual(
            (),
            suspend_upkeep_trigger_items(engine, active_player="A"),
        )
        engine.state.continuous_effects.clear()
        self.assertEqual(
            1,
            len(suspend_upkeep_trigger_items(engine, active_player="A")),
        )

    def test_step_begin_collection_adds_suspend_upkeep_to_shared_batch(self):
        session = self.session(7026211)
        engine = session.engine
        card = self.add_card(
            session,
            name="Suspend Creature Fixture",
            ref="COLLECT-SUSPEND",
            zone="exile",
        )
        card.counters["time"] = 1
        context = {
            "phase": "beginning",
            "step": "upkeep",
            "player": "A",
            "previous_upkeep_timestamp": 0,
        }
        items = collect_trigger_items(engine, "step.begin", context)
        self.assertEqual(1, len(items))
        self.assertEqual("builtin:suspend-upkeep-counter", items[0].semantic_key)
        self.assertEqual(
            (),
            tuple(
                collect_trigger_items(
                    engine,
                    "step.begin",
                    {**context, "step": "draw"},
                )
            ),
        )

    def test_four_player_projection_save_load_and_exact_replay(self):
        session = self.session(7026207, players=4)
        engine = session.engine
        card = self.add_card(
            session,
            name="Suspend Creature Fixture",
            ref="REPLAY-SUSPEND",
        )
        engine.state.players["A"].mana_pool.update({"U": 1})
        self.prepare_priority(session)
        action = self.suspend_action(session, card)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act(
            "pilot:A",
            {"action_id": action["id"], "pay": "auto"},
        )
        self.assertTrue(result.ok, result.summary)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "pilot:D"):
            packet = json.dumps(session.packet(principal, full=True), sort_keys=True)
            self.assertNotIn(card.object_id, packet)
        expected_hash = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "suspend-record"
            session.save(record_dir)
            loaded = CommanderSession.load(self.db, record_dir)
            self.assertEqual(
                expected_hash,
                authoritative_state_hash(loaded.state),
            )
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_multiple_upkeep_triggers_use_controller_order_and_private_projection(self):
        session = self.session(7026209, players=4)
        engine = session.engine
        cards = tuple(
            self.add_card(
                session,
                name="Suspend Creature Fixture",
                ref=f"ORDERED-SUSPEND-{index}",
                zone="exile",
            )
            for index in (1, 2)
        )
        for card in cards:
            card.counters["time"] = 1
        items = suspend_upkeep_trigger_items(
            engine,
            active_player="A",
        )
        self.assertEqual(2, len(items))
        enqueue_trigger_batch(engine, items)
        self.assertTrue(begin_pending_trigger_batch(engine))
        self.assertEqual(["pilot:A"], session.pending_principals())
        self.assertTrue(
            all(
                session.packet(f"pilot:{seat}", full=True)["decision"] is None
                for seat in ("B", "C", "D")
            )
        )
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual(
            {item.ref for item in items},
            {row["id"] for row in decision["ctx"]["triggers"]},
        )


if __name__ == "__main__":
    unittest.main()
