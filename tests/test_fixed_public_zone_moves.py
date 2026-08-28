from __future__ import annotations

import copy
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.commander_zones import (
    commander_hand_library_replacement_effect,
    commit_commander_zone_choice_decline,
    CommanderZoneError,
    CommanderZoneStateChoice,
    pending_commander_zone_state_choices,
)
from quorune.compiler.public_zone_move_templates import (
    public_zone_move_effect_template,
)
from quorune.deck import DeckLoader
from quorune.model import CardInstance, StackItem
from quorune.object_predicate import ObjectQuerySpec
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.projection import StateProjector
from quorune.public_zone_moves import (
    PublicZoneDestination,
    PublicZoneMoveError,
    PublicZoneMoveSetSpec,
    PublicZoneOrigin,
    PublicZoneRelationAxis,
    PublicZoneSeatRelation,
    resolve_public_zone_move_set,
    select_public_zone_move_objects,
)
from quorune.record import (
    authoritative_state_hash,
    checkpoint_envelope,
    replay_record,
)
from quorune.rules.capabilities import (
    CapabilityRegistry,
    capability_dependencies_for_node,
    load_default_capability_registry,
)
from quorune.rules.graveyard_card_targets import (
    PublicGraveyardCardTargetKind,
    PublicGraveyardCardTargetSpec,
)
from quorune.semantic_runtime import (
    ExilePublicGraveyardCardIntent,
    MovePublicZoneSetIntent,
    ReadOnlyHandlerContext,
    ReadOnlyRulesQuery,
)
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.public_zone_move_handlers import (
    ExilePublicGraveyardCardHandler,
    MovePublicZoneSetHandler,
)
from quorune.semantic_runtime.zone_replacement_model import (
    ZoneChangeSubjectSnapshot,
)
from scripts.build_test_database import build_fixture_database


def card_record(
    oracle_text: str,
    *,
    type_line: str = "Instant",
    name: str = "Fixed Public Zone Move Fixture",
) -> CardRecord:
    return CardRecord(
        oracle_id="00000000-0000-4000-8000-000000000298",
        name=name,
        mana_cost="{1}{U}",
        mana_value=2.0,
        type_line=type_line,
        oracle_text=oracle_text,
        power="2" if "Creature" in type_line else None,
        toughness="2" if "Creature" in type_line else None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=(),
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


def object_row(
    ref: str,
    *,
    owner: str,
    controller: str | None = None,
    zone: str = "battlefield",
    types: tuple[str, ...] = ("creature",),
    logical_id: str | None = None,
) -> ObjectQueryResult:
    return ObjectQueryResult(
        object_id=f"object-{ref.casefold()}",
        logical_object_id=logical_id or f"logical-{ref.casefold()}",
        ref=ref,
        printed_name=f"Fixture {ref}",
        owner=owner,
        controller=controller or owner,
        zone=zone,
        types=types,
    )


class CommanderZoneMoveModelTests(unittest.TestCase):
    def commander(self, *, zone: str = "graveyard") -> CardInstance:
        return CardInstance(
            object_id="commander-object",
            ref="A01",
            oracle_id="commander-oracle",
            printed_name="Fixture Commander",
            owner="A",
            controller="A",
            zone=zone,
            is_commander=True,
            commander_designation_id="commander:A:1",
        )

    def test_commander_zone_choice_model_mutations_fail_closed(self):
        commander = self.commander()
        pending = pending_commander_zone_state_choices(
            (commander,),
            active_seats=("A", "B", "C", "D"),
            apnap_order=("B", "C", "D", "A"),
        )
        self.assertEqual(("A01",), tuple(value.ref for value in pending))
        commit_commander_zone_choice_decline(commander, pending[0])
        self.assertFalse(
            pending_commander_zone_state_choices(
                (commander,),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            )
        )
        for mutation in (
            {**pending[0].to_dict(), "zone": "hand"},
            {**pending[0].to_dict(), "designation_id": ""},
            {**pending[0].to_dict(), "owner": ""},
            {**pending[0].to_dict(), "extra": "open"},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(CommanderZoneError):
                    CommanderZoneStateChoice.from_dict(mutation)
        with self.assertRaises(CommanderZoneError):
            commit_commander_zone_choice_decline(
                commander,
                replace(pending[0], logical_object_id="stale@9"),
            )

        subject = ZoneChangeSubjectSnapshot(
            object_id=commander.object_id,
            object_ref=commander.ref,
            logical_object_id=commander.logical_object_id,
            owner="A",
            controller="B",
            origin="battlefield",
            destination="hand",
            destination_controller="A",
            entry_face_id="front",
            object_types=("creature",),
            is_card_object=True,
            is_commander=True,
            commander_designation_id="commander:A:1",
        )
        effect = commander_hand_library_replacement_effect(subject)
        self.assertIsNotNone(effect)
        assert effect is not None
        self.assertTrue(effect.optional)
        self.assertEqual("command", effect.operations[0].value)
        self.assertIsNone(
            commander_hand_library_replacement_effect(
                replace(subject, destination="graveyard")
            )
        )


class FixedPublicZoneMoveModelTests(unittest.TestCase):
    def test_set_descriptor_and_apnap_selection_are_closed(self):
        spec = PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",),
                types_all=("artifact",),
            ),
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=PublicZoneDestination.OWNER_HAND,
            relation_axis=PublicZoneRelationAxis.CONTROLLER,
            seat_relation=PublicZoneSeatRelation.OPPONENTS,
            exclude_source=True,
        )
        self.assertEqual(spec, PublicZoneMoveSetSpec.from_dict(spec.to_dict()))
        selected = select_public_zone_move_objects(
            (
                object_row("A-source", owner="A", types=("artifact",)),
                object_row("D-artifact", owner="D", types=("artifact",)),
                object_row("B-artifact", owner="B", types=("artifact",)),
                object_row("C-creature", owner="C"),
                object_row("C-artifact", owner="C", types=("artifact",)),
            ),
            spec,
            actor="A",
            active_seats=("A", "B", "C", "D"),
            apnap_order=("B", "C", "D", "A"),
            source_ref="A-source",
        )
        self.assertEqual(
            ("B-artifact", "C-artifact", "D-artifact"),
            tuple(value.ref for value in selected),
        )
        malformed = spec.to_dict()
        malformed["query"]["zones"] = ["hand"]
        with self.assertRaises(PublicZoneMoveError):
            PublicZoneMoveSetSpec.from_dict(malformed)


class FixedPublicZoneMoveCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capabilities = load_default_capability_registry()
        cls.registry_value = json.loads(
            (ROOT / "quorune" / "rules" / "capability-registry.json").read_text(
                encoding="utf-8"
            )
        )

    def compile(
        self,
        text: str,
        *,
        type_line: str = "Instant",
        capabilities=None,
    ):
        return compile_oracle_card(
            card_record(text, type_line=type_line),
            capability_registry=capabilities or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_public_zone_moves_compile_across_effect_contexts(self):
        contexts = (
            (
                "Exile target card from a graveyard.",
                "Instant",
                "spell_ability",
                "card.exile.public_graveyard",
            ),
            (
                "When this creature enters, exile all creatures.",
                "Creature — Test",
                "triggered_ability",
                "zone.move.fixed_public_set",
            ),
            (
                "{2}, {T}: Exile target player's graveyard.",
                "Artifact Creature — Test",
                "activated_ability",
                "zone.move.fixed_public_set",
            ),
            (
                "Return all artifacts target player owns to their hand.",
                "Sorcery",
                "spell_ability",
                "zone.move.fixed_public_set",
            ),
        )
        for text, type_line, kind, capability in contexts:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertTrue(node.exact)
                self.assertEqual(kind, node.kind)
                self.assertIn(capability, node.capability_dependencies)
                self.assertEqual(text, text[node.span.start : node.span.end])

    def test_type_qualified_public_graveyard_targets_compile_exact(self):
        for kind in PublicGraveyardCardTargetKind:
            text = f"Exile target {kind.value} from a graveyard."
            with self.subTest(target=kind.value):
                template = public_zone_move_effect_template(text)
                self.assertIsNotNone(template)
                assert template is not None
                self.assertEqual(
                    PublicGraveyardCardTargetSpec(kind).to_target_schema(),
                    template.target_schema,
                )
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node = ir.faces[0].nodes[0]
                self.assertEqual(template.target_schema, node.target_schema)
                self.assertIn(
                    "card.exile.public_graveyard",
                    node.capability_dependencies,
                )

    def test_unsupported_public_zone_move_shapes_remain_residual(self):
        self.assertIsNone(
            public_zone_move_effect_template(
                "Exile up to one target card from a graveyard."
            )
        )
        unsupported = (
            "Exile target legendary creature card from a graveyard.",
            "Exile target Zombie card from a graveyard.",
            "Exile target card with mana value 3 from a graveyard.",
            "Exile all creatures with mana value 3 or less.",
            "Exile all creatures. Return them at the next end step.",
            "Return all creatures except Krakens to their owners' hands.",
            "Return all permanents of the color of your choice to their owners' hands.",
            "Return all creatures from your graveyard to the battlefield.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(public_zone_move_effect_template(text))
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

    def test_public_zone_move_dependencies_and_shape_mutations_fail_closed(self):
        templates = (
            public_zone_move_effect_template(
                "Exile target card from a graveyard."
            ),
            public_zone_move_effect_template(
                "Exile target creature card from a graveyard."
            ),
            public_zone_move_effect_template("Exile all creatures."),
        )
        self.assertTrue(all(template is not None for template in templates))
        for template in templates:
            assert template is not None
            dependencies = capability_dependencies_for_node(
                effects=template.effects,
                target_schema=template.target_schema,
                mechanic_ids=template.mechanics,
            )
            self.assertTrue(dependencies)
            effect = dict(template.effects[0])
            for malformed in (
                {**effect, "op": "open_zone_move"},
                {**effect, "extra": True},
            ):
                self.assertFalse(
                    capability_dependencies_for_node(
                        effects=(malformed,),
                        target_schema=template.target_schema,
                        mechanic_ids=template.mechanics,
                    )
                )

        for blocked in (
            "card.exile.public_graveyard",
            "zone.move.fixed_public_set",
            "variant.commander.zone_return",
            "zone.change.destination_replacement",
            "target.revalidate_resolution",
        ):
            with self.subTest(blocked=blocked):
                value = deepcopy(self.registry_value)
                row = next(
                    item for item in value["capabilities"] if item["id"] == blocked
                )
                row["status"] = "blocked"
                row["blockers"] = ["focused public-zone-move mutation"]
                registry = CapabilityRegistry(value)
                direct = self.compile(
                    "Exile target card from a graveyard.",
                    capabilities=registry,
                )
                mass = self.compile(
                    "Exile all creatures.",
                    capabilities=registry,
                )
                if blocked in {
                    "card.exile.public_graveyard",
                    "target.revalidate_resolution",
                }:
                    self.assertNotEqual("exact", direct.status)
                if blocked in {
                    "zone.move.fixed_public_set",
                    "variant.commander.zone_return",
                    "zone.change.destination_replacement",
                    "target.revalidate_resolution",
                }:
                    self.assertNotEqual("exact", mass.status)

        with patch(
            "quorune.compiler.resolution_effect_templates."
            "public_zone_move_effect_template",
            return_value=None,
        ):
            self.assertNotEqual(
                "exact",
                self.compile("Exile all creatures.").status,
            )

    def test_handlers_lower_only_closed_intents(self):
        context = ReadOnlyHandlerContext(
            actor="A",
            default_reason="public zone-move fixture",
            query=ReadOnlyRulesQuery(
                seats=("A", "B", "C", "D"),
                active_seats=("A", "B", "C", "D"),
                apnap_order=("B", "C", "D", "A"),
            ),
        )
        direct = public_zone_move_effect_template(
            "Exile target card from a graveyard."
        )
        mass = public_zone_move_effect_template("Exile all creatures.")
        assert direct is not None and mass is not None
        direct_plan = ExilePublicGraveyardCardHandler().lower(
            {"op": "exile_public_graveyard_card", "card": "B07"},
            context,
        )
        mass_plan = MovePublicZoneSetHandler().lower(mass.effects[0], context)
        self.assertIsInstance(
            direct_plan.intents[0], ExilePublicGraveyardCardIntent
        )
        self.assertIsInstance(mass_plan.intents[0], MovePublicZoneSetIntent)
        for malformed in (
            {"op": "move_public_zone_set", "source": "$source"},
            {**mass.effects[0], "source": True},
            {**mass.effects[0], "arbitrary": True},
        ):
            with self.assertRaises(SemanticNodeError):
                MovePublicZoneSetHandler().lower(malformed, context)


class _RuntimeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "fixed-public-zone-moves.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
                ROOT
                / "tests"
                / "fixtures"
                / "fixed-public-zone-move-cards.json",
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

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int, *, spell: str | None = None):
        mishra = copy.deepcopy(self.mishra)
        if spell is not None:
            next(
                entry for entry in mishra.entries if entry.board == "mainboard"
            ).name = spell
        session = make_session(
            self.db,
            mishra,
            copy.deepcopy(self.zimone),
            players=4,
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
    def card(engine, seat: str, *, name: str | None = None, exclude=()):
        excluded = set(exclude)
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat
            and card.object_id not in excluded
            and card.zone != "command"
            and card.is_card_object
            and (name is None or card.printed_name == name)
        )

    @staticmethod
    def commander(engine, seat: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.is_commander
        )

    def ready_spell(self, session, name: str, mana: dict[str, int]):
        engine = session.engine
        source = self.card(engine, "A", name=name)
        engine.move_card(source.object_id, "hand", log=False)
        engine.state.players["A"].mana_pool.update(mana)
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        action = next(
            row for row in hints["actions"] if row.get("card") == source.ref
        )
        engine._issue_priority("A", hints)
        return source, action

    def resolve_all(self, session, *, apply_replacements: bool = True):
        for _ in range(100):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "replacement.order":
                principal = session.pending_principals()[0]
                projected = StateProjector(self.db, session.state)._decision(
                    principal
                )
                assert projected is not None
                options = projected["ctx"]["options"]
                selected = next(
                    option
                    for option in options
                    if bool(option.get("decline")) is (not apply_replacements)
                )
                payload = {
                    "action_id": "choose",
                    "replacement": selected["id"],
                }
                result = session.act(principal, payload)
                self.assertTrue(result.ok, result.summary)
                continue
            if decision is not None and decision.kind == "state.commander_zone":
                principal = session.pending_principals()[0]
                result = session.act(
                    principal,
                    {"a": "choose", "choice": "command"},
                )
                self.assertTrue(result.ok, result.summary)
                continue
            if session.state.stack:
                principals = session.pending_principals()
                self.assertTrue(principals)
                result = session.act(principals[0], {"action_id": "pass"})
                self.assertTrue(result.ok, result.summary)
                continue
            if decision is None:
                return
            if decision.kind == "priority" and not session.state.stack:
                return
            raise AssertionError(f"Unexpected pending decision {decision.kind}")
        self.fail("Resolution did not stabilize")

    def assert_replays(self, session, label: str):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])


