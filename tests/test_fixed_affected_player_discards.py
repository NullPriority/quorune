from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session
from quorune.carddb import CardDatabase, CardRecord
from quorune.compiler.affected_player_discard_templates import (
    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
    FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC,
    FixedAffectedPlayerDiscardTemplate,
    fixed_affected_player_discard_effect_template,
)
from quorune.compiler.program_generation import register_generated_programs
from quorune.deck import DeckLoader
from quorune.errors import GameRuleError
from quorune.model import CardInstance, StackItem
from quorune.oracle_ir import compile_oracle_card
from quorune.projection import StateProjector
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
from scripts.build_test_database import build_fixture_database


REGISTRY_PATH = ROOT / "quorune" / "rules" / "capability-registry.json"


def focused_database(directory: str) -> CardDatabase:
    database = Path(directory) / "affected-player-discard.sqlite3"
    build_fixture_database(
        [
            ROOT / "tests" / "fixtures" / "scryfall-exact-lists.json",
            ROOT / "tests" / "fixtures" / "qualified-enchant-cards.json",
        ],
        database,
    )
    return CardDatabase(database)


def current_capabilities() -> CapabilityRegistry:
    value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    registry = CapabilityRegistry(value)
    registry.mark_evidence_verified("0" * 64)
    return registry


class FixedAffectedPlayerDiscardCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = CardRecord(
            oracle_id="fixture:affected-player-discard-compiler",
            name="Generic Affected Player Discard Compiler Fixture",
            mana_cost="{1}", mana_value=1, type_line="Artifact", oracle_text="",
            power=None, toughness=None, loyalty=None, defense=None,
            colors=(), color_identity=(), keywords=(), produced_mana=(),
            layout="normal", released_at="2026-01-01",
            legalities={"commander":"legal"}, faces=(), raw={},
        )
        cls.capabilities = current_capabilities()

    def compile(self, text: str, *, registry=None, type_line="Sorcery"):
        return compile_oracle_card(
            replace(
                self.base,
                name="Fixed affected-player discard fixture",
                oracle_text=text,
                type_line=type_line,
                keywords=(),
                faces=(),
            ),
            capability_registry=registry or self.capabilities,
            capability_profile="commander_review",
        )

    def test_fixed_affected_player_discards_compile_across_contexts_and_modes(
        self,
    ):
        fixtures = (
            ("Target player discards two cards.", "Sorcery"),
            (
                "When this creature enters, each opponent discards a card.",
                "Creature — Test",
            ),
            (
                "{2}, {T}: Target opponent discards a card.",
                "Artifact Creature — Test",
            ),
            (
                "Choose one —\n"
                "• Target player discards two cards.\n"
                "• Each opponent discards a card.",
                "Sorcery",
            ),
            (
                "Target player discards two cards. You draw a card.",
                "Sorcery",
            ),
        )
        for text, type_line in fixtures:
            with self.subTest(text=text):
                ir = self.compile(text, type_line=type_line)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                nodes = [
                    node
                    for face in ir.faces
                    for node in face.nodes
                    if FIXED_AFFECTED_PLAYER_DISCARD_MECHANIC
                    in node.mechanics
                ]
                self.assertTrue(nodes)
                for node in nodes:
                    self.assertIn(
                        FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
                        node.capability_dependencies,
                    )
                    self.assertEqual(
                        node.text,
                        text[node.span.start : node.span.end],
                    )

        template = fixed_affected_player_discard_effect_template(
            "Target opponent discards three cards."
        )
        self.assertIsNotNone(template)
        assert template is not None
        self.assertEqual(3, template.count)
        self.assertEqual("opponent", template.target_schema["player_relation"])
        self.assertTrue(template.effects[0]["hidden"])

    def test_whole_hand_target_promotes_composition_but_keeps_counted_leaf_boundary(self):
        text = "Target player discards their hand."
        self.assertIsNone(fixed_affected_player_discard_effect_template(text))
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("discard_whole_hands", node.effects[0]["op"])
        self.assertEqual(["$target.0"], node.effects[0]["players"])
        self.assertEqual(["player"], node.target_schema["categories"])
        self.assertEqual("any", node.target_schema["player_relation"])
        self.assertEqual(1, node.target_schema["count"])
        self.assertIn("zone.discard.whole_hand", node.capability_dependencies)

    def test_unsupported_discard_choices_and_shape_mutations_fail_closed(self):
        unsupported = (
            "Target player discards a card at random.",
            "Target player discards X cards.",
            "Target player discards four cards.",
            "Target player discards all but one card from their hand.",
            "Target player reveals their hand and discards a card.",
            "You may have target player discard a card.",
            "Target player discards two cards at random, then draws two cards.",
            "Discard a card.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_affected_player_discard_effect_template(text)
                )
                ir = self.compile(text)
                self.assertNotEqual("exact", ir.status)
                self.assertTrue(ir.material_residuals)

        template = fixed_affected_player_discard_effect_template(
            "Target player discards two cards."
        )
        assert template is not None
        _template_id, effects, schema, mechanics = template.compiled()
        mutations = []
        for field, value in (
            ("count", 4),
            ("hidden", False),
            ("then", "sacrifice"),
            ("zone", "battlefield"),
        ):
            effect = deepcopy(effects[0])
            effect[field] = value
            mutations.append((effect, schema, mechanics))
        effect = deepcopy(effects[0])
        effect["predicate"]["zones"] = ["battlefield"]
        mutations.append((effect, schema, mechanics))
        mutations.append((effects[0], None, mechanics))
        for effect, target_schema, mechanic_ids in mutations:
            with self.subTest(effect=effect, schema=target_schema):
                self.assertNotIn(
                    FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY,
                    capability_dependencies_for_node(
                        effects=(effect,),
                        target_schema=target_schema,
                        mechanic_ids=mechanic_ids,
                    ),
                )
        with self.assertRaisesRegex(ValueError, "count"):
            FixedAffectedPlayerDiscardTemplate(
                subject=template.subject,
                count=0,
            )

    def test_target_discard_dependency_fails_closed(self):
        text = "Target opponent discards two cards."
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        dependency = next(
            row
            for row in value["capabilities"]
            if row["id"] == "zone.change.destination_replacement"
        )
        dependency["status"] = "blocked"
        dependency["blockers"] = ["focused discard dependency"]
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        ir = self.compile(text, registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(ir.material_residuals)

    def test_bound_discard_draw_integration_preserves_leaf_boundary(self):
        text = "Target player discards two cards, then draws two cards."
        self.assertIsNone(fixed_affected_player_discard_effect_template(text))
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("bound-effect-program-v1", node.template_id)
        self.assertIn("resolution.effect_program.bound_references", node.capability_dependencies)
        self.assertEqual(("choose_cards_apnap", "draw"), tuple(e["op"] for e in node.effects))
        self.assertEqual(["$target.0"], node.effects[0]["players"])
        self.assertEqual("discard", node.effects[0]["then"])
        self.assertEqual("$target.0", node.effects[1]["player"])

    def test_affected_player_discard_compiler_mutant_is_killed(self):
        witnesses = (
            "Target player discards two cards.",
            "When this creature enters, each opponent discards a card.",
            "{2}, {T}: Target opponent discards a card.",
            "Choose one —\n"
            "• Target player discards two cards.\n"
            "• Each player discards a card.",
        )
        self.assertTrue(all(self.compile(text).status == "exact" for text in witnesses))
        with patch(
            "quorune.compiler.resolution_effect_templates."
            "fixed_affected_player_discard_effect_template",
            return_value=None,
        ):
            self.assertTrue(
                all(self.compile(text).status != "exact" for text in witnesses)
            )


class FixedAffectedPlayerDiscardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
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
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=4,
            seed=seed,
        )
        keep_all(session)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.state.priority_passes = []
        engine.state.pending_trigger_batches.clear()
        engine.state.stack.clear()
        session.commands.clear()
        session.decisions.clear()
        return session

    def register(self, engine, *names: str) -> None:
        register_generated_programs(
            self.db,
            engine.semantics,
            tuple(self.db.lookup(name) for name in names),
            trust_level="provisional",
            capability_registry=load_default_capability_registry(),
            capability_profile=engine.state.config.review_profile,
            promote_exact_runtime_handlers=True,
            promote_exact_capability_declarations=True,
            promote_exact_effect_programs=True,
        )

    def permanent(self, engine, *, seat: str, name: str, ref: str):
        record = self.db.lookup(name)
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

    def stage(
        self,
        engine,
        text: str,
        *,
        targets: tuple[str, ...] = (),
        following_effects: tuple[dict[str, object], ...] = (),
    ) -> StackItem:
        template = fixed_affected_player_discard_effect_template(text)
        self.assertIsNotNone(template)
        assert template is not None
        ref = engine._next_ref("S")
        item = StackItem(
            stack_id=engine._stable_runtime_id("stack", ref),
            ref=ref,
            kind="triggered_ability",
            controller="A",
            label="Affected-player discard fixture",
            targets=list(targets),
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        engine._begin_resolve_item(
            item,
            (*template.effects, *following_effects),
            None,
            note="Affected-player discard fixture",
        )
        return item

    @staticmethod
    def hand_cards(engine, seat: str) -> list[CardInstance]:
        return [
            engine.state.cards[object_id]
            for object_id in engine.state.players[seat].zones["hand"]
        ]

    @staticmethod
    def choose(session, seat: str, refs: list[str]):
        return session.act(
            f"pilot:{seat}",
            {"action_id": "choose", "cards": refs},
        )

    def choose_replacements(self, session) -> None:
        while (
            session.state.pending_decision is not None
            and session.state.pending_decision.kind == "replacement.order"
        ):
            principal = session.pending_principals()[0]
            projected = StateProjector(
                self.db, session.state
            )._decision(principal)
            self.assertIsNotNone(projected)
            assert projected is not None
            selected = projected["ctx"]["options"][0]["id"]
            result = session.act(
                principal,
                {
                    "action_id": "choose",
                    "replacement": selected,
                    "plan": "ORDER_REPLACEMENTS",
                    "reason": "Choose the discard destination replacement.",
                },
            )
            self.assertTrue(result.ok, result.summary)

    def assert_replays(self, session, label: str) -> None:
        expected_hash = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as temporary:
            record_dir = Path(temporary) / label
            session.save(record_dir)
            replay = replay_record(record_dir, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected_hash, replay["final_state_hash"])

    def test_private_apnap_discard_aura_attachment_zone_replacement_and_replay(
        self,
    ):
        session = self.session(70109101)
        engine = session.engine
        engine.state.active_player = "C"
        self.register(engine, "Dauthi Voidwalker", "Ice Over")
        attachment_target = self.permanent(
            engine,
            seat="A",
            name="Birds of Paradise",
            ref="discard-aura-target",
        )
        aura_record = self.db.lookup("Ice Over")
        aura = CardInstance(
            object_id="fixture:discard-aura",
            ref="discard-aura",
            oracle_id=aura_record.oracle_id,
            printed_name=aura_record.name,
            owner="A",
            controller="A",
            zone="hand",
            zone_timestamp=engine.state.event_sequence + 1,
            known_to=["A"],
            revealed_to=[],
        )
        engine.state.cards[aura.object_id] = aura
        engine.state.players["A"].zones["hand"].append(aura.object_id)
        engine.move_card(
            aura.object_id,
            "battlefield",
            controller="A",
            aura_target_ref=attachment_target.ref,
            reason="focused discard aura attachment",
        )
        self.assertEqual(attachment_target.object_id, aura.attached_to)
        self.permanent(
            engine,
            seat="A",
            name="Dauthi Voidwalker",
            ref="discard-voidwalker",
        )
        chosen = {seat: self.hand_cards(engine, seat)[0] for seat in "BCD"}
        self.stage(engine, "Each opponent discards a card.")
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        for index, seat in enumerate("CDB"):
            self.assertEqual(f"pilot:{seat}", session.pending_principals()[0])
            decision = StateProjector(self.db, engine.state)._decision(
                f"pilot:{seat}"
            )
            self.assertIsNotNone(decision)
            assert decision is not None
            serialized = json.dumps(decision, sort_keys=True)
            self.assertIn(chosen[seat].ref, serialized)
            self.assertNotIn(chosen[seat].object_id, serialized)
            for other in set("BCD") - {seat}:
                self.assertNotIn(chosen[other].ref, serialized)
                self.assertIsNone(
                    StateProjector(self.db, engine.state)._decision(
                        f"pilot:{other}"
                    )
                )
            result = self.choose(session, seat, [chosen[seat].ref])
            self.assertTrue(result.ok, result.summary)
            if index < 2:
                self.assertTrue(
                    all(card.zone == "hand" for card in chosen.values())
                )

        self.choose_replacements(session)
        for card in chosen.values():
            self.assertEqual("exile", card.zone)
            self.assertEqual(1, card.counters["void"])
        self.assertEqual("battlefield", aura.zone)
        self.assertEqual(attachment_target.object_id, aura.attached_to)
        self.assert_replays(session, "fixed-private-apnap-discard")

    def test_fixed_count_discard_uses_available_cards_and_skips_empty_hand(self):
        session = self.session(70109102)
        engine = session.engine
        retained_counts = {"A": 3, "B": 1, "C": 0, "D": 2}
        retained: dict[str, list[CardInstance]] = {}
        for seat, count in retained_counts.items():
            cards = self.hand_cards(engine, seat)
            retained[seat] = cards[:count]
            for card in cards[count:]:
                engine.move_card(
                    card.object_id,
                    "library",
                    position="bottom",
                    log=False,
                )
        self.stage(engine, "Each player discards three cards.")

        for seat in "ABD":
            self.assertEqual(f"pilot:{seat}", session.pending_principals()[0])
            result = self.choose(
                session,
                seat,
                [card.ref for card in retained[seat]],
            )
            self.assertTrue(result.ok, result.summary)
            if seat != "D":
                self.assertTrue(
                    all(
                        card.zone == "hand"
                        for cards in retained.values()
                        for card in cards
                    )
                )
        self.assertTrue(
            all(
                card.zone == "graveyard"
                for cards in retained.values()
                for card in cards
            )
        )

    def test_discard_choice_resumes_following_typed_effect(self):
        session = self.session(70109104)
        engine = session.engine
        discarded = self.hand_cards(engine, "B")[0]
        controller_hand_before = len(engine.state.players["A"].zones["hand"])
        self.stage(
            engine,
            "Target player discards a card.",
            targets=("B",),
            following_effects=(
                {
                    "op": "draw",
                    "player": "$controller",
                    "count": 1,
                    "private": True,
                },
            ),
        )

        result = self.choose(session, "B", [discarded.ref])

        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", discarded.zone)
        self.assertEqual(
            controller_hand_before + 1,
            len(engine.state.players["A"].zones["hand"]),
        )

    def test_stale_and_malformed_private_discard_reject_before_mutation(self):
        session = self.session(70109103)
        engine = session.engine
        stale, current = self.hand_cards(engine, "B")[:2]
        self.stage(
            engine,
            "Target player discards two cards.",
            targets=("B",),
        )
        engine.move_card(
            stale.object_id,
            "graveyard",
            reason="focused stale discard choice",
        )
        before = authoritative_state_hash(engine.state)
        result = self.choose(session, "B", [stale.ref, current.ref])
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        template = fixed_affected_player_discard_effect_template(
            "Each opponent discards a card."
        )
        assert template is not None
        malformed = deepcopy(template.effects[0])
        malformed["hidden"] = False
        before = authoritative_state_hash(engine.state)
        with self.assertRaises(GameRuleError):
            engine._issue_apnap_choice(
                effect=malformed,
                continuation={
                    "stack_ref": engine.state.stack[-1].ref,
                    "effects": [],
                    "destination": None,
                    "note": "malformed discard",
                },
            )
        self.assertEqual(before, authoritative_state_hash(engine.state))


if __name__ == "__main__":
    unittest.main()
