from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from common import keep_all, load_assets, make_session
from quorune import (
    PilotMemory,
    PilotResponse,
    ScriptedPilot,
    SequentialPilotRunner,
    semantic_preflight,
)
from quorune.record import replay_record
from quorune.report import derive_review, review_markdown


class NativeV3AuditAndPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def test_legacy_placeholders_are_not_reasons_or_model_calls(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=901
        )
        decisions = [
            {
                "accepted": True,
                "legacy_incomplete": True,
                "legal_alternatives": "unavailable",
                "reason": "unavailable in v2 record",
                "plan": "unavailable in v2 record",
                "action": "pass",
                "role": "pilot",
            }
        ]
        review = derive_review(
            session.engine,
            decisions=decisions,
            manifest={"replay": {"verification": "snapshot_only"}},
        )
        audit = review["pilot_audit"]
        self.assertFalse(audit["complete_alternatives"])
        self.assertFalse(audit["complete_reasons"])
        self.assertIsNone(audit["pilot_invocations_observed"])
        self.assertIsNone(audit["arbiter_invocations_observed"])
        self.assertEqual("unknown_legacy", audit["token_measurement_status"])

    def test_scripted_provider_records_actual_invocation_metadata(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=902
        )
        seen = []

        def chooser(observation, decision, memory: PilotMemory):
            seen.append(observation)
            self.assertNotIn(
                "hand", observation["state"]["players"]["B"]
            )
            return {
                "action_id": "keep",
                "plan": "MULLIGAN",
                "reason": "Functional opening hand.",
                "input_tokens": 17,
                "output_tokens": 6,
                "memory_update": "Keep exact color access in mind.",
            }

        runner = SequentialPilotRunner(
            session,
            {"pilot:A": ScriptedPilot(chooser=chooser)},
        )
        self.assertTrue(runner.step())
        self.assertEqual(1, runner.metrics.pilot_invocations)
        self.assertEqual(17, runner.metrics.input_tokens_observed)
        self.assertEqual(6, runner.metrics.output_tokens_observed)
        metrics = runner.metrics.to_dict()
        self.assertEqual(17, metrics["pilot_input_tokens_observed"])
        self.assertEqual(6, metrics["pilot_output_tokens_observed"])
        self.assertIsNone(metrics["arbiter_input_tokens_observed"])
        self.assertEqual("complete", metrics["token_measurement_status"])
        row = session.decisions[0]
        self.assertTrue(row["provider_invoked"])
        self.assertEqual("scripted", row["provider"])
        self.assertEqual(17, row["metrics"]["input_tokens"])
        self.assertEqual(
            "Keep exact color access in mind.",
            runner.memories["pilot:A"].text,
        )
        self.assertEqual(1, len(seen))

    def test_elimination_preserves_hidden_identity_and_public_knowledge(self):
        session = make_session(self.db, self.mishra, self.zimone, seed=903)
        keep_all(session)
        engine = session.engine
        hidden_hand = engine.state.players["B"].zones["hand"][0]
        hidden_library = engine.state.players["B"].zones["library"][-1]
        public = engine.state.players["B"].zones["hand"][1]
        hidden_names = {
            engine.state.cards[hidden_hand].printed_name,
            engine.state.cards[hidden_library].printed_name,
        }
        public_ref = engine.state.cards[public].ref
        engine.move_card(public, "graveyard", reason="public test")
        engine.permissions.invalidate_current()
        engine.state.players["B"].life = 0
        engine._stabilize()

        for principal in ("pilot:C", "pilot:D"):
            packet = session.packet(principal, full=True)
            serialized = json.dumps(packet)
            self.assertTrue(
                all(name not in serialized for name in hidden_names)
            )
            self.assertNotIn(
                engine.state.cards[hidden_hand].ref, serialized
            )
            self.assertNotIn(
                engine.state.cards[hidden_library].ref, serialized
            )
            self.assertIn(public_ref, serialized)
            self.assertNotIn("library_order", serialized)
        authoritative = engine.state.to_dict()
        self.assertIn(hidden_hand, authoritative["cards"])
        self.assertIn(hidden_library, authoritative["cards"])

    def test_ordered_provider_response_flattens_first_action_choices(self):
        response = PilotResponse.from_mapping(
            {
                "actions": [
                    {
                        "action_id": "play-land:A21",
                        "choices": {"pay_life": True},
                    },
                    {"action_id": "cast:A64"},
                ],
                "plan": "DEVELOP_ENGINE",
                "reason": "Develop the untapped source, then cast the engine.",
            }
        )
        payload = response.engine_response()
        self.assertEqual("play-land:A21", payload["action_id"])
        self.assertTrue(payload["pay_life"])
        self.assertEqual("DEVELOP_ENGINE", payload["plan_category"])
        with self.assertRaises(ValueError):
            PilotResponse.from_mapping(
                {
                    "action_id": "pass",
                    "actions": [{"action_id": "pass"}],
                    "plan": "PASS_WITH_YIELD",
                    "reason": "Invalid mutually exclusive response.",
                }
            )

    def test_native_record_has_nonempty_commands_and_command_replay(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=904
        )
        principal = session.pending_principals()[0]
        token = session.packet(principal, full=True)["decision"]["cap"]
        self.assertTrue(
            session.act(
                principal,
                {
                    "action_id": "keep",
                    "plan": "MULLIGAN",
                    "reason": "Keep a functional seven.",
                    "provider": "scripted",
                    "provider_invoked": True,
                    "invocation_id": "native-1",
                    "input_tokens": 10,
                    "output_tokens": 4,
                },
            ).ok
        )
        with tempfile.TemporaryDirectory() as temporary:
            record = Path(temporary) / "native"
            session.save(record)
            manifest = json.loads(
                (record / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                "command_replay", manifest["replay"]["mode"]
            )
            self.assertTrue(
                (record / "commands.jsonl").read_text(encoding="utf-8").strip()
            )
            for path in record.iterdir():
                if path.is_file() and path.suffix != ".gz":
                    self.assertNotIn(
                        token, path.read_text(encoding="utf-8")
                    )
            replay = replay_record(record, self.db, verify=True)
            self.assertTrue(replay["ok"])

    def test_preflight_is_trust_aware_and_fail_closed(self):
        report = semantic_preflight(self.db, self.zimone)
        self.assertEqual(3, report["schema_version"])
        self.assertEqual(100, report["total_cards"])
        self.assertTrue(report["deck_review_eligible_possible"])
        self.assertEqual(0, report["unresolved_cards"])
        by_name = {row["name"]: row for row in report["cards"]}
        self.assertTrue(by_name["Lotus Cobra"]["source_hash_match"])
        self.assertNotIn(
            "Lotus Cobra", report["source_hash_drift_cards"]
        )
        self.assertEqual(
            "fully_playable", by_name["Protean Hulk"]["status"]
        )
        for name in (
            "Golgari Rot Farm",
            "Simic Growth Chamber",
            "Dimir Aqueduct",
        ):
            with self.subTest(superseded_entry_return=name):
                row = by_name[name]
                self.assertEqual("fully_playable", row["status"])
                self.assertNotIn(
                    "trigger:enter",
                    {program["ability_id"] for program in row["programs"]},
                )
                self.assertEqual(
                    ["trigger:front:n2"],
                    [
                        program["ability_id"]
                        for program in row["programs"]
                        if "fixed-entry-return-requirement"
                        in program["semantic_family"]
                    ],
                )


class TrustedSemanticScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @staticmethod
    def _card(engine, seat: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == seat and card.printed_name == name
        )

    @staticmethod
    def _clear_decision(engine):
        engine.permissions.invalidate_current()
        engine.state.priority_player = None

    def test_lotus_cobra_landfall_delegates_mana_choice(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=911
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        cobra = self._card(engine, "B", "Lotus Cobra")
        land = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and engine.card_record(card)
            and engine.card_record(card).is_land
            and card.printed_name != "Field of the Dead"
        )
        engine.move_card(cobra.object_id, "battlefield", controller="B")
        engine.move_card(land.object_id, "battlefield", controller="B")
        queued = engine._dispatch_semantic_event(
            "land.enter", {"card": land.ref, "controller": "B"}
        )
        self.assertTrue(queued)
        engine._prepare_stack_resolution()
        packet = session.packet("pilot:B", full=True)
        self.assertEqual("semantic.choice", packet["decision"]["kind"])
        self.assertTrue(
            session.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "choice": "G",
                    "plan": "DEVELOP_MANA",
                    "reason": "Choose green for the next engine spell.",
                },
            ).ok
        )
        self.assertEqual(1, engine.state.players["B"].mana_pool["G"])

    def test_field_of_the_dead_uses_seven_distinct_land_names(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=912
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        lands = []
        names = set()
        for card in engine.state.cards.values():
            record = engine.card_record(card)
            if (
                card.owner == "B"
                and record
                and record.is_land
                and record.name not in names
            ):
                lands.append(card)
                names.add(record.name)
            if len(lands) == 7:
                break
        field = self._card(engine, "B", "Field of the Dead")
        if field not in lands:
            lands[-1] = field
        for card in lands:
            engine.move_card(card.object_id, "battlefield", controller="B")
        trigger_land = next(card for card in lands if card is not field)
        engine._dispatch_semantic_event(
            "land.enter", {"card": trigger_land.ref, "controller": "B"}
        )
        engine._prepare_stack_resolution()
        zombies = [
            card
            for card in engine.state.cards.values()
            if card.is_token and card.printed_name == "Zombie"
        ]
        self.assertEqual(1, len(zombies))

    def test_zimone_activation_and_second_draw_trigger(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=917
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        zimone = self._card(engine, "B", "Zimone and Dina")
        fodder = self._card(engine, "B", "Birds of Paradise")
        land = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and engine.card_record(card)
            and engine.card_record(card).is_land
            and "when"
            not in engine.card_record(card).oracle_text.casefold()
            and card.object_id not in {zimone.object_id, fodder.object_id}
        )
        engine.move_card(zimone.object_id, "battlefield", controller="B")
        engine.move_card(fodder.object_id, "battlefield", controller="B")
        engine.move_card(land.object_id, "hand")
        zimone.acquired_control_turn_count = (
            engine.state.players["B"].turns_begun - 1
        )
        engine.state.players["B"].stats.setdefault(
            "cards_drawn_by_turn", {}
        )[str(engine.state.turn_sequence)] = 1
        engine.state.priority_player = "B"
        engine._issue_priority("B")
        packet = session.packet("pilot:B", full=True)
        action = next(
            item
            for item in packet["decision"]["legal_actions"]
            if item.get("source") == zimone.ref
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": action["id"],
                "cost_cards": [fodder.ref],
                "plan": "DEVELOP_ENGINE",
                "reason": "Convert recurring fodder into a draw and tapped land.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        choice_packet = session.packet("pilot:B", full=True)
        self.assertEqual(
            "semantic.choice", choice_packet["decision"]["kind"]
        )
        self.assertTrue(
            session.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "card": land.ref,
                    "plan": "DEVELOP_MANA",
                    "reason": "Use the optional land placement from Zimone.",
                },
            ).ok
        )
        self.assertTrue(zimone.tapped)
        self.assertEqual("graveyard", fodder.zone)
        self.assertEqual("battlefield", land.zone)
        self.assertTrue(land.tapped)

        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        target_packet = session.packet("pilot:B", full=True)
        self.assertEqual(
            "semantic.target", target_packet["decision"]["kind"]
        )
        life_a = engine.state.players["A"].life
        life_b = engine.state.players["B"].life
        self.assertTrue(
            session.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "targets": ["A"],
                    "plan": "PRESSURE_PLAYER",
                    "reason": "Resolve the second-card trigger against A.",
                },
            ).ok
        )
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(life_a - 2, engine.state.players["A"].life)
        self.assertEqual(life_b + 2, engine.state.players["B"].life)

    def test_mishra_creates_hasty_warform_and_delayed_sacrifice(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=913
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        mishra = self._card(engine, "A", "Mishra, Eminent One")
        artifact = self._card(engine, "A", "Ichor Wellspring")
        engine.move_card(mishra.object_id, "battlefield", controller="A")
        engine.move_card(artifact.object_id, "battlefield", controller="A")
        engine._dispatch_semantic_event(
            "step.begin",
            {
                "phase": "combat",
                "step": "beginning_combat",
                "player": "A",
            },
        )
        engine._prepare_stack_resolution()
        packet = session.packet("pilot:A", full=True)
        self.assertEqual("semantic.target", packet["decision"]["kind"])
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": [artifact.ref],
                "plan": "DEVELOP_ENGINE",
                "reason": "Target Wellspring for the required hasty 4/4 Warform.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        warform = next(
            card
            for card in engine.state.cards.values()
            if card.is_token and engine.display_name(card.object_id) == "Mishra's Warform"
        )
        data = engine._effective_card_data(warform)
        self.assertEqual("4", data["power"])
        self.assertEqual("4", data["toughness"])
        self.assertIn("Haste", data["keywords"])
        self.assertTrue(
            any(
                trigger.source_object_id == warform.object_id
                and trigger.condition["step"] == "end_step"
                for trigger in engine.state.delayed_triggers
            )
        )

    def test_zulaport_drains_each_opponent_for_simultaneous_deaths(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=4, seed=914
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        cutthroat = self._card(engine, "B", "Zulaport Cutthroat")
        engine.move_card(
            cutthroat.object_id,
            "battlefield",
            controller="B",
        )
        fodder_ref = engine.create_token(
            "B",
            name="Zulaport Fodder",
            characteristics={
                "type_line": "Token Creature",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        fodder = next(
            card
            for card in engine.state.cards.values()
            if card.ref == fodder_ref
        )
        life_before = {
            seat: player.life
            for seat, player in engine.state.players.items()
        }

        engine._move_cards_simultaneously(
            [
                (cutthroat.object_id, "graveyard"),
                (fodder.object_id, "graveyard"),
            ],
            reason="Zulaport scenario",
        )
        self.assertTrue(engine._stabilize())
        packet = session.packet("pilot:B", full=True)
        self.assertEqual("trigger.order", packet["decision"]["kind"])
        trigger_refs = [
            item["id"]
            for item in packet["decision"]["ctx"]["triggers"]
        ]
        self.assertTrue(
            session.act(
                "pilot:B",
                {
                    "action_id": "order",
                    "triggers": trigger_refs,
                    "plan": "DEVELOP_ENGINE",
                    "reason": (
                        "Place both simultaneous Cutthroat triggers on "
                        "the stack in a deterministic order."
                    ),
                },
            ).ok
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None

        self.assertEqual(
            2,
            sum(
                item.label == "Zulaport Cutthroat dies trigger"
                for item in engine.state.stack
            ),
        )
        while engine.state.stack:
            engine.permissions.invalidate_current()
            engine.state.pending_decision = None
            engine.state.priority_player = None
            engine._prepare_stack_resolution()
        self.assertEqual(
            life_before["B"] + 2,
            engine.state.players["B"].life,
        )
        for seat in ("A", "C", "D"):
            self.assertEqual(
                life_before[seat] - 2,
                engine.state.players[seat].life,
            )

    def test_gonti_heart_energy_cost_and_extra_turn(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=916
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        heart = self._card(engine, "A", "Gonti's Aether Heart")
        engine.move_card(
            heart.object_id,
            "battlefield",
            controller="A",
        )
        ability = next(
            ability
            for ability in engine._activated_abilities(heart)
            if ability.energy_payment
        )
        self.assertEqual(8, ability.energy_payment)
        self.assertTrue(ability.exile_source)
        engine.create_token(
            "A",
            name="Gonti Trigger Artifact",
            characteristics={"type_line": "Token Artifact"},
        )
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(2, engine.state.players["A"].energy)
        engine.state.players["A"].energy = 8
        engine.state.priority_player = "A"

        engine._activate(
            "A",
            {
                "source": heart.ref,
                "ability": ability.ability_id,
            },
        )

        self.assertEqual(0, engine.state.players["A"].energy)
        self.assertEqual("exile", heart.zone)
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual(1, len(engine.state.extra_turns))
        self.assertEqual("A", engine.state.extra_turns[0].player)

    def test_red_elemental_blast_template_targets_zimone(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=914
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        zimone = self._card(engine, "B", "Zimone and Dina")
        reb = self._card(engine, "A", "Red Elemental Blast")
        engine.move_card(zimone.object_id, "battlefield", controller="B")
        engine.move_card(reb.object_id, "hand")
        engine.state.players["A"].mana_pool["R"] = 1
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        packet = session.packet("pilot:A", full=True)
        action = next(
            item
            for item in packet["decision"]["legal_actions"]
            if item.get("card") == reb.ref
        )
        self.assertIn(zimone.ref, action["target_schema"]["legal_refs"])
        result = session.act(
            "pilot:A",
            {
                "action_id": action["id"],
                "modes": ["destroy"],
                "targets": [zimone.ref],
                "pay": "manual",
                "payment": {"R": 1},
                "plan": "HOLD_INTERACTION",
                "reason": "REB can legally destroy the blue Zimone and Dina permanent.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        engine.permissions.invalidate_current()
        engine.state.priority_player = None
        engine._prepare_stack_resolution()
        self.assertEqual("graveyard", zimone.zone)

    def test_shortcuts_validate_sequence_priority_and_energy(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=915
        )
        keep_all(session)
        engine = session.engine
        self._clear_decision(engine)
        soul = self._card(engine, "B", "Warren Soultrader")
        crawler = self._card(engine, "B", "Gravecrawler")
        cutthroat = self._card(engine, "B", "Zulaport Cutthroat")
        for card in (soul, cutthroat):
            engine.move_card(card.object_id, "battlefield", controller="B")
        engine.move_card(crawler.object_id, "graveyard")
        engine.state.players["B"].mana_pool["B"] = 1
        life_before = engine.state.players["A"].life
        aggregate = engine.apply_shortcut(
            "B",
            {
                "signature": "soultrader-gravecrawler-zulaport",
                "sequence": [
                    f"cast:{crawler.ref}",
                    f"activate:{soul.ref}:ab1",
                ],
                "repeat_count": 3,
                "stop_condition": "repeat N times",
                "opponent_responses": [{"seat": "A", "response": "pass"}],
            },
        )
        self.assertEqual(life_before - 3, engine.state.players["A"].life)
        self.assertEqual(0, aggregate["controller_life_delta"])

        mishra = self._card(engine, "A", "Mishra, Eminent One")
        heart = self._card(engine, "A", "Gonti's Aether Heart")
        engine.move_card(mishra.object_id, "battlefield", controller="A")
        engine.move_card(heart.object_id, "battlefield", controller="A")
        energy = engine.apply_shortcut(
            "A",
            {
                "signature": "mishra-gonti-heart",
                "sequence": ["trigger:mishra-warform:gonti-heart"],
                "repeat_count": 1,
                "take_extra_turn": False,
                "opponent_responses": [{"seat": "B", "response": "pass"}],
            },
        )
        self.assertEqual(4, energy["energy_gained"])
        self.assertFalse(energy["infinite"])
        extra_turn = engine.apply_shortcut(
            "A",
            {
                "signature": "mishra-gonti-heart",
                "sequence": ["trigger:mishra-warform:gonti-heart"],
                "repeat_count": 1,
                "take_extra_turn": True,
                "stop_condition": "spend exactly eight energy",
                "opponent_responses": [{"seat": "B", "response": "pass"}],
            },
        )
        self.assertTrue(extra_turn["extra_turn_scheduled"])
        self.assertEqual(0, extra_turn["energy_remaining"])
        self.assertEqual("exile", heart.zone)
        self.assertEqual(1, len(engine.state.extra_turns))
        with self.assertRaises(ValueError):
            engine.apply_shortcut(
                "A",
                {
                    "signature": "mishra-gonti-heart",
                    "sequence": ["trigger:mishra-warform:gonti-heart"],
                    "repeat_count": 0,
                    "opponent_responses": [{"seat": "B", "response": "pass"}],
                },
            )

    def test_review_markdown_uses_cards_payment_and_decision_reason(self):
        session = make_session(
            self.db, self.mishra, self.zimone, players=2, seed=916
        )
        principal = session.pending_principals()[0]
        self.assertTrue(
            session.act(
                principal,
                {
                    "action_id": "keep",
                    "plan": "MULLIGAN",
                    "reason": "Keep exact early color access.",
                    "provider": "scripted",
                    "provider_invoked": True,
                },
            ).ok
        )
        review = derive_review(
            session.engine,
            decisions=session.decisions,
            manifest={"replay": {"verification": "not_run"}},
        )
        markdown = review_markdown(review)
        self.assertIn("Provider calls observed", markdown)
        self.assertNotIn("Model-call estimate: observed", markdown)


if __name__ == "__main__":
    unittest.main()