class FixedPublicZoneMoveRuntimeTests(_RuntimeBase):
    def test_jirina_graveyard_trigger_and_protection_activation_replay(self):
        session = self.session(729410)
        engine = session.engine
        record = self.db.lookup("Jirina, Dauntless General")
        registration = register_generated_programs(
            self.db,
            engine.semantics,
            (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True,
            promote_exact_effect_programs=True,
        )
        self.assertEqual(2, registration["programs_generated"])
        programs = engine.semantics.programs_for_oracle(record.oracle_id)
        trigger = next(
            program
            for program in programs
            if program.event == "permanent.enter.self"
        )
        activation = next(
            program for program in programs if program.event == "activate"
        )
        self.assertEqual(
            {
                "target.revalidate_resolution",
                "trigger.event.normalized_zone_change",
                "trigger.placement.apnap",
                "zone.move.fixed_public_set",
            },
            set(trigger.capability_dependencies),
        )
        self.assertGreaterEqual(
            set(activation.capability_dependencies),
            {
                "activation.source_zone_change.fixed",
                "continuous.resolution.fixed_characteristics_until_end_of_turn",
                "permanent.indestructible.ordinary",
                "target.protection.hexproof_permanent",
            },
        )

        jirina = CardInstance(
            object_id="fixture:jirina",
            ref="A-JIRINA",
            oracle_id=record.oracle_id,
            printed_name=record.name,
            owner="A",
            controller="A",
            zone="battlefield",
            zone_timestamp=engine.state.timestamp_sequence + 1,
            known_to=list(engine.seats),
            revealed_to=list(engine.seats),
        )
        engine.state.cards[jirina.object_id] = jirina
        engine.state.players["A"].zones["battlefield"].append(
            jirina.object_id
        )
        human_ref = engine.create_token(
            "A",
            name="Human witness",
            characteristics={
                "type_line": "Token Creature — Human",
                "power": "1",
                "toughness": "1",
            },
            reason="Jirina interaction witness",
        )[0]
        human = engine._resolve_object(
            "A", human_ref, zones={"battlefield"}
        )
        nonhuman_ref = engine.create_token(
            "A",
            name="Non-Human witness",
            characteristics={
                "type_line": "Token Creature — Spirit",
                "power": "1",
                "toughness": "1",
            },
            reason="Jirina exclusion witness",
        )[0]
        nonhuman = engine._resolve_object(
            "A", nonhuman_ref, zones={"battlefield"}
        )
        graveyard_cards = []
        for _ in range(2):
            card = self.card(
                engine,
                "B",
                exclude=(value.object_id for value in graveyard_cards),
            )
            engine.move_card(card.object_id, "graveyard", log=False)
            graveyard_cards.append(card)

        engine.state.active_player = "A"
        engine.state.started = True
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.stack.append(
            StackItem(
                stack_id="jirina-entry-trigger",
                ref="S-JIRINA-ENTRY",
                kind="triggered_ability",
                controller="A",
                label=trigger.label,
                semantic_key=trigger.key,
                source_object_id=jirina.object_id,
                targets=["B"],
                visibility=list(engine.seats),
                context={
                    "event": "permanent.enter.self",
                    "source_logical_object_id": jirina.logical_object_id,
                },
            )
        )
        engine.permissions.invalidate_current()
        engine._grant_priority("A")
        engine._issue_priority("A")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        action = next(
            row
            for row in engine._priority_action_hints("A")["actions"]
            if row.get("source") == jirina.ref
            and row.get("ability") == "ab2"
        )
        accepted = session.act("pilot:A", {"action_id": action["id"]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", jirina.zone)
        for iteration in range(24):
            if not session.state.stack:
                break
            decision = session.state.pending_decision
            principals = session.pending_principals()
            self.assertTrue(principals)
            self.assertIsNotNone(decision)
            if decision.kind == "arbiter.resolve":
                stack_program = engine.semantics.get(
                    engine.state.stack[-1].semantic_key
                )
                self.assertIsNotNone(stack_program)
                resolved = session.act(
                    "arbiter",
                    {
                        "action_id": "resolve",
                        "effects": stack_program.effects,
                    },
                )
                self.assertTrue(resolved.ok, resolved.summary)
                continue
            self.assertEqual(
                "priority",
                decision.kind,
                (iteration, decision.to_dict()),
            )
            projected = StateProjector(self.db, session.state)._decision(
                principals[0]
            )
            self.assertIsNotNone(projected)
            pass_action = next(
                row
                for row in projected["ctx"]["legal"]["actions"]
                if row.get("action") == "pass"
            )
            result = session.act(
                principals[0], {"action_id": pass_action["id"]}
            )
            self.assertTrue(
                result.ok,
                (iteration, decision.to_dict(), result.summary),
            )
        else:
            self.fail("Jirina interaction stack did not stabilize")

        human_keywords = set(engine._effective_card_data(human)["keywords"])
        self.assertGreaterEqual(
            human_keywords, {"Hexproof", "Indestructible"}
        )
        self.assertNotIn(
            "Hexproof", engine._effective_card_data(nonhuman)["keywords"]
        )
        self.assertEqual("graveyard", jirina.zone)
        self.assertTrue(
            all(card.zone == "exile" for card in graveyard_cards)
        )
        self.assertFalse(engine.state.players["B"].zones["graveyard"])
        self.assert_replays(session, "jirina-public-set-protection-record")

    def test_target_graveyard_exile_revalidates_and_replays(self):
        session = self.session(729401, spell="Public Grave Exile")
        engine = session.engine
        target = self.card(engine, "B")
        engine.move_card(target.object_id, "graveyard", log=False)
        source, action = self.ready_spell(
            session, "Public Grave Exile", {"B": 1}
        )
        self.assertIn(target.ref, action["target_schema"]["legal_refs"])
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_all(session)
        self.assertEqual("exile", target.zone)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "public-graveyard-exile-record")

    def test_target_graveyard_exile_rejects_stale_target_and_replays(self):
        session = self.session(729409, spell="Public Grave Exile")
        engine = session.engine
        target = self.card(engine, "B")
        engine.move_card(target.object_id, "graveyard", log=False)
        source, action = self.ready_spell(
            session, "Public Grave Exile", {"B": 1}
        )
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)

        engine.move_card(target.object_id, "hand", log=False)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        self.resolve_all(session)

        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "stale-public-graveyard-exile-record")

    def test_typed_graveyard_exile_advertises_and_replays(self):
        session = self.session(729408, spell="Public Creature Grave Exile")
        engine = session.engine
        creature = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and card.zone != "command"
            and card.is_card_object
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        noncreature = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and card.zone != "command"
            and card.is_card_object
            and card.object_id != creature.object_id
            and "creature"
            not in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        engine.move_card(creature.object_id, "graveyard", log=False)
        engine.move_card(noncreature.object_id, "graveyard", log=False)
        source, action = self.ready_spell(
            session,
            "Public Creature Grave Exile",
            {"B": 1},
        )
        legal_refs = set(action["target_schema"]["legal_refs"])
        self.assertIn(creature.ref, legal_refs)
        self.assertNotIn(noncreature.ref, legal_refs)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [creature.ref],
                "pay": "manual",
                "payment": {"B": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_all(session)
        self.assertEqual("exile", creature.zone)
        self.assertEqual("graveyard", noncreature.zone)
        self.assertEqual("graveyard", source.zone)
        self.assert_replays(session, "typed-public-graveyard-exile-record")

    def test_mass_zone_moves_use_apnap_replacements_and_replay(self):
        session = self.session(729402, spell="Fixed Public Return")
        engine = session.engine
        commanders = []
        for seat in ("A", "B", "C", "D"):
            commander = self.commander(engine, seat)
            engine.move_card(
                commander.object_id,
                "battlefield",
                controller=seat,
                log=False,
            )
            commanders.append(commander)
        source, action = self.ready_spell(
            session, "Fixed Public Return", {"C": 3, "U": 1}
        )
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        accepted = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "targets": [],
                "pay": "manual",
                "payment": {"C": 3, "U": 1},
            },
        )
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_all(session, apply_replacements=True)
        self.assertTrue(all(card.zone == "command" for card in commanders))
        self.assertEqual("graveyard", source.zone)
        replacement_choosers = [
            event.actor
            for event in engine.state.events
            if event.code == "decision.response"
        ]
        self.assertTrue({"A", "B", "C", "D"}.issubset(replacement_choosers))
        self.assert_replays(session, "fixed-public-return-record")

    def test_public_zone_move_privacy_and_stale_identity_roll_back(self):
        session = self.session(729403)
        engine = session.engine
        target = self.card(engine, "B")
        engine.move_card(target.object_id, "graveyard", log=False)
        before = authoritative_state_hash(engine.state)
        target.object_kind = "token"
        with self.assertRaises(PublicZoneMoveError):
            from quorune.public_zone_moves import exile_public_graveyard_card

            exile_public_graveyard_card(
                engine,
                target.ref,
                actor="A",
                reason="stale noncard witness",
            )
        target.object_kind = "card"
        self.assertEqual(before, authoritative_state_hash(engine.state))

        creature = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "C"
            and card.zone != "command"
            and card.is_card_object
            and "creature"
            in engine._type_parts(
                str(engine._effective_card_data(card).get("type_line") or "")
            )[0]
        )
        engine.move_card(
            creature.object_id,
            "battlefield",
            controller="C",
            log=False,
        )
        spec = PublicZoneMoveSetSpec(
            query=ObjectQuerySpec(
                zones=("battlefield",), types_all=("creature",)
            ),
            origin=PublicZoneOrigin.BATTLEFIELD,
            destination=PublicZoneDestination.OWNER_HAND,
        )
        resolve_public_zone_move_set(
            engine,
            actor="A",
            spec=spec,
            reason="privacy witness",
        )
        for seat in ("A", "B", "C", "D"):
            rendered = json.dumps(
                StateProjector(self.db, engine.state)._snapshot(f"pilot:{seat}"),
                sort_keys=True,
            )
            self.assertNotIn("logical_object_id", rendered)
            self.assertNotIn(creature.object_id, rendered)


