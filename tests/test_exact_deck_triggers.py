from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session


class ExactDeckTriggerFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int, *, players: int = 4):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=players,
            seed=seed,
        )
        keep_all(session)
        session.engine.permissions.invalidate_current()
        session.state.pending_decision = None
        session.state.priority_player = None
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

    def test_ichor_wellspring_draws_on_enter_and_graveyard(self):
        session = self.make_session(820, players=2)
        engine = session.engine
        wellspring = self.card(engine, "A", "Ichor Wellspring")
        before = len(engine.state.players["A"].draw_history)

        engine.move_card(
            wellspring.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="Ichor enters scenario",
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(1, len(engine.state.stack))
        trigger_key = engine.state.stack[0].semantic_key
        program = engine.semantics.get(trigger_key or "")
        self.assertIsNotNone(program)
        assert program is not None
        self.assertIn("fixed_multi_event_subscription", program.coverage)
        self.resolve_top(engine)
        self.assertEqual(
            before + 1,
            len(engine.state.players["A"].draw_history),
        )

        engine.apply_effect(
            {"op": "sacrifice", "card": wellspring.ref},
            actor="A",
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(1, len(engine.state.stack))
        self.assertEqual(trigger_key, engine.state.stack[0].semantic_key)
        self.resolve_top(engine)
        self.assertEqual(
            before + 2,
            len(engine.state.players["A"].draw_history),
        )

    def test_bastion_enter_and_multiplayer_death_triggers(self):
        session = self.make_session(821)
        engine = session.engine
        bastion = self.card(engine, "B", "Bastion of Remembrance")
        life_before = {
            seat: player.life
            for seat, player in engine.state.players.items()
        }

        engine.move_card(
            bastion.object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="Bastion enters scenario",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        soldier = next(
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.is_token
            and card.printed_name == "Human Soldier Token"
        )
        data = engine._effective_card_data(soldier)
        self.assertEqual("1", data["power"])
        self.assertEqual("1", data["toughness"])
        self.assertIn("creature", data["type_line"].casefold())

        engine.apply_effect(
            {"op": "sacrifice", "card": soldier.ref},
            actor="B",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            life_before["B"] + 1,
            engine.state.players["B"].life,
        )
        for seat in ("A", "C", "D"):
            self.assertEqual(
                life_before[seat] - 1,
                engine.state.players[seat].life,
            )

    def test_reckless_fireweaver_damages_each_opponent(self):
        session = self.make_session(822)
        engine = session.engine
        fireweaver = self.card(engine, "A", "Reckless Fireweaver")
        engine.move_card(
            fireweaver.object_id,
            "battlefield",
            controller="A",
        )
        life_before = {
            seat: player.life
            for seat, player in engine.state.players.items()
        }

        engine.create_token(
            "A",
            name="Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(
            [fireweaver.object_id],
            [item.source_object_id for item in engine.state.stack],
        )
        self.resolve_top(engine)
        self.assertEqual(life_before["A"], engine.state.players["A"].life)
        for seat in ("B", "C", "D"):
            self.assertEqual(
                life_before[seat] - 1,
                engine.state.players[seat].life,
            )

        engine.create_token(
            "B",
            name="Opponent Treasure",
            characteristics={"type_line": "Token Artifact — Treasure"},
        )
        self.assertFalse(engine._stabilize())
        self.assertFalse(engine.state.stack)

    def test_spine_destroys_target_then_returns_from_graveyard(self):
        session = self.make_session(823, players=2)
        engine = session.engine
        spine = self.card(engine, "A", "Spine of Ish Sah")
        target = self.card(engine, "B", "Sol Ring")
        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
        )

        engine.move_card(
            spine.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="Spine enters scenario",
        )
        self.assertTrue(engine._stabilize())
        self.assertEqual(
            "semantic.target", engine.state.pending_decision.kind
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "targets": [target.ref],
                "plan": "DISRUPT_ENGINE",
                "reason": "Destroy the opposing mana artifact.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("graveyard", target.zone)

        engine.apply_effect(
            {"op": "sacrifice", "card": spine.ref},
            actor="A",
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(
            ["Spine of Ish Sah — When this artifact is put into a graveyard from the battlefield, return it to its owner's hand."],
            [item.label for item in engine.state.stack],
        )
        self.resolve_top(engine)
        self.assertEqual("hand", spine.zone)

    def test_cryogen_relic_draws_on_enter_and_leave_and_stuns_tapped_creature(
        self,
    ):
        session = self.make_session(824, players=2)
        engine = session.engine
        relic = self.card(engine, "A", "Cryogen Relic")
        target = self.card(engine, "B", "Zimone and Dina")
        before = len(engine.state.players["A"].draw_history)

        engine.move_card(
            relic.object_id,
            "battlefield",
            controller="A",
            semantic_events=True,
            reason="Cryogen enters scenario",
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual(1, len(engine.state.stack))
        program = engine.semantics.get(
            engine.state.stack[0].semantic_key or ""
        )
        self.assertIsNotNone(program)
        assert program is not None
        self.assertIn("fixed_multi_event_subscription", program.coverage)
        self.resolve_top(engine)
        self.assertEqual(
            before + 1,
            len(engine.state.players["A"].draw_history),
        )

        engine.move_card(
            target.object_id,
            "battlefield",
            controller="B",
            tapped=True,
        )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": relic.ref,
                "ability": "ab2",
                "targets": [target.ref],
                "pay": "manual",
                "payment": {"C": 1, "U": 1},
            },
        )
        self.assertEqual("graveyard", relic.zone)

        self.resolve_top(engine)
        self.assertEqual(
            before + 2,
            len(engine.state.players["A"].draw_history),
        )
        self.resolve_top(engine)
        self.assertEqual(1, target.counters["stun"])
        engine.apply_effect(
            {"op": "untap", "card": target.ref},
            actor="B",
        )
        self.assertTrue(target.tapped)
        self.assertNotIn("stun", target.counters)
        engine.apply_effect(
            {"op": "untap", "card": target.ref},
            actor="B",
        )
        self.assertFalse(target.tapped)

    def test_ophiomancer_checks_each_upkeep_and_existing_snakes(self):
        session = self.make_session(825, players=2)
        engine = session.engine
        ophiomancer = self.card(engine, "B", "Ophiomancer")
        engine.move_card(
            ophiomancer.object_id,
            "battlefield",
            controller="B",
        )

        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        snakes = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and "snake"
            in engine._type_parts(
                str(
                    engine._effective_card_data(card).get("type_line")
                    or ""
                )
            )[1]
        ]
        self.assertEqual(1, len(snakes))
        self.assertIn(
            "Deathtouch",
            engine._effective_card_data(snakes[0])["keywords"],
        )

        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "B"},
        )
        self.assertFalse(engine._stabilize())
        self.assertFalse(engine.state.stack)

        engine.apply_effect(
            {"op": "sacrifice", "card": snakes[0].ref},
            actor="B",
        )
        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        self.assertEqual(
            1,
            sum(
                card.controller == "B"
                and card.zone == "battlefield"
                and "snake"
                in engine._type_parts(
                    str(
                        engine._effective_card_data(card).get(
                            "type_line"
                        )
                        or ""
                    )
                )[1]
                for card in engine.state.cards.values()
            ),
        )

    def test_tireless_provisioner_landfall_chooses_food_or_treasure(self):
        session = self.make_session(826, players=2)
        engine = session.engine
        provisioner = self.card(engine, "B", "Tireless Provisioner")
        engine.move_card(
            provisioner.object_id,
            "battlefield",
            controller="B",
        )

        for land_name, choice in (
            ("Island", "treasure"),
            ("Bayou", "food"),
        ):
            land = self.card(engine, "B", land_name)
            engine.move_card(
                land.object_id,
                "battlefield",
                controller="B",
                semantic_events=True,
                reason="Provisioner landfall scenario",
            )
            self.assertFalse(engine._stabilize())
            self.assertEqual(f'{provisioner.oracle_id}:trigger:landfall', engine.state.stack[-1].semantic_key)
            self.resolve_top(engine)
            packet = session.packet("pilot:B", full=True)
            self.assertEqual(
                {"food", "treasure"},
                {
                    option["id"]
                    for option in packet["decision"]["ctx"]["options"]
                },
            )
            result = session.act(
                "pilot:B",
                {
                    "action_id": "choose",
                    "choice": choice,
                    "plan": "DEVELOP_MANA",
                    "reason": f"Choose the {choice} token.",
                },
            )
            self.assertTrue(result.ok, result.summary)

        tokens = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and card.is_token
        ]
        self.assertEqual({"Food Token", "Treasure Token"}, {card.printed_name for card in tokens})
        for token in tokens:
            self.assertEqual(token.printed_name, engine.display_name(token.object_id))
            self.assertEqual(token.printed_name, engine._copyable_characteristics(token)["name"])

    def test_provisioner_native_choice_retains_current_and_pinned_names(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common import ROOT
        from quorune import CommanderSession
        from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
        from quorune.semantics import SemanticRegistry
        from quorune.turn_step_owner import TURN_STEPS

        historical = SemanticRegistry(include_builtin_packs=False,
            pack_paths=[ROOT / "quorune/semantic_packs/v080-exact-triggers.json"])
        for legacy, choice in ((False, "food"), (True, "treasure")):
            with self.subTest(legacy=legacy), TemporaryDirectory() as directory:
                session = self.make_session(1114011 + legacy)
                engine = session.engine
                source = self.card(engine, "B", "Tireless Provisioner")
                land = self.card(engine, "B", "Island")
                key = f"{source.oracle_id}:trigger:landfall"
                if legacy:
                    engine.semantics.put(historical.get(key))
                expected_version = 1 if legacy else 2
                self.assertEqual(expected_version, engine.semantics.get(key).version)
                engine.move_card(source.object_id, "battlefield", controller="B", log=False)
                engine.move_card(land.object_id, "hand", log=False)
                engine.state.active_player = engine.state.last_normal_turn_player = "B"
                engine.state.phase, engine.state.step = "precombat_main", "main"
                engine.state.phase_index = TURN_STEPS.index(("precombat_main", "main"))
                engine.state.priority_passes = []
                engine._grant_priority("B")
                engine.pump()
                session.initial_checkpoint = checkpoint_envelope(session.state)
                session.commands.clear()
                session.decisions.clear()

                land_action = f"play-land:{land.ref}"
                offered = session.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                self.assertIn(land_action, {row["id"] for row in offered})
                result = session.act("pilot:B", {"action_id": land_action})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(key, session.state.stack[-1].semantic_key)
                for _ in range(len(engine.seats)):
                    result = session.act(session.pending_principals()[0], {"action_id": "pass"})
                    self.assertTrue(result.ok, result.summary)
                self.assertEqual("semantic.choice", session.state.pending_decision.kind)
                self.assertFalse(any(card.is_token for card in session.state.cards.values()))
                self.assertEqual({"food", "treasure"}, {row["id"] for row in
                    session.packet("pilot:B", full=True)["decision"]["ctx"]["options"]})

                path = Path(directory) / "provisioner-choice"
                session.save(path)
                resumed = CommanderSession.load(self.db, path)
                self.assertEqual(expected_version, resumed.engine.semantics.get(key).version)
                command = {"action_id": "choose", "choice": choice}
                before = authoritative_state_hash(resumed.state)
                self.assertFalse(resumed.act("pilot:A", command).ok)
                self.assertEqual(before, authoritative_state_hash(resumed.state))
                result = resumed.act("pilot:B", command)
                self.assertTrue(result.ok, result.summary)
                token, = [card for card in resumed.state.cards.values() if card.is_token and card.zone == "battlefield"]
                expected_name = choice.title() if legacy else f"{choice.title()} Token"
                self.assertEqual(expected_name, token.printed_name)
                self.assertEqual(expected_name, resumed.engine.display_name(token.object_id))
                self.assertEqual(expected_name, resumed.engine._copyable_characteristics(token)["name"])
                resumed.save(path)
                replay = replay_record(path, self.db, verify=True)
                self.assertTrue(replay["ok"], replay)
                self.assertEqual(authoritative_state_hash(resumed.state), replay["final_state_hash"])

    def test_bloodghast_landfall_return_block_restriction_and_conditional_haste(
        self,
    ):
        session = self.make_session(827, players=2)
        engine = session.engine
        bloodghast = self.card(engine, "B", "Bloodghast")
        land = self.card(engine, "B", "Island")
        attacker = self.card(engine, "A", "Mishra, Eminent One")
        engine.move_card(bloodghast.object_id, "graveyard")
        engine.move_card(
            attacker.object_id,
            "battlefield",
            controller="A",
        )

        engine.move_card(
            land.object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="Bloodghast landfall scenario",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "choice": "return",
                "plan": "DEVELOP_ENGINE",
                "reason": "Return Bloodghast as reusable sacrifice fodder.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", bloodghast.zone)
        self.assertFalse(engine._can_block(attacker, bloodghast)[0])
        self.assertNotIn(
            "Haste",
            engine._effective_card_data(bloodghast)["keywords"],
        )

        engine.state.players["A"].life = 10
        self.assertIn(
            "Haste",
            engine._effective_card_data(bloodghast)["keywords"],
        )

    def test_scute_swarm_landfall_switches_to_copy_at_six_lands(self):
        session = self.make_session(828, players=2)
        engine = session.engine
        scute = self.card(engine, "B", "Scute Swarm")
        engine.move_card(
            scute.object_id,
            "battlefield",
            controller="B",
        )
        lands = [
            card
            for card in engine.state.cards.values()
            if card.owner == "B"
            and (record := engine.card_record(card))
            and record.is_land
            and "when" not in record.oracle_text.casefold()
        ]
        for land in lands[:2]:
            engine.move_card(
                land.object_id,
                "battlefield",
                controller="B",
            )
        engine.move_card(
            lands[2].object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="Scute low-land scenario",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        insects = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and card.is_token
            and card.printed_name == "Insect Token"
        ]
        self.assertEqual(1, len(insects))

        for land in lands[3:5]:
            engine.move_card(
                land.object_id,
                "battlefield",
                controller="B",
            )
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine.move_card(
            lands[5].object_id,
            "battlefield",
            controller="B",
            semantic_events=True,
            reason="Scute six-land scenario",
        )
        self.assertFalse(engine._stabilize())
        self.resolve_top(engine)
        copies = [
            card
            for card in engine.state.cards.values()
            if card.controller == "B"
            and card.zone == "battlefield"
            and card.is_token
            and card.oracle_id == scute.oracle_id
        ]
        self.assertEqual(1, len(copies))
        self.assertEqual(
            engine._effective_card_data(scute)["oracle_text"],
            engine._effective_card_data(copies[0])["oracle_text"],
        )


if __name__ == "__main__":
    unittest.main()
