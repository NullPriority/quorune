from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase, CardRecord
from quorune.continuous_effect_state import commit_continuous_effect
from quorune.continuous_effects import (
    ContinuousEffect,
    ContinuousEffectDuration,
    ContinuousEffectOrigin,
    ContinuousObjectIdentity,
    ContinuousOperation,
    Layer,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.drawing.model import DiscardDrawnCardUnlessType
from quorune.errors import GameRuleError
from quorune.madness import (
    MADNESS_CAPABILITY_ID,
    MADNESS_CHOICE_OPERATION,
    MADNESS_DISCARD_CAPABILITY_ID,
    MADNESS_REPLACEMENT_TEMPLATE_ID,
    MADNESS_TRIGGER_TEMPLATE_ID,
)
from quorune.model import GameConfig, GameState
from quorune.object_predicate import ObjectQuerySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    DEFAULT_CAPABILITY_REGISTRY,
)
from quorune.session import CommanderSession
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.madness import (
    MadnessDiscardReplacementHandler,
    MadnessTriggerHandler,
)
from quorune.zone_trigger_events import (
    normalized_zone_trigger_events,
    ZoneChangeOccurrence,
    ZoneTransitionKind,
)
from scripts.build_test_database import build_fixture_database


FIXTURE = ROOT / "tests" / "fixtures" / "madness-cards.json"
PAIRING_FIXTURE = (
    ROOT / "tests" / "fixtures" / "commander-pairing-cards.json"
)


def current_capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry.from_path(DEFAULT_CAPABILITY_REGISTRY)
    registry.mark_evidence_verified("0" * 64)
    return registry