class CommanderZoneMoveRuntimeTests(_RuntimeBase):
    def test_commander_hand_and_library_replacement_uses_owner_choice_and_replays(
        self,
    ):
        from quorune.semantics import SemanticProgram
        from quorune.model import StackItem

        cases = (
            ("hand", True, "command"),
            ("library", False, "library"),
        )
        for index, (destination, apply_replacement, expected) in enumerate(cases):
            with self.subTest(destination=destination):
                session = self.session(729404 + index)
                engine = session.engine
                commander = self.commander(engine, "B")
                engine.move_card(
                    commander.object_id,
                    "battlefield",
                    controller="A",
                    log=False,
                )
                key = f"test:commander-{destination}-replacement"
                program = SemanticProgram(
                    key=key,
                    label=f"Move commander to {destination}",
                    effects=[
                        {
                            "op": "move",
                            "card": commander.ref,
                            "destination": destination,
                        }
                    ],
                    trust_level="provisional",
                )
                engine.semantics.put(program)
                engine.state.stack.append(
                    StackItem(
                        stack_id=f"commander-{destination}-replacement",
                        ref=f"S-commander-{destination}-replacement",
                        kind="triggered_ability",
                        controller="A",
                        label=program.label,
                        semantic_key=program.key,
                        visibility=list(engine.seats),
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
                self.resolve_all(
                    session,
                    apply_replacements=apply_replacement,
                )
                self.assertEqual(expected, commander.zone)
                self.assert_replays(
                    session,
                    f"commander-{destination}-replacement-record",
                )

    def test_commander_graveyard_and_exile_state_choice_replays(self):
        cases = (("graveyard", "remain"), ("exile", "command"))
        for index, (zone, choice) in enumerate(cases):
            with self.subTest(zone=zone, choice=choice):
                session = self.session(729405 + index)
                engine = session.engine
                commander = self.commander(engine, "B")
                engine.move_card(commander.object_id, zone, log=False)
                self.assertTrue(engine._stabilize())
                self.assertEqual(
                    "state.commander_zone", engine.state.pending_decision.kind
                )
                projected_b = StateProjector(
                    self.db, engine.state
                )._decision("pilot:B")
                projected_a = StateProjector(
                    self.db, engine.state
                )._decision("pilot:A")
                self.assertIsNotNone(projected_b)
                self.assertIsNone(projected_a)
                assert projected_b is not None
                self.assertEqual(
                    {"command", "remain"},
                    {
                        action["id"]
                        for action in projected_b["legal_actions"]
                    },
                )
                session.initial_checkpoint = checkpoint_envelope(engine.state)
                session.commands.clear()
                session.decisions.clear()
                accepted = session.act(
                    "pilot:B",
                    {"a": "choose", "choice": choice},
                )
                self.assertTrue(accepted.ok, accepted.summary)
                expected = "command" if choice == "command" else zone
                self.assertEqual(expected, commander.zone)
                if choice == "remain":
                    self.assertEqual(
                        commander.logical_object_id,
                        commander.commander_zone_choice_logical_id,
                    )
                    self.assertFalse(engine._stabilize())
                rendered = json.dumps(
                    StateProjector(self.db, engine.state)._snapshot("pilot:A"),
                    sort_keys=True,
                )
                self.assertNotIn(commander.object_id, rendered)
                self.assert_replays(
                    session, f"commander-{zone}-state-choice-record"
                )

    def test_commander_zone_choice_stale_identity_rolls_back(self):
        session = self.session(729407)
        engine = session.engine
        commander = self.commander(engine, "B")
        engine.move_card(commander.object_id, "graveyard", log=False)
        self.assertTrue(engine._stabilize())
        engine.move_card(commander.object_id, "exile", log=False)
        before = authoritative_state_hash(engine.state)
        rejected = session.act(
            "pilot:B",
            {"a": "choose", "choice": "command"},
        )
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))


if __name__ == "__main__":
    unittest.main()
