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




class LandManaPotentialTests(unittest.TestCase):
    from test_bound_effect_programs import BoundEffectProgramRuntimeTests as _native

    register_fixture_cards_on_add = True
    session = _native.session
    add = _native.add
    ready = _native.ready
    checkpoint = _native.checkpoint
    resolve = _native.resolve
    replay = _native.replay

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from common import ROOT
        from quorune.carddb import CardDatabase
        from quorune.deck import DeckDefinition, DeckEntry
        from quorune.rules.capabilities import load_default_capability_registry
        from scripts.build_test_database import build_fixture_database

        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "land-mana-potential.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "scryfall-exact-lists.json",
            fixtures / "land-mana-potential-cards.json",
            fixtures / "incubation-druid-mana-cards.json",
            fixtures / "fixed-draw-hand-putback-cards.json",
            fixtures / "chosen-color-mana-cards.json",
            fixtures / "entry-copy-cards.json",
            fixtures / "public-quantity-mana-cards.json",
            fixtures / "tap-for-mana-production-multipliers-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Land mana potential",
            [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)],
            ["Generic Bound Commander"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def offer(self, session, card, *, actor="A"):
        actions = session.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == f"activate:{card.ref}:ab1")

    def act(self, session, action_id, *, actor="A", **selection):
        result = session.act("pilot:" + actor, {"action_id": action_id, **selection})
        self.assertTrue(result.ok, result.summary)

    def reject(self, session, action_id, *, actor="A", **selection):
        from quorune.record import authoritative_state_hash

        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + actor, {"action_id": action_id, **selection})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def output_options(self, offer):
        rows = offer["choice_schema"]["mana_output"]["options"]
        return [row["value"] for row in rows]

    def test_native_opponent_mirrors_use_cost_free_potential_and_supply_real_payment(self):
        for index, name in enumerate(("Vivid Crag", "Primal Beyond")):
            with self.subTest(producer=name):
                game = self.session(106_700_001 + index * 10)
                engine = game.engine
                stone = self.add(engine, "Fellwar Stone")
                orchard = self.add(engine, "Exotic Orchard")
                counted = self.add(engine, name, seat="C")
                spell = self.add(engine, "Brainstorm", zone="hand")
                counted.tapped = True
                self.assertEqual(0, counted.counters.get("charge", 0))
                self.ready(game, stone, {})
                self.checkpoint(game)
                for card in (stone, orchard):
                    offer = self.offer(game, card)
                    self.assertEqual([{color: 1} for color in "WUBRG"], self.output_options(offer))
                    self.reject(game, offer["id"], mana_choice="C")
                    self.reject(game, offer["id"], mana_output={"U": True})
                    self.reject(game, offer["id"], mana_output={"U": 2})
                    self.reject(game, offer["id"], mana_choice="U", mana_output={"U": 1})
                    self.reject(game, offer["id"], actor="B", mana_choice="U")
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                if name == "Primal Beyond":
                    self.act(game, self.offer(game, stone)["id"], mana_output={"U": 1})
                    self.assertEqual(1, game.state.players["A"].mana_pool["U"])
                self.act(game, "cast:" + spell.ref, pay="auto")
                self.assertEqual(0, game.state.players["A"].mana_pool["U"])
                self.assertTrue(game.state.stack)
                self.assertTrue(counted.tapped)
                self.assertEqual(0, counted.counters.get("charge", 0))
                self.assertTrue(any(game.state.cards[card.object_id].tapped for card in (stone, orchard)))
                self.replay(game, load=True)

    def test_native_no_output_cycle_taps_and_adds_nothing(self):
        game = self.session(106_700_002)
        engine = game.engine
        orchard = self.add(engine, "Exotic Orchard")
        pool = self.add(engine, "Reflecting Pool")
        stone = self.add(engine, "Fellwar Stone")
        self.add(engine, "Exotic Orchard", seat="B", ref="opposing-orchard")
        self.add(engine, "Reflecting Pool", seat="B", ref="opposing-pool")
        self.add(engine, "Sol Ring", seat="C")
        self.ready(game, orchard, {})
        self.checkpoint(game)
        for card in (orchard, pool, stone):
            offer = self.offer(game, card)
            self.assertNotIn("mana_output", offer.get("choice_schema") or {})
            self.reject(game, offer["id"], mana_choice="G")
            self.reject(game, offer["id"], mana_output={"G": 1})
            self.act(game, offer["id"])
            self.assertTrue(game.state.cards[card.object_id].tapped)
            self.assertFalse(any(game.state.players["A"].mana_pool.values()))
            self.assertFalse(game.state.stack)
        self.assertEqual([{}, {}, {}], [event.details["bundle"] for event in game.state.events
                                         if event.code == "mana.ability"])
        self.replay(game, load=True)

    def test_native_seeded_cycle_and_actual_vesuva_copy_use_current_land_abilities(self):
        game = self.session(106_700_003)
        engine = game.engine
        orchard = self.add(engine, "Exotic Orchard")
        self.add(engine, "Forest")
        self.add(engine, "Exotic Orchard", seat="B", ref="opponent-orchard")
        pool = self.add(engine, "Reflecting Pool", seat="B")
        copy = self.add(engine, "Vesuva", zone="hand")
        self.add(engine, "Mana Reflection", seat="B")
        self.ready(game, copy, {})
        self.checkpoint(game)
        self.act(game, self.offer(game, orchard)["id"], mana_choice="G")
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])
        self.act(game, "play-land:" + copy.ref)
        choices = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        chosen = next(row for row in choices if pool.ref in row["id"])
        self.act(game, "choose", replacement=chosen["id"])
        copy_now = game.state.cards[copy.object_id]
        self.assertTrue(copy_now.tapped)
        self.assertEqual("Reflecting Pool", engine.display_name(copy.object_id))
        self.assertEqual("controlled_types", engine._activated_abilities(copy_now)[0].dynamic_mana_output["scope"])
        self.act(game, "pass")
        offer = self.offer(game, pool, actor="B")
        self.reject(game, offer["id"], actor="B", mana_choice="U")
        self.act(game, offer["id"], actor="B", mana_choice="G")
        self.assertEqual(2, game.state.players["B"].mana_pool["G"])
        self.replay(game, load=True)

    def test_native_pool_uses_chosen_color_and_live_positive_amounts(self):
        game = self.session(106_700_004)
        engine = game.engine
        pool = self.add(engine, "Reflecting Pool")
        vale = self.add(engine, "Shimmerdrift Vale", zone="hand")
        devotion = self.add(engine, "Nykthos, Shrine to Nyx")
        devotion.tapped = True
        self.add(engine, "Generic Bound Body", seat="B")
        self.ready(game, vale, {})
        self.checkpoint(game)
        self.act(game, "play-land:" + vale.ref)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        red = next(row["id"] for row in options if row["id"].endswith(":R"))
        self.act(game, "choose", choices={"replacement": red})
        offer = self.offer(game, pool)
        self.assertEqual([{"R": 1}, {"C": 1}], self.output_options(offer))
        self.reject(game, offer["id"], mana_choice="W")
        self.act(game, offer["id"], mana_choice="C")
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertTrue(game.state.cards[vale.object_id].tapped)
        self.replay(game, load=True)

    def test_land_mana_potential_grammar_and_runtime_dependency_fail_closed(self):
        from copy import deepcopy
        from dataclasses import replace
        import json

        from common import ROOT
        from quorune.abilities import ActivatedAbility
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from quorune.land_mana_potential import LAND_MANA_POTENTIAL_CAPABILITY, LAND_MANA_POTENTIAL_HANDLER_ID
        from quorune.oracle_ir import compile_oracle_card, generated_programs
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.semantic_runtime.mana_abilities import fixed_mana_specs_from_descriptors

        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        card = self.db.lookup("Reflecting Pool")
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(LAND_MANA_POTENTIAL_CAPABILITY, node.capability_dependencies)
        spec, = fixed_mana_specs_from_descriptors(node.handlers)
        ability = spec.to_activated_ability()
        self.assertEqual(ability, ActivatedAbility.from_dict(ability.to_dict()))
        for changed in ("scope", "schema_version", "tap_source", "target_schema"):
            with self.subTest(changed=changed):
                raw = ability.to_dict()
                if changed == "scope":
                    raw["dynamic_mana_output"]["scope"] = "all_types"
                elif changed == "schema_version":
                    raw["dynamic_mana_output"]["schema_version"] = True
                elif changed == "tap_source":
                    raw["tap_source"] = False
                else:
                    raw["target_schema"] = {"zones": ["battlefield"]}
                with self.assertRaises(ValueError):
                    ActivatedAbility.from_dict(raw)
        for changed in (
            "{1}, " + card.oracle_text, card.oracle_text + " Draw a card.",
            card.oracle_text.replace("you control", "any player controls"),
            card.oracle_text.replace("any type", "any color"),
        ):
            with self.subTest(oracle=changed):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=changed), **options).status)
        raw_registry = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        changed_registry = deepcopy(raw_registry)
        for row in changed_registry["capabilities"]:
            if row["id"] == LAND_MANA_POTENTIAL_CAPABILITY:
                row.update(status="blocked", blockers=["land mana potential owner withdrawn"])
        self.assertNotEqual("exact", compile_oracle_card(card, capability_registry=CapabilityRegistry(changed_registry),
                                                        capability_profile="commander_review").status)
        program, = generated_programs(self.db, card, trust_level="trusted", **options)
        self.assertTrue(bind_semantic_program_runtime(program, profile="commander_review",
                                                       capability_registry=self.registry)["strict"])
        without_owner = replace(program, handlers=tuple(handler for handler in program.handlers
                                     if handler["handler_id"] != LAND_MANA_POTENTIAL_HANDLER_ID))
        self.assertFalse(bind_semantic_program_runtime(without_owner, profile="commander_review",
                                                        capability_registry=self.registry)["strict"])



    def test_native_mirror_rejects_a_mutated_potential_and_recovers(self):
        from unittest.mock import patch

        from quorune import mana_ability_runtime

        game = self.session(106_700_005)
        orchard = self.add(game.engine, "Exotic Orchard")
        self.add(game.engine, "Forest", seat="B")
        self.ready(game, orchard, {})
        self.checkpoint(game)
        offer = self.offer(game, orchard)
        with patch.object(mana_ability_runtime, "_land_mana_types_by_controller",
                          return_value={"A": {"G"}, "B": set(), "C": set(), "D": set()}):
            self.reject(game, offer["id"], mana_choice="G")
        self.act(game, offer["id"], mana_choice="G")
        self.assertEqual(1, game.state.players["A"].mana_pool["G"])


    def test_native_druid_uses_tapped_lands_without_their_costs_or_spending_restrictions(self):
        for index, name in enumerate(("Spire of Industry", "Primal Beyond")):
            with self.subTest(land=name):
                game = self.session(106_700_010 + index)
                druid = self.add(game.engine, "Incubation Druid")
                land = self.add(game.engine, name)
                land.tapped = True
                spell = self.add(game.engine, "Brainstorm", zone="hand")
                game.state.players["A"].life = 1
                self.ready(game, druid, {})
                self.checkpoint(game)

                offer = self.offer(game, druid)
                self.assertEqual([{color: 1} for color in "WUBRGC"], self.output_options(offer))
                self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
                self.reject(game, offer["id"], actor="B", mana_choice="U")
                self.act(game, offer["id"], mana_choice="U")
                self.assertEqual(1, game.state.players["A"].life)
                self.assertEqual(1, game.state.players["A"].mana_pool["U"])
                self.assertTrue(game.state.cards[druid.object_id].tapped)
                self.assertTrue(game.state.cards[land.object_id].tapped)
                self.assertFalse(game.state.stack)

                self.act(game, "cast:" + spell.ref, pay="auto")
                self.assertTrue(game.state.stack)
                self.assertEqual(0, game.state.players["A"].mana_pool["U"])
                self.replay(game, load=True)

    def test_native_druid_taps_for_zero_with_only_opposing_lands_and_a_friendly_nonland(self):
        game = self.session(106_700_012)
        druid = self.add(game.engine, "Incubation Druid")
        druid.counters["+1/+1"] = 1
        self.add(game.engine, "Sol Ring")
        self.add(game.engine, "Island", seat="B")
        self.add(game.engine, "Wastes", seat="C")
        self.ready(game, druid, {})
        self.checkpoint(game)

        offer = self.offer(game, druid)
        self.assertNotIn("mana_output", offer.get("choice_schema") or {})
        self.reject(game, offer["id"], mana_choice="U")
        self.reject(game, offer["id"], mana_output={"C": 3})
        self.act(game, offer["id"])
        self.assertTrue(game.state.cards[druid.object_id].tapped)
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.assertFalse(game.state.stack)
        self.assertEqual([{}], [event.details["bundle"] for event in game.state.events
                                if event.code == "mana.ability"])
        self.replay(game, load=True)

    def test_native_druid_adapt_changes_one_to_three_and_rolls_back_invalid_choices(self):
        from unittest.mock import patch

        from quorune.land_mana_potential import LandManaPotential
        from quorune.record import authoritative_state_hash

        game = self.session(106_700_013)
        druid = self.add(game.engine, "Incubation Druid")
        druid.counters["luck"] = 1
        self.add(game.engine, "Forest")
        self.add(game.engine, "Wastes")
        self.add(game.engine, "Island", seat="B")
        untap = self.add(game.engine, "Vitalize", zone="hand")
        self.ready(game, druid, {"G": 5, "C": 6})
        self.checkpoint(game)

        offer = self.offer(game, druid)
        self.assertEqual([{"G": 1}, {"C": 1}], self.output_options(offer))
        self.reject(game, offer["id"], mana_output={"G": 3})
        self.reject(game, offer["id"], mana_choice="U")
        old_cap = game.engine.permissions.capability_for("pilot:A").token
        self.act(game, offer["id"], mana_choice="G")
        self.assertEqual(6, game.state.players["A"].mana_pool["G"])

        adapt = f"activate:{druid.ref}:ab2"
        self.act(game, adapt, pay="auto")
        self.assertTrue(game.state.stack)
        self.assertEqual({"luck": 1}, game.state.cards[druid.object_id].counters)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"luck": 1, "+1/+1": 3}, game.state.cards[druid.object_id].counters)
        self.act(game, "cast:" + untap.ref, pay="auto")
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[druid.object_id].tapped)

        offer = self.offer(game, druid)
        self.assertEqual([{"G": 3}, {"C": 3}], self.output_options(offer))
        self.reject(game, offer["id"], mana_output={"C": 1})
        self.reject(game, offer["id"], mana_output={"G": 3, "C": 3})
        self.reject(game, offer["id"], mana_output={"C": True})
        self.reject(game, offer["id"], mana_choice="U")
        before = authoritative_state_hash(game.state)
        rejected = game.engine.try_submit(
            token=old_cap, principal="pilot:A", action=offer["action"],
            payload={"card": druid.ref, "ability_id": "ab1", "mana_choice": "C"},
        )
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertIn("capability", rejected.summary.lower())
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertFalse(game.state.cards[druid.object_id].tapped)

        with patch.object(LandManaPotential, "amount", return_value=1):
            self.reject(game, offer["id"], mana_output={"C": 3})
        pool_before = game.state.players["A"].mana_pool.copy()
        self.act(game, offer["id"], mana_output={"C": 3})
        self.assertEqual(pool_before["C"] + 3, game.state.players["A"].mana_pool["C"])
        self.assertEqual(pool_before["G"], game.state.players["A"].mana_pool["G"])
        self.assertFalse(game.state.stack)

        self.act(game, adapt, pay="auto")
        self.assertTrue(game.state.stack)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"luck": 1, "+1/+1": 3}, game.state.cards[druid.object_id].counters)
        self.replay(game, load=True)

    def test_druid_shape_binding_requires_its_exact_source_counter_owner(self):
        from dataclasses import replace
        import json

        from quorune.card_programs import bind_card_program_runtime
        from quorune.card_programs.adapters import compile_best_available_card_program
        from quorune.land_mana_potential import (
            LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY, LandManaPotential,
        )
        from quorune.oracle_ir import compile_oracle_card
        from quorune.rules.capabilities import DEFAULT_CAPABILITY_REGISTRY, CapabilityRegistry
        from quorune.rules.evidence import (
            DEFAULT_CAPABILITY_EVIDENCE, capability_evidence_fingerprint, validate_capability_evidence_index,
        )
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.semantics import SemanticRegistry
        from quorune.semantic_runtime.mana_abilities import fixed_mana_specs_from_descriptors

        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        card = self.db.lookup("Incubation Druid")
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        mana, adapt = ir.faces[0].nodes
        self.assertEqual("keyword-action-adapt-fixed-v1", adapt.template_id)
        self.assertTrue(adapt.exact)
        spec, = fixed_mana_specs_from_descriptors(mana.handlers)
        self.assertIs(LandManaPotential.CONTROLLED_TYPES_WITH_PLUS_ONE_COUNTER, spec.output)
        self.assertIn(LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY, mana.capability_dependencies)
        for old, changed in (("+1/+1", "luck"), ("three mana", "two mana"),
                             ("you control", "an opponent controls"), ("that type instead", "of any color instead")):
            with self.subTest(changed=changed):
                shape = replace(card, oracle_text=card.oracle_text.replace(old, changed, 1))
                self.assertNotEqual("exact", compile_oracle_card(shape, **options).status)

        program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(), **options)
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)
        self.assertEqual("capability_closed", program.trust_closure["trust_basis"])
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_text(encoding="utf-8"))
        owner = next(row for row in raw["capabilities"]
                     if row["id"] == LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY)
        owner.update(status="blocked", blockers=["source counter production owner withdrawn"])
        blocked = CapabilityRegistry(raw)
        evidence = read_evidence_document(DEFAULT_CAPABILITY_EVIDENCE)
        self.assertEqual(self.registry.evidence_fingerprint, evidence["fingerprint"])
        evidence["registry_fingerprint"] = blocked.fingerprint
        evidence["fingerprint"] = capability_evidence_fingerprint(evidence)
        blocked.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=blocked))
        withheld = compile_oracle_card(card, capability_registry=blocked, capability_profile="commander_review")
        self.assertNotEqual("exact", withheld.status)
        blockers = {blocker for residual in withheld.material_residuals for blocker in residual.blockers}
        self.assertIn(f"capability:status:{LAND_MANA_POTENTIAL_SOURCE_COUNTER_CAPABILITY}:blocked", blockers)
        self.assertTrue(withheld.faces[0].nodes[-1].exact)
        binding = bind_card_program_runtime(program, capability_registry=blocked, profile="commander_review")
        self.assertFalse(binding["strict_capability_ready"], binding)


if __name__ == "__main__":
    unittest.main()
