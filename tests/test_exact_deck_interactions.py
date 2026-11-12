from __future__ import annotations

import json
import unittest

from common import keep_all, load_assets, make_session
from quorune.model import StackItem
from quorune.rules.activation import activation_condition_status


class ExactDeckInteractionFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def make_session(self, seed: int):
        session = make_session(
            self.db,
            self.mishra,
            self.zimone,
            players=2,
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

    def test_bojuka_bog_targets_and_exiles_one_graveyard(self):
        session = self.make_session(830)
        engine = session.engine
        bog = self.card(engine, "B", "Bojuka Bog")
        opposing_card = self.card(engine, "A", "Ichor Wellspring")
        own_card = self.card(engine, "B", "Faerie Mastermind")
        engine.move_card(opposing_card.object_id, "graveyard")
        engine.move_card(own_card.object_id, "graveyard")

        engine.move_card(
            bog.object_id,
            "battlefield",
            controller="B",
            tapped=True,
            semantic_events=True,
            reason="Bojuka Bog scenario",
        )
        self.assertTrue(engine._stabilize())
        packet = session.packet("pilot:B", full=True)
        self.assertEqual("semantic.target", packet["decision"]["kind"])
        candidates = set(
            packet["decision"]["ctx"]["target_schema"]["legal_refs"]
        )
        self.assertEqual({"A", "B"}, candidates)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "targets": ["A"],
                "plan": "DISRUPT_GRAVEYARD",
                "reason": "Exile A's graveyard with Bojuka Bog.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("exile", opposing_card.zone)
        self.assertEqual("graveyard", own_card.zone)

    def test_reanimate_uses_any_graveyard_and_exact_mana_value_loss(self):
        session = self.make_session(831)
        engine = session.engine
        spell = self.card(engine, "B", "Reanimate")
        target = self.card(engine, "A", "Brudiclad, Telchor Engineer")
        engine.move_card(target.object_id, "graveyard")
        engine._remove_from_zone(spell)
        spell.zone = "stack"
        item = StackItem(
            stack_id="reanimate-scenario",
            ref="S-reanimate",
            kind="spell",
            controller="B",
            label="Reanimate",
            card_object_id=spell.object_id,
            semantic_key=f"{spell.oracle_id}:spell:front",
            default_destination="graveyard",
            visibility=list(engine.seats),
        )
        engine.state.stack.append(item)
        before_life = engine.state.players["B"].life

        engine._prepare_stack_resolution()
        packet = session.packet("pilot:B", full=True)
        self.assertEqual("semantic.target", packet["decision"]["kind"])
        candidates = set(
            packet["decision"]["ctx"]["target_schema"]["legal_refs"]
        )
        self.assertIn(target.ref, candidates)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "targets": [target.ref],
                "plan": "DEVELOP_ENGINE",
                "reason": "Reanimate the six-mana artifact creature.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertEqual("battlefield", target.zone)
        self.assertEqual("B", target.controller)
        self.assertEqual(
            before_life - 6,
            engine.state.players["B"].life,
        )

    def test_sylvan_safekeeper_pays_land_and_grants_temporary_shroud(self):
        session = self.make_session(832)
        engine = session.engine
        safekeeper = self.card(engine, "B", "Sylvan Safekeeper")
        creature = self.card(engine, "B", "Deathrite Shaman")
        land = self.card(engine, "B", "Island")
        for card in (safekeeper, creature, land):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="B",
            )
        engine.state.active_player = "B"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "B"
        engine._issue_priority("B")
        packet = session.packet("pilot:B", full=True)
        action = next(
            action
            for action in packet["decision"]["legal_actions"]
            if action.get("source") == safekeeper.ref
        )
        result = session.act(
            "pilot:B",
            {
                "action_id": action["id"],
                "cost_cards": [land.ref],
                "targets": [creature.ref],
                "plan": "PROTECT_ENGINE",
                "reason": "Sacrifice the land to protect Deathrite Shaman.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", land.zone)
        self.resolve_top(engine)
        self.assertIn(
            "Shroud",
            engine._effective_card_data(creature)["keywords"],
        )

    def test_senseis_divining_top_reorders_and_draws_to_library(self):
        session = self.make_session(833)
        engine = session.engine
        top = self.card(engine, "A", "Sensei's Divining Top")
        engine.move_card(
            top.object_id,
            "battlefield",
            controller="A",
        )
        original_top_first = [
            engine.state.cards[object_id].ref
            for object_id in reversed(
                engine.state.players["A"].zones["library"][-3:]
            )
        ]
        engine.state.players["A"].mana_pool["C"] = 1
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        packet = session.packet("pilot:A", full=True)
        look_action = next(
            action
            for action in packet["decision"]["legal_actions"]
            if action.get("source") == top.ref
            and action.get("ability") == "ab1"
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": look_action["id"],
                "plan": "SET_UP_DRAW",
                "reason": "Inspect and reorder the top three cards.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        choice = session.packet("pilot:A", full=True)
        self.assertEqual("semantic.choice", choice["decision"]["kind"])
        options = [
            item["id"] for item in choice["decision"]["ctx"]["cards"]
        ]
        self.assertEqual(set(original_top_first), set(options))
        opposing = json.dumps(session.packet("pilot:B", full=True))
        self.assertTrue(all(ref not in opposing for ref in options))
        selected = list(reversed(options))
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "cards": selected,
                "plan": "SET_UP_DRAW",
                "reason": "Put the selected card on top and preserve the rest.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(
            selected[0],
            engine.state.cards[
                engine.state.players["A"].zones["library"][-1]
            ].ref,
        )

        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = "A"
        engine._issue_priority("A")
        packet = session.packet("pilot:A", full=True)
        draw_action = next(
            action
            for action in packet["decision"]["legal_actions"]
            if action.get("source") == top.ref
            and action.get("ability") == "ab2"
        )
        result = session.act(
            "pilot:A",
            {
                "action_id": draw_action["id"],
                "plan": "GAIN_CARDS",
                "reason": "Draw the arranged card and put Top on the library.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(engine)
        self.assertIn(
            next(
                card.object_id
                for card in engine.state.cards.values()
                if card.ref == selected[0]
            ),
            engine.state.players["A"].zones["hand"],
        )
        self.assertEqual("library", top.zone)
        self.assertEqual(
            top.object_id,
            engine.state.players["A"].zones["library"][-1],
        )

    def test_idol_of_oblivion_tracks_token_creation_and_both_abilities(self):
        session = self.make_session(834)
        engine = session.engine
        idol = self.card(engine, "A", "Idol of Oblivion")
        engine.move_card(
            idol.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        draw_ability = next(
            ability
            for ability in engine._activated_abilities(idol)
            if ability.ability_id == "ab1"
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", draw_ability)[0],
        )

        engine.create_token(
            "A",
            name="Servo",
            characteristics={
                "type_line": "Artifact Creature — Servo",
                "power": "1",
                "toughness": "1",
            },
            reason="Idol test setup",
        )
        self.assertEqual(
            "payable",
            activation_condition_status(engine, "A", draw_ability)[0],
        )
        before_draws = len(engine.state.players["A"].draw_history)
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {"source": idol.ref, "ability": "ab1"},
        )
        self.resolve_top(engine)
        self.assertEqual(
            before_draws + 1,
            len(engine.state.players["A"].draw_history),
        )

        idol.tapped = False
        before_tokens = int(
            engine.state.players["A"].stats["tokens_created_by_turn"][
                str(engine.state.turn_sequence)
            ]
        )
        engine.state.players["A"].mana_pool["C"] = 8
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": idol.ref,
                "ability": "ab2",
                "pay": "manual",
                "payment": {"C": 8},
            },
        )
        self.resolve_top(engine)
        self.assertEqual("graveyard", idol.zone)
        eldrazi = next(
            card
            for card in engine.state.cards.values()
            if card.owner == "A"
            and card.is_token
            and card.printed_name == "Eldrazi Token"
        )
        data = engine._effective_card_data(eldrazi)
        self.assertEqual(("10", "10"), (data["power"], data["toughness"]))
        self.assertEqual(
            before_tokens + 1,
            engine.state.players["A"].stats["tokens_created_by_turn"][
                str(engine.state.turn_sequence)
            ],
        )

    def test_liquimetal_torque_changes_only_nonland_permanents_until_cleanup(
        self,
    ):
        session = self.make_session(835)
        engine = session.engine
        torque = self.card(engine, "A", "Liquimetal Torque")
        target = self.card(engine, "B", "Zimone and Dina")
        land = self.card(engine, "B", "Island")
        for card, controller in (
            (torque, "A"),
            (target, "B"),
            (land, "B"),
        ):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller=controller,
                log=False,
            )
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": torque.ref,
                "ability": "ab2",
                "targets": [target.ref],
            },
        )
        self.resolve_top(engine)
        self.assertIn(
            "artifact",
            engine._type_parts(
                engine._effective_card_data(target)["type_line"]
            )[0],
        )
        engine._finish_cleanup()
        self.assertNotIn(
            "artifact",
            engine._type_parts(
                engine._effective_card_data(target)["type_line"]
            )[0],
        )

        torque.tapped = False
        engine.state.priority_player = "A"
        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "A",
                {
                    "source": torque.ref,
                    "ability": "ab2",
                    "targets": [land.ref],
                },
            )

    def test_deathrite_shaman_executes_all_three_targeted_abilities(self):
        cases = (
            ("ab1", "Island", {}, "mana"),
            ("ab2", "Abrade", {"B": 1}, "opponent_life"),
            ("ab3", "Goblin Engineer", {"G": 1}, "controller_life"),
        )
        for index, (ability_id, target_name, mana, outcome) in enumerate(
            cases
        ):
            with self.subTest(ability=ability_id):
                session = self.make_session(836 + index)
                engine = session.engine
                shaman = self.card(engine, "B", "Deathrite Shaman")
                target = self.card(engine, "A", target_name)
                engine.move_card(
                    shaman.object_id,
                    "battlefield",
                    controller="B",
                    log=False,
                )
                shaman.acquired_control_turn_count = (
                    engine.state.players["B"].turns_begun - 1
                )
                engine.move_card(target.object_id, "graveyard", log=False)
                engine.state.players["B"].mana_pool.update(mana)
                before_a = engine.state.players["A"].life
                before_b = engine.state.players["B"].life
                engine.state.priority_player = "B"
                response = {
                    "source": shaman.ref,
                    "ability": ability_id,
                    "targets": [target.ref],
                }
                if mana:
                    response.update(
                        {
                            "pay": "manual",
                            "payment": mana,
                        }
                    )
                engine._activate("B", response)
                self.resolve_top(engine)

                if outcome == "mana":
                    packet = session.packet("pilot:B", full=True)
                    self.assertEqual(
                        ["W", "U", "B", "R", "G"],
                        packet["decision"]["legal_actions"][0]["choice_schema"]["legal_values"],
                    )
                    result = session.act(
                        "pilot:B",
                        {
                            "action_id": "choose",
                            "choice": "G",
                            "plan": "DEVELOP_MANA",
                            "reason": "Choose green from Deathrite Shaman.",
                        },
                    )
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual(
                        1, engine.state.players["B"].mana_pool["G"]
                    )
                elif outcome == "opponent_life":
                    self.assertEqual(
                        before_a - 2, engine.state.players["A"].life
                    )
                    self.assertEqual(
                        before_b, engine.state.players["B"].life
                    )
                else:
                    self.assertEqual(
                        before_b + 2, engine.state.players["B"].life
                    )
                    self.assertEqual(
                        before_a, engine.state.players["A"].life
                    )
                self.assertEqual("exile", target.zone)


if __name__ == "__main__":
    unittest.main()
