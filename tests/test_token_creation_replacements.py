from __future__ import annotations

from pathlib import Path
import json
import tempfile
import unittest

from common import keep_all, load_assets, make_session
from quorune.card_overrides import normalize_game_record_v3_runtime_handler
from quorune.model import CardInstance, StackItem
from quorune.compiler.token_templates import (
    static_additional_token_replacement_handler,
)
from quorune.oracle_ir import register_generated_programs
from quorune.projection import StateProjector
from quorune.replacement_effects import (
    CreateAdditionalToken,
    ReplacementChoiceRequired,
    replacement_choice_payload,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.semantic_runtime import (
    AdditionalTokenReplacementHandler,
    GenericAdditionalTokenReplacementHandler,
    SemanticNodeError,
    TokenCreationReplacementContext,
    default_token_creation_replacement_registry,
    resolve_token_creation_replacements,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticProgram, SemanticRegistry


def additional_token_descriptor(
    *, name: str = "Map", created_type: str = "artifact"
) -> dict:
    return {
        "handler_id": "replacement.token.additional.v1",
        "schema_version": 2,
        "event": "token.create",
        "condition": {
            "event_controller": "source_controller",
            "created_types_all": [created_type],
        },
        "quantity": 1,
        "token": {
            "name": name,
            "type_line": f"Token Artifact — {name}",
        },
    }


class TokenCreationReplacementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
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

    @staticmethod
    def card(engine, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A" and card.printed_name == name
        )

    def install_generated_worldwalker(self, engine):
        record = self.db.lookup("Worldwalker Helm")
        for program in engine.semantics.programs_for_oracle(record.oracle_id):
            if program.event == "token.create":
                engine.semantics.remove(program.key)
        result = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertEqual(1, result["runtime_handlers_promoted"])
        source = self.card(engine, "Worldwalker Helm")
        source.printed_name = "Anonymous replacement source"
        engine.move_card(source.object_id, "battlefield", controller="A")
        return source

    def install_generated_doubling_season(self, engine, *, ref: str):
        record = self.db.lookup("Doubling Season")
        source = CardInstance(
            object_id=f"fixture:{ref}",
            ref=ref,
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.event_sequence + 1,
            acquired_control_turn_count=-1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[source.object_id] = source
        engine.state.players["A"].zones["battlefield"].append(source.object_id)
        result = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review",
            promote_exact_runtime_handlers=True,
        )
        self.assertGreaterEqual(result["runtime_handlers_promoted"], 1)
        return source

    def test_token_multiplier_preserves_specifications_and_simultaneous_commit(self):
        session = self.session(1250590)
        engine = session.engine
        self.install_generated_doubling_season(
            engine, ref="token-multiplier-source"
        )

        created = engine.create_token(
            "A",
            name="Treasure",
            quantity=2,
            tapped=True,
            characteristics={"type_line": "Token Artifact — Treasure"},
            temporary_keywords=("Haste",),
            reason="token multiplier specification witness",
        )
        cards = [engine._resolve_object("A", ref) for ref in created]

        self.assertEqual(4, len(cards))
        self.assertEqual(1, len({card.zone_timestamp for card in cards}))
        self.assertTrue(all(card.tapped for card in cards))
        self.assertTrue(
            all("Haste" in card.temporary_keywords for card in cards)
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(
            ["replacement.token.quantity.v1"],
            [
                component["handler_id"]
                for component in event.details["replacement_components"]
            ],
        )

    def test_registered_additional_token_components_replace_without_name_dispatch(
        self,
    ):
        session = self.session(1250501)
        engine = session.engine
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")
        automaton.printed_name = "Renamed replacement source"

        created = engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
            reason="typed replacement characterization",
        )

        self.assertEqual(
            {"Treasure", "Thopter"},
            {engine._resolve_object("A", ref).printed_name for ref in created},
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(
            [
                {
                    "handler_id": "replacement.token.additional.v2",
                    "source": automaton.ref,
                    "quantity": 1,
                }
            ],
            event.details["replacement_components"],
        )
        self.assertEqual(
            "token.creation.additional_replacement",
            default_token_creation_replacement_registry().inventory()[0][
                "capability_dependencies"
            ][0],
        )

    def test_generic_compiler_program_drives_runtime_without_name_dispatch(self):
        session = self.session(12505011)
        engine = session.engine
        self.install_generated_worldwalker(engine)

        created = engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
            reason="generic compiler replacement characterization",
        )

        self.assertEqual(2, len(created))
        self.assertEqual(
            ["Map", "Treasure"],
            sorted(
                engine._resolve_object("A", ref).printed_name
                for ref in created
            ),
        )
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(
            "replacement.token.additional.v2",
            event.details["replacement_components"][0]["handler_id"],
        )

    def test_typed_token_display_text_is_not_runtime_oracle_authority(self):
        session = self.session(12505012)
        engine = session.engine
        source = self.install_generated_worldwalker(engine)

        created = engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
            reason="typed display boundary characterization",
        )
        map_card = next(
            engine._resolve_object("A", ref)
            for ref in created
            if engine._resolve_object("A", ref).printed_name == "Map"
        )
        stored = map_card.annotations["token_characteristics"]
        self.assertIn("display_text", stored)
        self.assertNotIn("oracle_text", stored)
        effective = engine._effective_card_data(map_card)
        self.assertEqual("", effective["oracle_text"])
        self.assertEqual(
            stored["display_text"], effective["display_oracle_text"]
        )
        projected = session.projector._effective(map_card)
        self.assertEqual(stored["display_text"], projected["o"])
        self.assertEqual(1, len(effective["activated_abilities"]))

        engine.move_card(
            source.object_id,
            "graveyard",
            controller="A",
        )
        copied_ref = engine.create_token(
            "A",
            name="",
            copy_of=map_card.ref,
            reason="typed display copy characterization",
        )[0]
        copied = engine._resolve_object("A", copied_ref)
        copied_effective = engine._effective_card_data(copied)
        self.assertEqual("", copied_effective["oracle_text"])
        self.assertEqual(
            stored["display_text"],
            copied_effective["display_oracle_text"],
        )
        self.assertEqual(1, len(copied_effective["activated_abilities"]))

    def test_additional_token_component_rejects_malformed_or_nonmatching_events(
        self,
    ):
        handler = AdditionalTokenReplacementHandler()
        malformed = additional_token_descriptor()
        malformed["quantity"] = 0
        with self.assertRaisesRegex(SemanticNodeError, "quantity"):
            handler.validate(malformed)

        obsolete_text = additional_token_descriptor()
        obsolete_text["token"]["oracle_text"] = "Display only"
        with self.assertRaisesRegex(SemanticNodeError, "unknown fields"):
            handler.validate(obsolete_text)

        malformed_display = additional_token_descriptor()
        malformed_display["token"]["display_text"] = ["not", "text"]
        with self.assertRaisesRegex(SemanticNodeError, "display_text"):
            handler.validate(malformed_display)

        descriptor = additional_token_descriptor()
        creature_context = TokenCreationReplacementContext(
            source_ref="P1",
            source_controller="A",
            event_controller="A",
            created_types=("creature",),
        )
        opponent_context = TokenCreationReplacementContext(
            source_ref="P1",
            source_controller="A",
            event_controller="B",
            created_types=("artifact",),
        )
        self.assertEqual((), handler.lower(descriptor, creature_context))
        self.assertEqual((), handler.lower(descriptor, opponent_context))
        matching = TokenCreationReplacementContext(
            source_ref="P1",
            source_controller="A",
            event_controller="A",
            created_types=("artifact",),
        )
        self.assertIsInstance(
            handler.replacement_effect(descriptor, matching).operations[0],
            CreateAdditionalToken,
        )
        with self.assertRaisesRegex(SemanticNodeError, "Unknown runtime handler"):
            SemanticProgram(
                key="test:unknown-runtime-handler",
                label="Unknown runtime handler",
                handlers=[
                    {
                        **descriptor,
                        "handler_id": "replacement.token.unknown.v1",
                    }
                ],
            )

    def test_historical_token_display_field_has_one_explicit_adapter(self):
        descriptor = additional_token_descriptor()
        descriptor["schema_version"] = 1
        descriptor["token"]["oracle_text"] = "Historical display text"

        normalized = normalize_game_record_v3_runtime_handler(descriptor)

        self.assertNotIn("oracle_text", normalized["token"])
        self.assertEqual(2, normalized["schema_version"])
        self.assertEqual(
            "Historical display text", normalized["token"]["display_text"]
        )
        self.assertNotIn("display_text", descriptor["token"])
        self.assertEqual(
            "Historical display text", descriptor["token"]["oracle_text"]
        )

    def test_generic_filter_and_context_types_are_canonical_and_unique(self):
        handler = GenericAdditionalTokenReplacementHandler()
        descriptor = static_additional_token_replacement_handler(
            "If you would create one or more artifact tokens, instead create "
            "those tokens plus an additional Map token."
        )[1]
        context = TokenCreationReplacementContext(
            source_ref="P1",
            source_controller="A",
            event_controller="A",
            created_types=("Creature", "Artifact"),
            created_subtypes=("Cat",),
        )
        self.assertEqual(("artifact", "creature"), context.created_types)
        self.assertEqual(("cat",), context.created_subtypes)
        self.assertEqual(
            "artifact",
            handler.validate(descriptor).created_types_all[0],
        )

        with self.assertRaisesRegex(SemanticNodeError, "must be unique"):
            TokenCreationReplacementContext(
                source_ref="P1",
                source_controller="A",
                event_controller="A",
                created_types=("Artifact", "artifact"),
            )

        duplicate_filter = {
            **descriptor,
            "condition": {
                **descriptor["condition"],
                "created_types_all": ["Artifact", "artifact"],
            },
        }
        with self.assertRaisesRegex(SemanticNodeError, "must be unique"):
            handler.validate(duplicate_filter)

    def test_multiple_additional_token_components_share_one_creation_timestamp(
        self,
    ):
        session = self.session(1250502)
        engine = session.engine
        for name in ("Stridehangar Automaton", "Worldwalker Helm"):
            source = self.card(engine, name)
            engine.move_card(source.object_id, "battlefield", controller="A")

        with self.assertRaises(ReplacementChoiceRequired) as required:
            engine.create_token(
                "A",
                name="Treasure",
                quantity=2,
                characteristics={"type_line": "Token Artifact — Treasure"},
                reason="simultaneous replacement characterization",
            )
        pending = required.exception.pending
        self.assertEqual("A", pending.choice.chooser)
        self.assertEqual(2, len(pending.choice.options))
        created = engine.create_token(
            "A",
            name="Treasure",
            quantity=2,
            characteristics={"type_line": "Token Artifact — Treasure"},
            reason="simultaneous replacement characterization",
            replacement_selections=(pending.choice.options[0],),
        )
        cards = [engine._resolve_object("A", ref) for ref in created]

        self.assertEqual(4, len(cards))
        self.assertEqual(
            {"Treasure": 2, "Thopter": 1, "Map": 1},
            {
                name: sum(card.printed_name == name for card in cards)
                for name in ("Treasure", "Thopter", "Map")
            },
        )
        self.assertEqual(1, len({card.zone_timestamp for card in cards}))
        event = next(
            event
            for event in reversed(engine.state.events)
            if event.code == "token.create"
        )
        self.assertEqual(2, len(event.details["replacement_order"]))

    def test_replacement_created_token_rediscovery_uses_same_event(self):
        handler = GenericAdditionalTokenReplacementHandler()
        any_descriptor = static_additional_token_replacement_handler(
            "If you would create one or more tokens, instead create those "
            "tokens plus an additional Food token."
        )[1]
        artifact_descriptor = static_additional_token_replacement_handler(
            "If you would create one or more artifact tokens, instead create "
            "those tokens plus an additional Map token."
        )[1]
        context = TokenCreationReplacementContext(
            source_ref="A-any",
            source_controller="A",
            event_controller="A",
            created_types=("creature",),
            created_subtypes=("cat",),
            component_id="any",
        )
        effects = (
            handler.replacement_effect(any_descriptor, context),
            handler.replacement_effect(
                artifact_descriptor,
                TokenCreationReplacementContext(
                    source_ref="A-artifact",
                    source_controller="A",
                    event_controller="A",
                    created_types=("creature",),
                    created_subtypes=("cat",),
                    component_id="artifact",
                ),
            ),
        )

        resolution = resolve_token_creation_replacements(
            event_id="token:create:rediscovery",
            controller="A",
            tokens=(
                {
                    "name": "Cat",
                    "quantity": 1,
                    "characteristics": {
                        "type_line": "Token Creature — Cat"
                    },
                },
            ),
            created_types=("creature",),
            created_subtypes=("cat",),
            effects=effects,
            apnap_order=("A", "B", "C", "D"),
        )

        self.assertIsNone(resolution.pending)
        self.assertEqual(
            ["Cat", "Food", "Map"],
            [token["name"] for token in resolution.tokens],
        )
        self.assertEqual(2, len(resolution.journal))
        self.assertEqual(
            ["artifact", "creature"],
            list(resolution.event.payload["created_types"]),
        )
        self.assertEqual(
            ["cat", "food", "map"],
            list(resolution.event.payload["created_subtypes"]),
        )

    def test_additional_token_does_not_inherit_original_entry_modifiers(self):
        session = self.session(12505021)
        engine = session.engine
        helm = self.card(engine, "Worldwalker Helm")
        engine.move_card(helm.object_id, "battlefield", controller="A")

        created = engine.create_token(
            "A",
            name="Treasure",
            tapped=True,
            characteristics={"type_line": "Token Artifact — Treasure"},
            temporary_keywords=("Haste",),
            reason="replacement token modifier isolation",
        )
        resolved = [engine._resolve_object("A", ref) for ref in created]
        cards = {card.printed_name: card for card in resolved}

        self.assertTrue(cards["Treasure"].tapped)
        self.assertIn("Haste", cards["Treasure"].temporary_keywords)
        self.assertFalse(cards["Map"].tapped)
        self.assertNotIn("Haste", cards["Map"].temporary_keywords)

    def test_source_created_by_event_does_not_replace_its_own_creation(self):
        session = self.session(1250505)
        engine = session.engine
        automaton = self.card(engine, "Stridehangar Automaton")
        engine.move_card(automaton.object_id, "battlefield", controller="A")

        created = engine.create_token(
            "A",
            name="Stridehangar Automaton",
            copy_of=automaton.ref,
            reason="replacement source timing characterization",
        )

        self.assertEqual(2, len(created))
        self.assertEqual(
            {"Stridehangar Automaton": 1, "Thopter": 1},
            {
                name: sum(
                    engine._resolve_object("A", ref).printed_name == name
                    for ref in created
                )
                for name in ("Stridehangar Automaton", "Thopter")
            },
        )

    def test_unnamed_copy_token_keeps_the_copied_objects_name(self):
        session = self.session(12505051)
        engine = session.engine
        original = self.card(engine, "Stridehangar Automaton")
        engine.move_card(original.object_id, "battlefield", controller="A")

        created = engine.create_token(
            "A",
            name="",
            copy_of=original.ref,
            reason="copy-name regression",
        )

        copy_token = engine._resolve_object("A", created[0])
        self.assertEqual("Stridehangar Automaton", copy_token.printed_name)
        self.assertEqual(
            "Stridehangar Automaton",
            engine.display_name(copy_token.object_id),
        )

    def test_resolution_suspends_for_seat_scoped_replacement_order(self):
        session = self.session(1250506)
        engine = session.engine
        for name in ("Stridehangar Automaton", "Worldwalker Helm"):
            source = self.card(engine, name)
            engine.move_card(source.object_id, "battlefield", controller="A")
        program = SemanticProgram(
            key="test:replacement-order-token-effect",
            label="Create an artifact token with competing replacements",
            effects=[
                {
                    "op": "create_token",
                    "controller": "A",
                    "name": "Treasure",
                    "characteristics": {
                        "type_line": "Token Artifact — Treasure"
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        item = StackItem(
            stack_id="replacement-order-token-effect",
            ref="S-replacement-order-token-effect",
            kind="triggered_ability",
            controller="A",
            label=program.label,
            semantic_key=program.key,
            visibility=["A", "B"],
        )
        engine.state.stack.append(item)

        engine._continue_resolution(
            stack_ref=item.ref,
            effects=[dict(value) for value in program.effects],
            destination=None,
            note="",
        )

        decision = engine.state.pending_decision
        self.assertIsNotNone(decision)
        self.assertEqual("replacement.order", decision.kind)
        self.assertEqual(["A"], decision.actors)
        projector = StateProjector(self.db, engine.state)
        projected_a = projector._decision("pilot:A")
        projected_b = projector._decision("pilot:B")
        self.assertIsNone(projected_b)
        self.assertEqual(2, len(projected_a["ctx"]["options"]))
        self.assertNotIn("replacement_batch", json.dumps(projected_a))
        self.assertNotIn("replacement_effects", json.dumps(projected_a))
        self.assertEqual(
            {"chooser", "prompt", "options", "legal_actions"},
            set(projected_a["ctx"]),
        )
        selected = projected_a["ctx"]["options"][0]["id"]
        capability = engine.permissions.capability_for("pilot:A")
        before_rejection = authoritative_state_hash(engine.state)
        rejected = engine.try_submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": "unknown-replacement"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(
            before_rejection, authoritative_state_hash(engine.state)
        )
        capability = engine.permissions.capability_for("pilot:A")
        result = engine.submit(
            token=capability.token,
            principal="pilot:A",
            action="choose",
            payload={"replacement": selected},
        )

        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Treasure", "Thopter", "Map"},
            {
                card.printed_name
                for card in engine.state.cards.values()
                if card.is_token and card.zone == "battlefield"
            },
        )
        self.assertFalse(
            any(candidate.ref == item.ref for candidate in engine.state.stack)
        )

    def test_four_player_replacement_choice_is_affected_seat_scoped(self):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=12505061,
            auto_pass_empty=False,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        sources = [
            card
            for card in engine.state.cards.values()
            if card.owner == "C"
            and card.printed_name in {
                "Stridehangar Automaton",
                "Worldwalker Helm",
            }
        ]
        self.assertEqual(2, len(sources))
        for source in sources:
            engine.move_card(source.object_id, "battlefield", controller="C")

        before = authoritative_state_hash(engine.state)
        with self.assertRaises(ReplacementChoiceRequired) as required:
            engine.create_token(
                "C",
                name="Treasure",
                characteristics={"type_line": "Token Artifact — Treasure"},
                reason="four-player replacement order characterization",
            )
        self.assertEqual("C", required.exception.pending.choice.chooser)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        # The pure creation call suspends before the engine installs a task;
        # the replacement payload itself still contains no private state.
        payload = replacement_choice_payload(
            required.exception.pending,
            required.exception.effects,
        )
        self.assertNotIn("hand", json.dumps(payload))
        self.assertNotIn("library", json.dumps(payload))
        self.assertEqual("C", payload["chooser"])

    def test_additional_token_component_replays_exactly(self):
        session = self.session(1250503)
        engine = session.engine
        for name in ("Stridehangar Automaton", "Worldwalker Helm"):
            source = self.card(engine, name)
            engine.move_card(source.object_id, "battlefield", controller="A")
        program = SemanticProgram(
            key="test:artifact-token-effect",
            label="Create an artifact token",
            effects=[
                {
                    "op": "create_token",
                    "controller": "A",
                    "name": "Treasure",
                    "characteristics": {
                        "type_line": "Token Artifact — Treasure"
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="artifact-token-effect",
                ref="S-artifact-token-effect",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "replacement.order", engine.state.pending_decision.kind
        )
        projected = StateProjector(self.db, engine.state)._decision("pilot:A")
        selected = projected["ctx"]["options"][0]["id"]
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choices": {"replacement": selected},
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Treasure", "Thopter", "Map"},
            {
                card.printed_name
                for card in engine.state.cards.values()
                if card.is_token and card.zone == "battlefield"
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "additional-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(3, replay["commands"])

    def test_generic_additional_token_program_replays_exactly(self):
        session = self.session(12505031)
        engine = session.engine
        self.install_generated_worldwalker(engine)
        program = SemanticProgram(
            key="test:generic-treasure-token-effect",
            label="Create a Treasure token",
            effects=[
                {
                    "op": "create_token",
                    "controller": "A",
                    "name": "Treasure",
                    "characteristics": {
                        "type_line": "Token Artifact — Treasure"
                    },
                }
            ],
            trust_level="provisional",
        )
        engine.semantics.put(program)
        engine.state.stack.append(
            StackItem(
                stack_id="generic-treasure-token-effect",
                ref="S-generic-treasure-token-effect",
                kind="triggered_ability",
                controller="A",
                label=program.label,
                semantic_key=program.key,
                visibility=["A", "B"],
            )
        )
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for principal in ("pilot:A", "pilot:B"):
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            {"Map": 1, "Treasure": 1},
            {
                name: sum(
                    card.is_token
                    and card.zone == "battlefield"
                    and card.printed_name == name
                    for card in engine.state.cards.values()
                )
                for name in ("Map", "Treasure")
            },
        )

        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / "generic-additional-token-record"
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(2, replay["commands"])

    def test_complete_legacy_registry_uses_pinned_compatibility_component(self):
        with tempfile.TemporaryDirectory() as temporary:
            semantics_path = Path(temporary) / "semantics.json"
            semantics_path.write_text(
                json.dumps(
                    {
                        "schema_version": 3,
                        "include_builtin_packs": False,
                        "programs": {},
                    }
                ),
                encoding="utf-8",
            )
            registry = SemanticRegistry(semantics_path)

        oracle_id = "9070c98b-fd01-4eeb-a4ec-fc464946c7c0"
        self.assertEqual([], registry.programs_for_oracle(oracle_id))
        programs = registry.runtime_handler_programs_for_oracle(
            oracle_id,
            active_zone="battlefield",
            event="token.create",
        )
        self.assertEqual(
            [f"{oracle_id}:replacement:additional-thopter"],
            [program.key for program in programs],
        )
        self.assertTrue(
            registry.is_runtime_handler_compatibility_program(programs[0])
        )
        self.assertEqual(
            "replacement.token.additional.v2",
            programs[0].handlers[0]["handler_id"],
        )

        session = self.session(1250504)
        session.engine.semantics = registry
        session.engine._semantic_trust_cache.clear()
        automaton = self.card(session.engine, "Stridehangar Automaton")
        session.engine.move_card(
            automaton.object_id, "battlefield", controller="A"
        )
        created = session.engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
            reason="legacy compatibility characterization",
        )
        self.assertEqual(
            {"Treasure", "Thopter"},
            {
                session.engine._resolve_object("A", ref).printed_name
                for ref in created
            },
        )


if __name__ == "__main__":
    unittest.main()