def card_record(text: str) -> CardRecord:
    return CardRecord(
        oracle_id="fixture:madness-compiler",
        name="Generic Madness Compiler Fixture",
        mana_cost="{3}{B}",
        mana_value=4,
        type_line="Sorcery",
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("B",),
        color_identity=("B",),
        keywords=("Madness",),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class MadnessCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = current_capabilities()

    def compile(self, text: str, *, registry=None):
        return compile_oracle_card(
            card_record(text),
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_mana_madness_compiles_typed_replacement_and_trigger(self):
        reminder = (
            " (If you discard this card, discard it into exile. When you do, "
            "cast it for its madness cost or put it into your graveyard.)"
        )
        for cost, suffix in (("{0}", reminder), ("{B}", ""), ("{2}{C}", reminder)):
            with self.subTest(cost=cost, reminder=bool(suffix)):
                text = f"Madness {cost}{suffix}"
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = ir.faces[0].nodes
                self.assertEqual(2, len(nodes))
                replacement = next(
                    node
                    for node in nodes
                    if node.template_id == MADNESS_REPLACEMENT_TEMPLATE_ID
                )
                trigger = next(
                    node
                    for node in nodes
                    if node.template_id == MADNESS_TRIGGER_TEMPLATE_ID
                )
                self.assertEqual("all", replacement.active_zone)
                self.assertEqual("zone.change", replacement.event)
                self.assertEqual("exile", trigger.active_zone)
                self.assertEqual("card.discarded.self", trigger.event)
                self.assertEqual(MADNESS_CHOICE_OPERATION, trigger.effects[0]["op"])
                self.assertEqual(replacement.span, trigger.span)
                self.assertEqual(text, text[replacement.span.start : replacement.span.end])
                self.assertIn(
                    MADNESS_DISCARD_CAPABILITY_ID,
                    replacement.capability_dependencies,
                )
                self.assertIn(
                    MADNESS_CAPABILITY_ID,
                    trigger.capability_dependencies,
                )
                self.assertTrue(replacement.handlers)
                self.assertTrue(trigger.handlers)

    def test_madness_variants_and_dependencies_fail_closed(self):
        variants = (
            "Madness {X}{B}",
            "Madness {W/B}",
            "Madness {B/P}",
            "Madness {S}",
            "Madness—Pay six {C}.",
            "Madness—{2}{B}, Pay 8 life.",
            "Madness {B} (Discard this into exile.)",
            "Madness {B}, draw a card.",
        )
        for text in variants:
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertFalse(
                    any(
                        node.template_id
                        in {
                            MADNESS_REPLACEMENT_TEMPLATE_ID,
                            MADNESS_TRIGGER_TEMPLATE_ID,
                        }
                        for node in ir.faces[0].nodes
                    )
                )

        value = json.loads(
            DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8")
        )
        for capability_id in (
            MADNESS_CAPABILITY_ID,
            MADNESS_DISCARD_CAPABILITY_ID,
            "trigger.event.normalized_zone_change",
            "trigger.placement.apnap",
            "zone.change.destination_replacement",
        ):
            with self.subTest(blocked=capability_id):
                mutated = json.loads(json.dumps(value))
                row = next(
                    item
                    for item in mutated["capabilities"]
                    if item["id"] == capability_id
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused Madness dependency mutation"]
                ir = self.compile(
                    "Madness {B}",
                    registry=CapabilityRegistry(mutated),
                )
                self.assertNotEqual("exact", ir.status)

    def test_madness_compiler_and_shape_mutations_fail_closed(self):
        exact = self.compile("Madness {B}")
        replacement = next(
            node
            for node in exact.faces[0].nodes
            if node.template_id == MADNESS_REPLACEMENT_TEMPLATE_ID
        )
        trigger = next(
            node
            for node in exact.faces[0].nodes
            if node.template_id == MADNESS_TRIGGER_TEMPLATE_ID
        )
        MadnessDiscardReplacementHandler().validate(replacement.handlers[0])
        MadnessTriggerHandler().validate(trigger.handlers[0])
        for handler, descriptor in (
            (MadnessDiscardReplacementHandler(), replacement.handlers[0]),
            (MadnessTriggerHandler(), trigger.handlers[0]),
        ):
            with self.subTest(handler=handler.handler_id):
                with self.assertRaises(SemanticNodeError):
                    handler.validate({**descriptor, "unknown": True})
                mutated = json.loads(json.dumps(descriptor))
                mutated["madness"]["oracle_line"] = (
                    "Madness {B} (use an unsupported continuation.)"
                )
                with self.assertRaisesRegex(
                    SemanticNodeError,
                    "outside the closed grammar",
                ):
                    handler.validate(mutated)

        with patch(
            "quorune.compiler.multi_keyword_nodes.madness_keyword_nodes",
            return_value=None,
        ):
            ir = self.compile("Madness {B}")
        self.assertNotEqual("exact", ir.status)
        self.assertFalse(
            any(
                node.template_id
                in {
                    MADNESS_REPLACEMENT_TEMPLATE_ID,
                    MADNESS_TRIGGER_TEMPLATE_ID,
                }
                for node in ir.faces[0].nodes
            )
        )


class MadnessRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "madness.sqlite3"
        build_fixture_database((PAIRING_FIXTURE, FIXTURE), database)
        cls.db = CardDatabase(database)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    @staticmethod
    def deck() -> DeckDefinition:
        return DeckDefinition(
            name="Typed Madness",
            entries=[
                DeckEntry("Thrasios, Triton Hero", board="commander"),
                DeckEntry("Generic Madness Life Fixture", quantity=8),
                DeckEntry("Generic Madness Creature Fixture", quantity=2),
                DeckEntry("Generic Discard Outlet Fixture"),
                DeckEntry("Generic Discard Cost Spell Fixture"),
                DeckEntry("Generic Simple Madness Aura Fixture"),
                DeckEntry("Generic Typed Madness Aura Fixture"),
                DeckEntry("Generic Targeted Madness Destroy Fixture"),
                DeckEntry("Generic Any Target Madness Fixture"),
            ],
            commanders=["Thrasios, Triton Hero"],
        )

    def session(self, seed: int, *, players: int = 4) -> CommanderSession:
        session = CommanderSession.create(
            self.db,
            {chr(ord("A") + index): self.deck() for index in range(players)},
            first_player="A",
            seed=seed,
            config=GameConfig(seed=seed, auto_pass_empty_priority=False),
        )
        while (
            session.state.pending_decision is not None
            and session.state.pending_decision.kind == "mulligan.declare"
        ):
            for principal in tuple(session.pending_principals()):
                result = session.act(principal, {"a": "keep"})
                self.assertTrue(result.ok, result.summary)
        session.engine.permissions.invalidate_current()
        session.engine.state.pending_decision = None
        session.engine.state.priority_player = None
        session.engine.state.priority_passes = []
        return session

    @staticmethod
    def card(session: CommanderSession, seat: str, name: str, *, zone=None):
        return next(
            card
            for card in session.state.cards.values()
            if card.owner == seat
            and card.printed_name == name
            and (zone is None or card.zone == zone)
        )

    def pass_until(self, session: CommanderSession, predicate, *, limit=24):
        for _ in range(limit):
            if predicate():
                return
            principals = session.pending_principals()
            self.assertTrue(principals)
            result = session.act(principals[0], {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Madness resolution did not reach the expected state")

    def begin_madness_choice(
        self,
        session: CommanderSession,
        *,
        seat: str = "A",
        mana: int = 1,
        card_name: str = "Generic Madness Life Fixture",
    ):
        engine = session.engine
        card = self.card(session, seat, card_name)
        if card.zone != "hand":
            engine.move_card(card.object_id, "hand", log=False)
        engine.state.players[seat].mana_pool["B"] = mana
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.move_card(
            card.object_id,
            "graveyard",
            reason="typed Madness discard",
            semantic_events=True,
            transition_kind=ZoneTransitionKind.DISCARD,
        )
        self.assertEqual("exile", card.zone)
        engine._stabilize()
        engine._prepare_stack_resolution()
        self.assertEqual("semantic.choice", engine.state.pending_decision.kind)
        return card

    def test_typed_discard_cause_covers_authoritative_producers_without_inference(self):
        session = self.session(702_035_001)
        engine = session.engine
        card = self.card(
            session, "A", "Generic Madness Life Fixture", zone="hand"
        )
        ordinary = replace(card)
        occurrence = ZoneChangeOccurrence(
            object_id=ordinary.object_id,
            card_ref=ordinary.ref,
            owner="A",
            origin="hand",
            destination="graveyard",
            previous_controller="A",
            current_controller="A",
            previous_logical_object_id=ordinary.logical_object_id,
            current_logical_object_id=f"{ordinary.object_id}@99",
            zone_change_counter=99,
            token=False,
            card_object=True,
            previous_characteristics={},
            current_characteristics={},
        )
        self.assertNotIn(
            "card.discarded",
            {event.kind for event in normalized_zone_trigger_events(occurrence)},
        )
        discarded = replace(
            occurrence,
            transition_kind=ZoneTransitionKind.DISCARD,
        )
        self.assertIn(
            "card.discarded",
            {event.kind for event in normalized_zone_trigger_events(discarded)},
        )
        with self.assertRaisesRegex(GameRuleError, "discard transition"):
            engine.move_card(
                card.object_id,
                "exile",
                transition_kind=ZoneTransitionKind.DISCARD,
            )

        engine.move_card(
            card.object_id,
            "graveyard",
            semantic_events=True,
        )
        self.assertEqual("graveyard", card.zone)
        self.assertFalse(engine.state.stack)

        effect_session = self.session(702_035_002)
        effect_card = self.card(
            effect_session,
            "A",
            "Generic Madness Life Fixture",
            zone="hand",
        )
        effect_session.engine.apply_effect(
            {"op": "discard", "card": effect_card.ref},
            actor="A",
        )
        self.assertEqual("exile", effect_card.zone)

    def test_madness_cast_and_decline_share_offer_commit_and_replay(self):
        session = self.session(702_035_003)
        card = self.begin_madness_choice(session)
        packet = session.packet("pilot:A", full=True)
        self.assertEqual(
            ["cast", "decline"],
            packet["decision"]["legal_actions"][0]["choice_schema"][
                "legal_values"
            ],
        )
        options = packet["decision"]["ctx"]["cast_options"]
        self.assertEqual(1, len(options))
        self.assertEqual("madness", options[0]["id"])
        self.assertEqual(1, options[0]["requirements"]["B"])
        life_before = session.state.players["A"].life
        session.initial_checkpoint = checkpoint_envelope(session.state)
        session.commands.clear()
        session.decisions.clear()
        chosen = session.act("pilot:A", {"action_id": "choose", "choice": "cast"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertTrue(session.state.stack)
        self.pass_until(session, lambda: not session.state.stack)
        self.assertEqual("graveyard", card.zone)
        self.assertEqual(life_before + 3, session.state.players["A"].life)
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "madness-cast"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

        declined = self.session(702_035_004)
        declined_card = self.begin_madness_choice(declined)
        result = declined.act(
            "pilot:A", {"action_id": "choose", "choice": "decline"}
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", declined_card.zone)
        self.assertFalse(declined.state.stack)

    def test_discard_cause_routes_cleanup_activation_casting_and_post_draw(self):
        activation = self.session(702_035_011)
        engine = activation.engine
        outlet = self.card(
            activation, "A", "Generic Discard Outlet Fixture", zone="hand"
        )
        paid = self.card(
            activation, "A", "Generic Madness Life Fixture", zone="hand"
        )
        engine.move_card(outlet.object_id, "battlefield", log=False)
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("source") == outlet.ref
        )
        activated = activation.act(
            "pilot:A",
            {"action_id": action["id"], "cost_cards": [paid.ref]},
        )
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual("exile", paid.zone)

        casting = self.session(702_035_012)
        engine = casting.engine
        spell = self.card(
            casting, "A", "Generic Discard Cost Spell Fixture"
        )
        if spell.zone != "hand":
            engine.move_card(spell.object_id, "hand", log=False)
        paid = self.card(
            casting, "A", "Generic Madness Life Fixture", zone="hand"
        )
        engine.state.players["A"].mana_pool.update({"B": 1, "C": 1})
        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        action = next(
            value
            for value in engine._priority_action_hints("A")["actions"]
            if value.get("card") == spell.ref
        )
        cost_schema = action["cost_options"][0]["choice_schema"][
            "discard_cards"
        ]
        self.assertIn(paid.ref, cost_schema["legal_refs"])
        cast = casting.act(
            "pilot:A",
            {"action_id": action["id"], "discard_cards": [paid.ref]},
        )
        self.assertTrue(cast.ok, cast.summary)
        self.assertEqual("exile", paid.zone)
        self.assertEqual("stack", spell.zone)

        cleanup = self.session(702_035_013)
        cleanup_card = self.card(
            cleanup, "A", "Generic Madness Life Fixture", zone="hand"
        )
        cleanup.state.players["A"].max_hand_size = (
            len(cleanup.state.players["A"].zones["hand"]) - 1
        )
        cleanup.engine._complete_cleanup_discard(
            SimpleNamespace(
                actors=("A",),
                responses={"A": {"cards": [cleanup_card.ref]}},
            )
        )
        self.assertEqual("exile", cleanup_card.zone)

        post_draw = self.session(702_035_014)
        drawn = self.card(
            post_draw, "A", "Generic Madness Life Fixture", zone="hand"
        )
        post_draw.engine.move_card(
            drawn.object_id,
            "library",
            position="top",
            log=False,
        )
        post_draw.engine._begin_draw_sequence(
            "A",
            1,
            reason="focused post-draw discard",
            post_draw_actions=(DiscardDrawnCardUnlessType("land"),),
        )
        self.assertEqual("exile", drawn.zone)

    def test_madness_stale_payment_counter_and_current_ability_boundaries(self):
        insufficient = self.session(702_035_005)
        card = self.begin_madness_choice(insufficient, mana=0)
        packet = insufficient.packet("pilot:A", full=True)
        self.assertEqual(
            ["decline"],
            packet["decision"]["legal_actions"][0]["choice_schema"][
                "legal_values"
            ],
        )
        rejected = insufficient.act(
            "pilot:A", {"action_id": "choose", "choice": "cast"}
        )
        self.assertFalse(rejected.ok)
        self.assertEqual("exile", card.zone)

        stale_cost = self.session(702_035_015)
        stale_cost_card = self.begin_madness_choice(stale_cost)
        stale_cost.state.players["A"].mana_pool["B"] = 0
        before = authoritative_state_hash(stale_cost.state)
        rejected = stale_cost.act(
            "pilot:A", {"action_id": "choose", "choice": "cast"}
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(stale_cost.state))
        self.assertEqual("exile", stale_cost_card.zone)

        stale = self.session(702_035_006)
        stale_card = self.begin_madness_choice(stale)
        stale.engine.move_card(stale_card.object_id, "hand", log=False)
        stale.engine.move_card(stale_card.object_id, "exile", log=False)
        before = authoritative_state_hash(stale.state)
        result = stale.act(
            "pilot:A", {"action_id": "choose", "choice": "cast"}
        )
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(stale.state))

        removed = self.session(702_035_007)
        removed_card = self.card(
            removed,
            "A",
            "Generic Madness Life Fixture",
            zone="hand",
        )
        commit_continuous_effect(
            removed.state,
            ContinuousEffect(
                effect_id="fixture:remove-madness",
                source_id="fixture:remove-madness-owner",
                layer=Layer.ABILITY,
                sublayer="6",
                timestamp=removed.engine._next_zone_timestamp(),
                operations=(ContinuousOperation("remove_all_abilities"),),
                origin=ContinuousEffectOrigin.RESOLUTION,
                duration=ContinuousEffectDuration.UNTIL_END_OF_TURN,
                applies=ObjectQuerySpec(zones=("hand",)),
                locked_objects=(
                    ContinuousObjectIdentity(
                        object_id=removed_card.object_id,
                        logical_object_id=removed_card.logical_object_id,
                    ),
                ),
            ),
        )
        removed.engine.move_card(
            removed_card.object_id,
            "graveyard",
            semantic_events=True,
            transition_kind=ZoneTransitionKind.DISCARD,
        )
        self.assertEqual("graveyard", removed_card.zone)
        self.assertFalse(removed.state.stack)

        countered = self.session(702_035_010)
        countered_card = self.begin_madness_choice(countered)
        cast = countered.act(
            "pilot:A", {"action_id": "choose", "choice": "cast"}
        )
        self.assertTrue(cast.ok, cast.summary)
        spell = next(
            item
            for item in countered.state.stack
            if item.card_object_id == countered_card.object_id
        )
        countered.engine._counter_stack_item(
            spell.ref,
            reason="focused Madness counter witness",
            countered_by="B",
        )
        self.assertEqual("graveyard", countered_card.zone)
        self.assertFalse(countered.state.pending_trigger_batches)

    def test_simultaneous_madness_triggers_batch_in_apnap_order(self):
        session = self.session(702_035_008)
        engine = session.engine
        first = self.card(
            session, "A", "Generic Madness Life Fixture", zone="hand"
        )
        second = self.card(
            session, "B", "Generic Madness Life Fixture", zone="hand"
        )
        engine._move_cards_simultaneously(
            [
                (first.object_id, "graveyard"),
                (second.object_id, "graveyard"),
            ],
            reason="simultaneous Madness discard",
            transition_kinds={
                first.object_id: ZoneTransitionKind.DISCARD,
                second.object_id: ZoneTransitionKind.DISCARD,
            },
        )
        self.assertEqual("exile", first.zone)
        self.assertEqual("exile", second.zone)
        self.assertEqual(1, len(engine.state.pending_trigger_batches))
        batch = engine.state.pending_trigger_batches[0]
        self.assertEqual(("A", "B", "C", "D"), batch.apnap_order)
        self.assertEqual(
            ["A", "B"],
            [item["controller"] for item in batch.items],
        )

    def test_four_player_madness_projection_and_save_load(self):
        session = self.session(702_035_009)
        card = self.begin_madness_choice(session)
        self.assertIn(
            card.ref,
            json.dumps(session.packet("pilot:B", full=True)),
        )
        self.assertIsNone(session.packet("pilot:B", full=True)["decision"])
        self.assertEqual(
            session.state.to_dict(),
            GameState.from_dict(session.state.to_dict()).to_dict(),
        )

    def test_madness_aura_casts_compose_with_typed_discard_and_replacement(self):
        simple = self.session(702_035_016)
        simple_target = self.card(
            simple,
            "A",
            "Generic Madness Creature Fixture",
        )
        simple.engine.move_card(
            simple_target.object_id,
            "battlefield",
            log=False,
        )
        simple_aura = self.begin_madness_choice(
            simple,
            card_name="Generic Simple Madness Aura Fixture",
        )
        simple_options = simple.packet("pilot:A", full=True)["decision"]["ctx"][
            "cast_options"
        ]
        self.assertIn(
            simple_target.ref,
            simple_options[0]["target_schema"]["legal_refs"],
        )
        result = simple.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "cast",
                "targets": [simple_target.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(simple, lambda: not simple.state.stack)
        self.assertEqual("battlefield", simple_aura.zone)
        self.assertEqual(simple_target.object_id, simple_aura.attached_to)

        typed = self.session(702_035_017)
        typed_target = self.card(
            typed,
            "A",
            "Generic Discard Outlet Fixture",
        )
        typed.engine.move_card(
            typed_target.object_id,
            "battlefield",
            log=False,
        )
        typed_aura = self.begin_madness_choice(
            typed,
            card_name="Generic Typed Madness Aura Fixture",
        )
        typed_options = typed.packet("pilot:A", full=True)["decision"]["ctx"][
            "cast_options"
        ]
        self.assertIn(
            typed_target.ref,
            typed_options[0]["target_schema"]["legal_refs"],
        )
        result = typed.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "cast",
                "targets": [typed_target.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(typed, lambda: not typed.state.stack)
        self.assertEqual("battlefield", typed_aura.zone)
        self.assertEqual(typed_target.object_id, typed_aura.attached_to)

    def test_targeted_madness_revalidates_characteristic_target_after_discard(self):
        session = self.session(702_035_018)
        target = self.card(
            session,
            "B",
            "Generic Madness Creature Fixture",
        )
        session.engine.move_card(target.object_id, "battlefield", log=False)
        spell = self.begin_madness_choice(
            session,
            card_name="Generic Targeted Madness Destroy Fixture",
        )
        options = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "cast_options"
        ]
        self.assertIn(target.ref, options[0]["target_schema"]["legal_refs"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "cast",
                "targets": [target.ref],
            },
        )
        self.assertTrue(result.ok, result.summary)
        cast_item = next(
            item
            for item in session.state.stack
            if item.card_object_id == spell.object_id
        )
        self.assertEqual([target.ref], cast_item.targets)
        session.engine.move_card(target.object_id, "hand", log=False)
        self.pass_until(session, lambda: not session.state.stack)
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", spell.zone)

    def test_any_target_madness_composes_with_typed_discard(self):
        session = self.session(702_035_019)
        life_before = session.state.players["B"].life
        spell = self.begin_madness_choice(
            session,
            card_name="Generic Any Target Madness Fixture",
        )
        options = session.packet("pilot:A", full=True)["decision"]["ctx"][
            "cast_options"
        ]
        self.assertIn("B", options[0]["target_schema"]["legal_refs"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "cast",
                "targets": ["B"],
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.pass_until(session, lambda: not session.state.stack)
        self.assertEqual(life_before - 2, session.state.players["B"].life)
        self.assertEqual("graveyard", spell.zone)


if __name__ == "__main__":
    unittest.main()
