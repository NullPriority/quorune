from __future__ import annotations

import unittest

from common import keep_all, load_assets, make_session
from quorune.rules.activation import activation_condition_status


class ExactManaFamilyTests(unittest.TestCase):
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

    def test_compiled_mana_catalogs_use_offered_actions_and_replay(self):
        from pathlib import Path
        import tempfile

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
        from quorune.rules.capabilities import load_default_capability_registry

        capabilities = load_default_capability_registry()
        for index, name in enumerate(("Ashnod's Altar", "Mana Confluence", "Spire of Industry")):
            with self.subTest(card=name):
                session = self.make_session(605_300_970 + index)
                engine = session.engine
                source = self.card(engine, "A", name)
                engine.move_card(source.object_id, "battlefield", controller="A", log=False)
                artifact = self.card(engine, "A", "Sol Ring")
                if name == "Ashnod's Altar":
                    creature = self.card(engine, "A", "Goblin Engineer")
                    other = self.card(engine, "B", "Birds of Paradise")
                    for card, controller in ((creature, "A"), (other, "B"), (artifact, "A")):
                        engine.move_card(card.object_id, "battlefield", controller=controller, log=False)
                if name == "Spire of Industry":
                    engine.move_card(artifact.object_id, "hand", log=False)
                    session.state.players["A"].mana_pool["C"] = 1
                engine.state.started = True
                engine.state.active_player = "A"
                engine.state.phase, engine.state.step, engine.state.phase_index = "precombat_main", "main", 3
                engine._grant_priority("A")
                engine.pump()
                program = compile_best_available_card_program(
                    self.db, self.db.lookup(name), semantic_registry=engine.semantics,
                    capability_registry=capabilities, capability_profile="commander_review",
                )
                binding = bind_card_program_runtime(program, capability_registry=capabilities, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)
                self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
                session.initial_checkpoint = checkpoint_envelope(session.state)
                session.commands.clear()
                session.decisions.clear()

                def actions():
                    return session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

                def reject(principal, command):
                    before = authoritative_state_hash(session.state)
                    result = session.act(principal, command)
                    self.assertFalse(result.ok, result.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))

                if name == "Spire of Industry":
                    self.assertTrue(any(row["id"] == f"activate:{source.ref}:ab1" for row in actions()))
                    self.assertFalse(any(row["id"] == f"activate:{source.ref}:ab2" for row in actions()))
                    reject("pilot:A", {"action_id": f"activate:{source.ref}:ab2", "mana_choice": "G"})
                    cast, = (row for row in actions() if row["id"] == "cast:" + artifact.ref)
                    result = session.act("pilot:A", {"action_id": cast["id"], "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    for _ in range(20):
                        if not session.state.stack:
                            break
                        self.assertEqual("priority", session.state.pending_decision.kind)
                        result = session.act(session.pending_principals()[0], {"action_id": "pass"})
                        self.assertTrue(result.ok, result.summary)
                    else:
                        self.fail("The artifact did not finish resolving")
                    self.assertEqual("battlefield", session.state.cards[artifact.object_id].zone)
                ability = "ab2" if name == "Spire of Industry" else "ab1"
                offer, = (row for row in actions() if row["id"] == f"activate:{source.ref}:{ability}")
                command = {"action_id": offer["id"]}
                if name == "Ashnod's Altar":
                    reject("pilot:A", {**command, "cost_cards": [artifact.ref]})
                    reject("pilot:A", {**command, "cost_cards": [other.ref]})
                    command["cost_cards"] = [creature.ref]
                else:
                    reject("pilot:A", {**command, "mana_choice": "C"})
                    command["mana_choice"] = "G"
                reject("pilot:B", command)
                life = session.state.players["A"].life
                result = session.act("pilot:A", command)
                self.assertTrue(result.ok, result.summary)
                self.assertFalse(session.state.stack)
                self.assertEqual("A", session.state.priority_player)
                if name == "Ashnod's Altar":
                    self.assertEqual("graveyard", session.state.cards[creature.object_id].zone)
                    self.assertEqual(2, session.state.players["A"].mana_pool["C"])
                    self.assertEqual(life, session.state.players["A"].life)
                else:
                    self.assertEqual(life - 1, session.state.players["A"].life)
                    self.assertEqual(1, session.state.players["A"].mana_pool["G"])
                    self.assertTrue(session.state.cards[source.object_id].tapped)
                with tempfile.TemporaryDirectory() as directory:
                    record = Path(directory) / "compiled-mana"
                    session.save(record, include_review=False)
                    replay = replay_record(record, self.db, verify=True)
                    self.assertTrue(replay["ok"], replay)
                    self.assertEqual(authoritative_state_hash(session.state), replay["final_state_hash"])

    def test_ashnods_altar_sacrifices_exactly_one_creature_for_two_colorless(
        self,
    ):
        session = self.make_session(870)
        engine = session.engine
        altar = self.card(engine, "A", "Ashnod's Altar")
        creature = self.card(engine, "A", "Goblin Engineer")
        for card in (altar, creature):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": altar.ref,
                "ability": "ab1",
                "cost_cards": [creature.ref],
            },
        )
        self.assertEqual("graveyard", creature.zone)
        self.assertEqual(2, engine.state.players["A"].mana_pool["C"])
        self.assertFalse(engine.state.stack)

    def test_mana_confluence_pays_life_and_produces_selected_color(self):
        session = self.make_session(871)
        engine = session.engine
        confluence = self.card(engine, "A", "Mana Confluence")
        engine.move_card(
            confluence.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        before_life = engine.state.players["A"].life
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": confluence.ref,
                "ability": "ab1",
                "mana_choice": "G",
            },
        )
        self.assertEqual(before_life - 1, engine.state.players["A"].life)
        self.assertEqual(1, engine.state.players["A"].mana_pool["G"])
        self.assertTrue(confluence.tapped)

    def test_fellwar_stone_and_exotic_orchard_use_live_opponent_land_colors(
        self,
    ):
        session = self.make_session(872)
        engine = session.engine
        stone = self.card(engine, "A", "Fellwar Stone")
        orchard = self.card(engine, "A", "Exotic Orchard")
        island = self.card(engine, "B", "Island")
        forest = self.card(engine, "B", "Forest")
        for card, controller in (
            (stone, "A"),
            (orchard, "A"),
            (island, "B"),
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
                "source": stone.ref,
                "ability": "ab1",
                "mana_choice": "U",
            },
        )
        self.assertEqual(1, engine.state.players["A"].mana_pool["U"])

        engine.move_card(
            forest.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": orchard.ref,
                "ability": "ab1",
                "mana_choice": "G",
            },
        )
        self.assertEqual(1, engine.state.players["A"].mana_pool["G"])

    def test_spire_of_industry_requires_an_artifact_and_pays_life(self):
        session = self.make_session(873)
        engine = session.engine
        spire = self.card(engine, "A", "Spire of Industry")
        artifact = self.card(engine, "A", "Sol Ring")
        engine.move_card(
            spire.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        ability = next(
            ability
            for ability in engine._activated_abilities(spire)
            if ability.ability_id == "ab2"
        )
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "A", ability)[0],
        )
        engine.move_card(
            artifact.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        self.assertEqual(
            "payable",
            activation_condition_status(engine, "A", ability)[0],
        )
        before_life = engine.state.players["A"].life
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": spire.ref,
                "ability": "ab2",
                "mana_choice": "R",
            },
        )
        self.assertEqual(before_life - 1, engine.state.players["A"].life)
        self.assertEqual(1, engine.state.players["A"].mana_pool["R"])

    def test_bloom_tender_derives_one_mana_for_each_controlled_color(self):
        session = self.make_session(874)
        engine = session.engine
        tender = self.card(engine, "B", "Bloom Tender")
        zimone = self.card(engine, "B", "Zimone and Dina")
        for card in (tender, zimone):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="B",
                log=False,
            )
        tender.acquired_control_turn_count = (
            engine.state.players["B"].turns_begun - 1
        )
        engine.state.priority_player = "B"
        engine._activate(
            "B",
            {"source": tender.ref, "ability": "ab1"},
        )
        self.assertEqual(
            {"B": 1, "G": 1, "U": 1},
            {
                color: amount
                for color, amount in engine.state.players["B"].mana_pool.items()
                if amount
            },
        )
        self.assertFalse(engine.state.stack)


if __name__ == "__main__":
    unittest.main()
