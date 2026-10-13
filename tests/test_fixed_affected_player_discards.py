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
    # Expected contract from pinned CR 109.5, 603.3a, 609.3 and 701.9a-b:
    # A controls the triggering ability; B is the damaged or upkeep player.
    # B chooses up to the required available hand cards. A remains the cause's
    # controller and receives any separately printed "you gain life" result.
    # No target is chosen for that occurrence-bound player. Random, qualified,
    # variable and linked-cost discard instructions stay outside this slice.
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

    def test_standalone_controller_discard_uses_private_affected_player_choice(self):
        text = "Discard two cards."
        ir = self.compile(text)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertIsNone(node.target_schema)
        self.assertEqual("choose_cards_apnap", node.effects[0]["op"])
        self.assertEqual("$controller", node.effects[0]["actor"])
        self.assertEqual(["$controller"], node.effects[0]["players"])
        self.assertEqual(2, node.effects[0]["count"])
        self.assertTrue(node.effects[0]["hidden"])
        self.assertIn("choice.affected_player.fixed_controller_discard", node.capability_dependencies)

    def test_combat_damage_discard_preserves_trigger_controller_and_damaged_player(self):
        text = "Whenever this creature deals combat damage to a player, that player discards two cards."
        ir = self.compile(text, type_line="Creature — Specter")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("damage.dealt.self", node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual("$controller", node.effects[0]["actor"])
        self.assertEqual(["$context.target"], node.effects[0]["players"])
        self.assertEqual(2, node.effects[0]["count"])
        self.assertIn("trigger.result.fixed_context_player_discard", node.capability_dependencies)

    def test_scheduled_discard_keeps_affected_player_distinct_from_controller(self):
        text = "At the beginning of each player's upkeep, that player discards a card."
        ir = self.compile(text, type_line="Enchantment")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = ir.faces[0].nodes[0]
        self.assertEqual("step.begin", node.event)
        self.assertIsNone(node.target_schema)
        self.assertEqual("$controller", node.effects[0]["actor"])
        self.assertEqual(["$context.player"], node.effects[0]["players"])
        self.assertIn("trigger.result.fixed_context_player_discard", node.capability_dependencies)

    def test_mixed_scheduled_discard_does_not_redirect_controller_life_gain(self):
        text = "At the beginning of each opponent's upkeep, that player discards a card and you gain 1 life."
        ir = self.compile(text, type_line="Enchantment")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        effects = ir.faces[0].nodes[0].effects
        self.assertEqual("$controller", effects[0]["actor"])
        self.assertEqual(["$context.player"], effects[0]["players"])
        self.assertEqual({"op": "life", "player": "$controller", "delta": 1}, effects[1])

    def test_context_discard_scope_and_shape_boundaries_fail_closed(self):
        from quorune.compiler.affected_player_discard_templates import (
            FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC,
            FIXED_CONTROLLER_DISCARD_MECHANIC,
            standalone_controller_discard_effect_template,
        )
        from quorune.rules.affected_player_discard_capability_shapes import (
            fixed_context_player_discard_node_capabilities,
            fixed_controller_discard_node_capabilities,
            is_closed_fixed_bound_discard_program,
        )
        compiled = standalone_controller_discard_effect_template("Discard two cards.")
        effect = compiled[1][0]
        mechanics = compiled[3]
        self.assertTrue(fixed_controller_discard_node_capabilities(
            effects=(effect,), target_schema=None, mechanic_ids=mechanics))
        contextual = {**effect, "players": ["$context.target"]}
        context_mechanics = (*mechanics, FIXED_CONTEXT_PLAYER_DISCARD_MECHANIC)
        self.assertTrue(fixed_context_player_discard_node_capabilities(
            effects=(contextual,), target_schema=None, mechanic_ids=context_mechanics))
        from types import SimpleNamespace
        required = fixed_context_player_discard_node_capabilities(
            effects=(contextual,), target_schema=None, mechanic_ids=context_mechanics)
        program = SimpleNamespace(effects=(contextual,), target_schema=None,
            coverage=context_mechanics, capability_dependencies=required,
            event="damage.dealt.self",
            event_condition={"field": "target_kind", "op": "eq", "value": "player"})
        self.assertTrue(is_closed_fixed_bound_discard_program(program))
        for event, condition in (("spell.cast", program.event_condition),
                                 ("damage.dealt.self", None),
                                 ("damage.dealt.self", {"all": True}),
                                 ("damage.dealt.self", {"field": "target_kind", "op": "eq", "value": "creature"})):
            forged = SimpleNamespace(**{**vars(program), "event": event, "event_condition": condition})
            self.assertFalse(is_closed_fixed_bound_discard_program(forged))
        for changes in ({"count": True}, {"count": 0}, {"count": 4},
                        {"actor": "$context.target"}, {"players": ["$context.card"]},
                        {"players": ["$context.target", "$controller"]},
                        {"hidden": False}, {"then": "sacrifice"}):
            with self.subTest(changes=changes):
                self.assertEqual((), fixed_context_player_discard_node_capabilities(
                    effects=({**contextual, **changes},), target_schema=None,
                    mechanic_ids=context_mechanics))
        for text in ("Discard five cards.",
                     "Discard a creature card.", "Discard X cards.",
                     "Whenever this creature deals combat damage to a player, that player discards four cards.",
                     "Whenever this creature deals combat damage to a player, that player discards a creature card at random."):
            self.assertNotEqual("exact", self.compile(text, type_line="Creature — Test").status, text)
        text="Whenever this creature deals combat damage to a player, that player discards a card at random."
        ir=self.compile(text,type_line="Creature — Test")
        self.assertEqual("exact",ir.status,ir.material_residuals)
        self.assertEqual("discard_random_cards",ir.faces[0].nodes[0].effects[0]["op"])
        text = "Whenever this creature deals combat damage to a player, that player discards two cards."
        with patch("quorune.compiler.affected_player_discard_templates.damage_player_discard_result", return_value=None):
            self.assertNotEqual("exact", self.compile(text, type_line="Creature — Test").status)
        with patch("quorune.compiler.affected_player_discard_templates.standalone_controller_discard_effect_template", return_value=None):
            self.assertNotEqual("exact", self.compile("Discard two cards.").status)
        value = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        next(row for row in value["capabilities"] if row["id"] == FIXED_AFFECTED_PLAYER_DISCARD_CAPABILITY)["status"] = "blocked"
        registry = CapabilityRegistry(value)
        registry.mark_evidence_verified("0" * 64)
        self.assertNotEqual("exact", self.compile(text, registry=registry, type_line="Creature — Test").status)

    def test_unsupported_discard_choices_and_shape_mutations_fail_closed(self):
        unsupported = (
            "Target player discards a card at random.",
            "Target player discards X cards.",
            "Target player discards four cards.",
            "Target player discards all but one card from their hand.",
            "Target player reveals their hand and discards a card.",
            "You may have target player discard a card.",
            "Target player discards two cards at random, then draws two cards.",
            "Discard five cards.",
        )
        for text in unsupported:
            with self.subTest(text=text):
                self.assertIsNone(
                    fixed_affected_player_discard_effect_template(text)
                )
                ir = self.compile(text)
                if text == "Target player discards a card at random.":
                    self.assertEqual("exact",ir.status,ir.material_residuals)
                    self.assertEqual("discard_random_cards",ir.faces[0].nodes[0].effects[0]["op"])
                else:
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


class ContextDiscardRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from quorune.deck import DeckDefinition, DeckEntry
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "context-discard.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/fixed-context-discard-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Context discard", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 99),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    import test_bound_effect_programs as _witnesses
    session = _witnesses.BoundEffectProgramRuntimeTests.session
    add = _witnesses.BoundEffectProgramRuntimeTests.add
    ready = _witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = _witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = _witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = _witnesses.BoundEffectProgramRuntimeTests.replay

    def assert_whole_admission(self, session, name):
        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        program = compile_best_available_card_program(
            self.db, self.db.lookup(name), semantic_registry=session.engine.semantics,
            capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry,
            profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)

    def test_context_discard_runtime_is_private_principal_correct_and_replays(self):
        session = self.session(331001)
        source = self.add(session.engine, "Generic Standalone Controller Discard", zone="hand")
        self.assert_whole_admission(session, source.printed_name)
        action = self.ready(session, source, {"B": 1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(["A"], session.state.pending_decision.actors)
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        refs = [session.state.cards[oid].ref for oid in session.state.players["A"].zones["hand"]][:2]
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:B", {"action_id": "choose", "cards": refs})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act("pilot:A", {"action_id": "choose", "cards": refs})
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve(session)
        self.assertTrue(all(next(card for card in session.state.cards.values() if card.ref == ref).zone == "graveyard" for ref in refs))
        self.replay(session, load=True)

    def test_original_specter_combat_discard_ignores_player_shroud_and_replays(self):
        from quorune.model import CombatState
        session = self.session(331003)
        engine = session.engine
        source = self.add(engine, "Blazing Specter")
        self.add(engine, "Ivory Mask", seat="B")
        self.assert_whole_admission(session, source.printed_name)
        engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.active_player = "A"
        session.state.started = True
        session.state.phase = "combat"
        session.state.step = "declare_attackers"
        session.state.phase_index = 5
        session.state.combat = CombatState()
        engine._issue_attackers()
        self.checkpoint(session)
        accepted = session.act("pilot:A", {"a": "attack", "atk": {source.ref: "B"}})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(64):
            decision = session.state.pending_decision
            if session.state.players["B"].life == 38 and decision is not None and decision.kind != "priority":
                break
            self.assertIsNotNone(decision)
            principal = session.pending_principals()[0]
            if decision.kind == "priority":
                result = session.act(principal, {"action_id": "pass"})
            elif "block" in decision.kind:
                result = session.act(principal, {"a": "block", "blk": {}})
            else:
                self.fail("Unexpected combat decision: " + decision.kind)
            self.assertTrue(result.ok, result.summary)
        self.assertEqual(38, session.state.players["B"].life)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        ref = session.state.cards[session.state.players["B"].zones["hand"][0]].ref
        before_count = len(session.state.players["B"].zones["hand"])
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": "choose", "cards": [ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:B", {"action_id": "choose", "cards": [ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve(session)
        self.assertEqual(before_count - 1, len(session.state.players["B"].zones["hand"]))
        self.assertEqual("graveyard", next(card for card in session.state.cards.values() if card.ref == ref).zone)
        self.replay(session, load=True)

    def test_original_mists_upkeep_discard_retains_affected_player_and_replays(self):
        from test_scheduled_player_triggers import ScheduledPlayerTriggerRuntimeTests
        session = self.session(331002)
        mists = self.add(session.engine, "Necrogen Mists")
        self.assert_whole_admission(session, mists.printed_name)
        ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self, session, "B")
        trigger = next(item for item in session.state.stack if item.source_object_id == mists.object_id)
        self.assertEqual("A", trigger.controller)
        self.assertEqual("B", trigger.context["player"])
        self.resolve(session)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        for seat in "ACD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        ref = session.state.cards[session.state.players["B"].zones["hand"][0]].ref
        sizes = {seat: len(session.state.players[seat].zones["hand"]) for seat in "ABCD"}
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": "choose", "cards": [ref]})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act("pilot:B", {"action_id": "choose", "cards": [ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.resolve(session)
        self.assertEqual("graveyard", next(card for card in session.state.cards.values() if card.ref == ref).zone)
        self.assertEqual({**sizes, "B": sizes["B"] - 1},
            {seat: len(session.state.players[seat].zones["hand"]) for seat in "ABCD"})
        self.replay(session, load=True)

    def test_controller_discard_uses_short_hand_and_skips_empty_hand(self):
        for available in (1, 0):
            with self.subTest(available=available):
                session = self.session(331010 + available)
                engine = session.engine
                hand = list(session.state.players["A"].zones["hand"])
                for oid in hand[available:]:
                    engine.move_card(oid, "library", reason="Prepare the focused initial hand")
                source = self.add(engine, "Generic Standalone Controller Discard", zone="hand")
                action = self.ready(session, source, {"B": 1})
                self.checkpoint(session)
                result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.resolve(session)
                if available:
                    self.assertEqual(["A"], session.state.pending_decision.actors)
                    ref = session.state.cards[session.state.players["A"].zones["hand"][0]].ref
                    result = session.act("pilot:A", {"action_id": "choose", "cards": [ref]})
                    self.assertTrue(result.ok, result.summary)
                    self.resolve(session)
                    self.assertEqual("graveyard", next(card for card in session.state.cards.values() if card.ref == ref).zone)
                else:
                    decision = session.state.pending_decision
                    self.assertTrue(decision is None or decision.kind == "priority")
                self.assertEqual([], session.state.players["A"].zones["hand"])
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_mixed_upkeep_discards_for_active_player_and_gains_for_controller(self):
        from test_scheduled_player_triggers import ScheduledPlayerTriggerRuntimeTests
        session = self.session(331020)
        source = self.add(session.engine, "Generic Mixed Upkeep Discard")
        self.assert_whole_admission(session, source.printed_name)
        ScheduledPlayerTriggerRuntimeTests.enter_upkeep_from_untap(self, session, "B")
        self.resolve(session)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        self.assertEqual((40, 40), (session.state.players["A"].life, session.state.players["B"].life))
        ref = session.state.cards[session.state.players["B"].zones["hand"][0]].ref
        session = self.replay(session, load=True)
        result = session.act("pilot:B", {"action_id": "choose", "cards": [ref]})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual((41, 40), (session.state.players["A"].life, session.state.players["B"].life))
        self.assertEqual("graveyard", next(card for card in session.state.cards.values() if card.ref == ref).zone)
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
