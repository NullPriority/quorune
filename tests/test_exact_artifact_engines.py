from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.preflight import card_semantic_status


class ExactArtifactEngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 2):
        session = make_session(
            self.db,
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
        return session

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

    @staticmethod
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_sai_cast_trigger_and_two_artifact_draw_cost(self):
        session = self.make_session(900)
        engine = session.engine
        sai = self.card(engine, "A", "Sai, Master Thopterist")
        sol_ring = self.card(engine, "A", "Sol Ring")
        idol = self.card(engine, "A", "Idol of Oblivion")
        engine.move_card(sai.object_id, "battlefield", controller="A")
        engine.move_card(sol_ring.object_id, "hand")
        engine.move_card(idol.object_id, "battlefield", controller="A")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.priority_player = "A"

        engine._cast("A", {"card": sol_ring.ref, "pay": "auto"})
        self.assertFalse(engine.state.pending_trigger_batches)
        self.assertEqual(
            "Sai artifact-cast trigger", engine.state.stack[-1].label
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual("Sai artifact-cast trigger", engine.state.stack[-1].label)
        self.resolve_top(engine)
        thopters = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.controller == "A"
            and card.printed_name == "Thopter"
            and card.zone == "battlefield"
        ]
        self.assertEqual(1, len(thopters))

        before_hand = len(engine.state.players["A"].zones["hand"])
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": sai.ref,
                "ability": "ab2",
                "cost_cards": [idol.ref, thopters[0].ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual(
            before_hand + 1,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertEqual("graveyard", idol.zone)
        self.assertEqual("outside", thopters[0].zone)

    def test_padeem_hexproof_and_intervening_upkeep_condition(self):
        session = self.make_session(901)
        engine = session.engine
        padeem = self.card(engine, "A", "Padeem, Consul of Innovation")
        sol_ring = self.card(engine, "A", "Sol Ring")
        portal = self.card(engine, "A", "Portal to Phyrexia")
        engine.move_card(padeem.object_id, "battlefield", controller="A")
        engine.move_card(sol_ring.object_id, "battlefield", controller="A")
        engine.move_card(portal.object_id, "battlefield", controller="B")
        self.assertIn("Hexproof", engine._effective_card_data(sol_ring)["keywords"])
        self.assertNotIn("Hexproof", engine._effective_card_data(portal)["keywords"])

        event = {"phase": "beginning", "step": "upkeep", "player": "A"}
        engine._dispatch_semantic_event("step.begin", event)
        self.assertFalse(engine.state.pending_trigger_batches)

        engine.move_card(portal.object_id, "hand")
        engine._dispatch_semantic_event("step.begin", event)
        self.assertFalse(engine._stabilize())
        engine.move_card(portal.object_id, "battlefield", controller="B")
        before_hand = len(engine.state.players["A"].zones["hand"])
        self.resolve_top(engine)
        self.assertEqual(
            before_hand,
            len(engine.state.players["A"].zones["hand"]),
        )
        self.assertFalse(engine.state.stack)

    def test_marionette_apprentice_fabricate_and_graveyard_trigger(self):
        session = self.make_session(902)
        engine = session.engine
        apprentice = self.card(engine, "A", "Marionette Apprentice")
        sol_ring = self.card(engine, "A", "Sol Ring")
        engine.move_card(sol_ring.object_id, "battlefield", controller="A")
        engine.move_card(
            apprentice.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "choice": "token",
                "plan": "DEVELOP_BOARD",
                "reason": "Create the artifact creature token.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        servos = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.printed_name == "Servo"
            and card.zone == "battlefield"
        ]
        self.assertEqual(1, len(servos))

        before_b = engine.state.players["B"].life
        engine.move_card(
            sol_ring.object_id,
            "graveyard",
            semantic_events=True,
            reason="artifact sacrificed",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(before_b - 1, engine.state.players["B"].life)

    def test_portal_apnap_sacrifice_and_upkeep_reanimation(self):
        session = self.make_session(903)
        engine = session.engine
        portal = self.card(engine, "A", "Portal to Phyrexia")
        victim_one = self.card(engine, "B", "Zimone and Dina")
        victim_two = self.card(engine, "B", "Deathrite Shaman")
        for victim in (victim_one, victim_two):
            engine.move_card(victim.object_id, "battlefield", controller="B")
        engine.move_card(
            portal.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "cards": [victim_one.ref, victim_two.ref],
                "plan": "MINIMIZE_LOSS",
                "reason": "Sacrifice every creature controlled.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", victim_one.zone)
        self.assertEqual("graveyard", victim_two.zone)
        self.assertEqual(
            "state.commander_zone", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:B",
            {"a": "choose", "choice": "remain"},
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            "semantic.target", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": [victim_one.ref],
                "plan": "RECUR_VALUE",
                "reason": "Return the commander as a Phyrexian.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("battlefield", victim_one.zone)
        self.assertEqual("A", victim_one.controller)
        self.assertIn(
            "Phyrexian",
            engine._effective_card_data(victim_one)["type_line"],
        )

    def test_arcum_sacrifices_target_controller_artifact_and_searches(self):
        session = self.make_session(904)
        engine = session.engine
        arcum = self.card(engine, "A", "Arcum Dagsson")
        target = self.card(engine, "B", "Sol Ring")
        engine.move_card(arcum.object_id, "battlefield", controller="A")
        arcum.acquired_control_turn_count = -1
        token_ref = engine.create_token(
            "B",
            name="Assembly-Worker",
            characteristics={
                "type_line": "Artifact Creature — Assembly-Worker",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        token = engine._resolve_object("B", token_ref)
        engine.move_card(target.object_id, "library")
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": arcum.ref,
                "ability": "ab1",
                "targets": [token.ref],
            },
        )
        self.resolve_top(engine)
        packet = session.packet("pilot:B", full=True)
        self.assertEqual("semantic.search", packet["decision"]["kind"])
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "FIND_ENGINE",
                "reason": "Find the noncreature artifact.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("outside", token.zone)
        self.assertEqual("battlefield", target.zone)
        self.assertEqual("B", target.controller)

    def test_goblin_welder_requires_same_player_and_both_legal(self):
        session = self.make_session(905)
        engine = session.engine
        welder = self.card(engine, "A", "Goblin Welder")
        battlefield_artifact = self.card(engine, "A", "Sol Ring")
        graveyard_artifact = self.card(engine, "A", "Ichor Wellspring")
        opposing_artifact = self.card(engine, "B", "Sol Ring")
        for card, controller in (
            (welder, "A"),
            (battlefield_artifact, "A"),
            (opposing_artifact, "B"),
        ):
            engine.move_card(card.object_id, "battlefield", controller=controller)
        engine.move_card(graveyard_artifact.object_id, "graveyard")
        welder.acquired_control_turn_count = -1
        engine.state.priority_player = "A"
        hints = engine._priority_action_hints("A")
        self.assertIn(
            f"activate:{welder.ref}:ab1",
            {action["id"] for action in hints["actions"]},
        )
        with self.assertRaisesRegex(Exception, "same player"):
            engine._activate(
                "A",
                {
                    "source": welder.ref,
                    "ability": "ab1",
                    "targets": [
                        {
                            "group": "battlefield_artifact",
                            "ref": opposing_artifact.ref,
                        },
                        {
                            "group": "graveyard_artifact",
                            "ref": graveyard_artifact.ref,
                        },
                    ],
                },
            )

        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": welder.ref,
                "ability": "ab1",
                "targets": [
                    {
                        "group": "battlefield_artifact",
                        "ref": battlefield_artifact.ref,
                    },
                    {
                        "group": "graveyard_artifact",
                        "ref": graveyard_artifact.ref,
                    },
                ],
            },
        )
        self.resolve_top(engine)
        self.assertEqual("graveyard", battlefield_artifact.zone)
        self.assertEqual("battlefield", graveyard_artifact.zone)

    def test_repurposing_bay_binds_sacrificed_mana_value(self):
        session = self.make_session(906)
        engine = session.engine
        bay = self.card(engine, "A", "Repurposing Bay")
        sol_ring = self.card(engine, "A", "Sol Ring")
        target = self.card(engine, "A", "Ichor Wellspring")
        wrong_value = self.card(engine, "A", "Panharmonicon")
        engine.move_card(bay.object_id, "battlefield", controller="A")
        engine.move_card(sol_ring.object_id, "battlefield", controller="A")
        engine.move_card(target.object_id, "library")
        engine.move_card(wrong_value.object_id, "library")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool["C"] = 2
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": bay.ref,
                "ability": "ab1",
                "cost_cards": [sol_ring.ref],
                "pay": "manual",
                "payment": {"C": 2},
            },
        )
        self.resolve_top(engine)
        packet = session.packet("pilot:A", full=True)
        search_refs = {
            row["id"]
            for row in packet["decision"]["ctx"]["search_cards"]
        }
        self.assertIn(target.ref, search_refs)
        self.assertNotIn(wrong_value.ref, search_refs)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "FIND_ENGINE",
                "reason": "Find the exact mana-value artifact.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", sol_ring.zone)
        self.assertEqual("battlefield", target.zone)

    def test_panharmonicon_adds_one_matching_enter_trigger(self):
        session = self.make_session(907)
        engine = session.engine
        panharmonicon = self.card(engine, "A", "Panharmonicon")
        wellspring = self.card(engine, "A", "Ichor Wellspring")
        engine.move_card(
            panharmonicon.object_id,
            "battlefield",
            controller="A",
        )
        engine.move_card(
            wellspring.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
        )
        queued = [
            item
            for batch in engine.state.pending_trigger_batches
            for group in batch["groups"]
            for item in group["items"]
            if item.get("source_object_id") == wellspring.object_id
            and item.get("semantic_key") is not None
        ]
        self.assertEqual(2, len(queued))
        self.assertEqual(1, len({item["semantic_key"] for item in queued}))
        program = engine.semantics.get(queued[0]["semantic_key"])
        self.assertIsNotNone(program)
        assert program is not None
        self.assertIn("fixed_multi_event_subscription", program.coverage)

    def test_brudiclad_creates_myr_and_copies_other_tokens(self):
        session = self.make_session(908)
        engine = session.engine
        brudiclad = self.card(
            engine, "A", "Brudiclad, Telchor Engineer"
        )
        engine.move_card(
            brudiclad.object_id,
            "battlefield",
            controller="A",
        )
        treasure_ref = engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Artifact — Treasure"},
        )[0]
        engine._dispatch_semantic_event(
            "step.begin",
            {
                "phase": "combat",
                "step": "beginning_combat",
                "player": "A",
            },
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        packet = session.packet("pilot:A", full=True)
        myr_ref = next(
            value
            for value in packet["decision"]["ctx"]["options"]
            if value != treasure_ref
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": myr_ref,
                "plan": "BUILD_BOARD",
                "reason": "Make every other token a 2/1 Myr.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        treasure = engine._resolve_object(
            "A", treasure_ref, zones={"battlefield"}
        )
        copied = engine._effective_card_data(treasure)
        self.assertEqual("Phyrexian Myr", copied["name"])
        self.assertIn("Artifact Creature", copied["type_line"])
        self.assertEqual("2", copied["power"])
        self.assertIn("Haste", copied["keywords"])

    def test_determined_iteration_populates_with_haste_and_delayed_sacrifice(self):
        session = self.make_session(909)
        engine = session.engine
        iteration = self.card(engine, "A", "Determined Iteration")
        engine.move_card(
            iteration.object_id,
            "battlefield",
            controller="A",
        )
        original_ref = engine.create_token(
            "A",
            name="Bear",
            characteristics={
                "type_line": "Creature — Bear",
                "power": "2",
                "toughness": "2",
            },
        )[0]
        engine._dispatch_semantic_event(
            "step.begin",
            {
                "phase": "combat",
                "step": "beginning_combat",
                "player": "A",
            },
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "card": original_ref,
                "plan": "BUILD_BOARD",
                "reason": "Populate the Bear for combat.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        bears = [
            card
            for card in engine.state.cards.values()
            if card.is_token
            and card.zone == "battlefield"
            and engine._effective_card_data(card)["name"] == "Bear"
        ]
        self.assertEqual(2, len(bears))
        created = next(card for card in bears if card.ref != original_ref)
        self.assertIn(
            "Haste", engine._effective_card_data(created)["keywords"]
        )

        delayed = engine._matching_delayed_triggers(
            "step.begin",
            {"phase": "ending", "step": "end_step", "player": "A"},
        )
        self.assertEqual(1, len(delayed))
        engine._start_trigger_batch(delayed, after="grant_priority")
        self.resolve_top(engine)
        self.assertEqual("outside", created.zone)
        self.assertEqual("battlefield", engine._resolve_object("A", original_ref).zone)

    def test_lightning_greaves_equip_grants_haste_and_shroud(self):
        session = self.make_session(910)
        engine = session.engine
        greaves = self.card(engine, "A", "Lightning Greaves")
        mishra = self.card(engine, "A", "Mishra, Eminent One")
        engine.move_card(greaves.object_id, "battlefield", controller="A")
        engine.move_card(mishra.object_id, "battlefield", controller="A")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": greaves.ref,
                "ability": "ab2",
                "targets": [mishra.ref],
            },
        )
        self.resolve_top(engine)
        self.assertEqual(mishra.object_id, greaves.attached_to)
        self.assertIn(greaves.object_id, mishra.attachments)
        keywords = engine._effective_card_data(mishra)["keywords"]
        self.assertIn("Haste", keywords)
        self.assertIn("Shroud", keywords)

    def test_equip_resolves_without_effect_after_equipment_leaves(self):
        session = self.make_session(9101)
        engine = session.engine
        greaves = self.card(engine, "A", "Lightning Greaves")
        mishra = self.card(engine, "A", "Mishra, Eminent One")
        engine.move_card(greaves.object_id, "battlefield", controller="A")
        engine.move_card(mishra.object_id, "battlefield", controller="A")
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": greaves.ref,
                "ability": "ab2",
                "targets": [mishra.ref],
            },
        )

        engine.move_card(greaves.object_id, "graveyard")
        self.resolve_top(engine)

        self.assertEqual("graveyard", greaves.zone)
        self.assertIsNone(greaves.attached_to)
        self.assertNotIn(greaves.object_id, mishra.attachments)
        self.assertFalse(engine.state.stack)
        self.assertTrue(
            any(
                event.code == "attachment.no_effect"
                and event.details.get("result")
                == "equipment_not_on_battlefield"
                for event in engine.state.events
            )
        )

    def test_skullclamp_modifier_and_attached_death_draw(self):
        session = self.make_session(911)
        engine = session.engine
        skullclamp = self.card(engine, "B", "Skullclamp")
        engine.move_card(
            skullclamp.object_id,
            "battlefield",
            controller="B",
        )
        token_ref = engine.create_token(
            "B",
            name="Saproling",
            characteristics={
                "type_line": "Creature — Saproling",
                "power": "1",
                "toughness": "1",
            },
        )[0]
        token = engine._resolve_object("B", token_ref)
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["B"].mana_pool["C"] = 1
        engine.state.priority_player = "B"
        before_hand = len(engine.state.players["B"].zones["hand"])
        engine._activate(
            "B",
            {
                "source": skullclamp.ref,
                "ability": "ab3",
                "targets": [token.ref],
                "pay": "manual",
                "payment": {"C": 1},
            },
        )
        self.resolve_top(engine)
        self.assertEqual("outside", token.zone)
        self.assertEqual(
            "Skullclamp equipped-creature death trigger",
            engine.state.stack[-1].label,
        )
        self.resolve_top(engine)
        self.assertEqual(
            before_hand + 2,
            len(engine.state.players["B"].zones["hand"]),
        )

    def test_artifact_engine_cards_pass_exact_semantic_preflight(self):
        for name in (
            "Arcum Dagsson",
            "Sai, Master Thopterist",
            "Padeem, Consul of Innovation",
            "Marionette Apprentice",
            "Portal to Phyrexia",
            "Goblin Welder",
            "Repurposing Bay",
            "Panharmonicon",
            "Brudiclad, Telchor Engineer",
            "Determined Iteration",
            "Lightning Greaves",
            "Skullclamp",
        ):
            with self.subTest(card=name):
                record = self.db.lookup(name)
                row = card_semantic_status(
                    record,
                    self.make_session(920).engine.semantics,
                    db=self.db,
                )
                self.assertEqual(
                    "fully_playable",
                    row["status"],
                    row["unresolved"],
                )


if __name__ == "__main__":
    unittest.main()
