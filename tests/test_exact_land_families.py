from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from common import ROOT, keep_all, load_assets, make_session
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.engine import TURN_STEPS
from quorune.record import authoritative_state_hash
from quorune.rules.activation import activation_condition_status
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class ExactLandFamilyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, cls.mishra, cls.zimone = load_assets()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    @staticmethod
    def card(engine, owner: str, name: str):
        return next(
            card
            for card in engine.state.cards.values()
            if card.owner == owner and card.printed_name == name
        )

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
    def resolve_top(engine):
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.priority_player = None
        engine._prepare_stack_resolution()

    def test_bounce_lands_require_controller_to_return_one_land(self):
        for index, name in enumerate(
            ("Golgari Rot Farm", "Simic Growth Chamber", "Dimir Aqueduct")
        ):
            with self.subTest(card=name):
                session = make_session(
                    self.db,
                    self.mishra,
                    self.zimone,
                    players=2,
                    seed=850 + index,
                )
                keep_all(session)
                engine = session.engine
                engine.permissions.invalidate_current()
                engine.state.pending_decision = None
                engine.state.priority_player = None
                bounce = self.card(engine, "B", name)
                own_land = self.card(engine, "B", "Island")
                opposing_land = self.card(engine, "A", "Island")
                engine.move_card(
                    own_land.object_id,
                    "battlefield",
                    controller="B",
                )
                engine.move_card(
                    opposing_land.object_id,
                    "battlefield",
                    controller="A",
                )

                engine.move_card(
                    bounce.object_id,
                    "battlefield",
                    controller="B",
                    tapped=True,
                    semantic_events=True,
                    reason=f"{name} scenario",
                )
                self.assertFalse(engine._stabilize())
                self.assertEqual(1, len(engine.state.stack))
                engine._prepare_stack_resolution()
                packet = session.packet("pilot:B", full=True)
                self.assertEqual("choice.apnap", packet["decision"]["kind"])
                options = set(packet["decision"]["ctx"]["options"])
                self.assertEqual({bounce.ref, own_land.ref}, options)
                result = session.act(
                    "pilot:B",
                    {
                        "action_id": "choose",
                        "cards": [own_land.ref],
                    },
                )

                self.assertTrue(result.ok, result.summary)
                self.assertEqual("hand", own_land.zone)
                self.assertEqual("battlefield", bounce.zone)
                self.assertTrue(bounce.tapped)

    def test_prismatic_vista_finds_only_basic_lands(self):
        session = self.make_session(860)
        engine = session.engine
        vista = self.card(engine, "B", "Prismatic Vista")
        forest = self.card(engine, "B", "Forest")
        breeding_pool = self.card(engine, "B", "Breeding Pool")
        for card in (forest, breeding_pool):
            engine.move_card(card.object_id, "library", log=False)
        engine.move_card(
            vista.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        ability = next(
            ability
            for ability in engine._activated_abilities(vista)
            if ability.ability_id == "ab1"
        )

        self.assertEqual(
            ("basic land",),
            ability.library_search_types,
        )
        options = {
            row["id"]
            for row in engine._fetch_land_options("B", ("basic land",))
        }
        self.assertIn(forest.ref, options)
        self.assertNotIn(breeding_pool.ref, options)

        before_life = engine.state.players["B"].life
        engine.state.priority_player = "B"
        engine._activate(
            "B",
            {
                "source": vista.ref,
                "ability": "ab1",
                "search_card": forest.ref,
            },
        )
        self.resolve_top(engine)
        self.assertEqual("battlefield", forest.zone)
        self.assertFalse(forest.tapped)
        self.assertEqual("graveyard", vista.zone)
        self.assertEqual(before_life - 1, engine.state.players["B"].life)

    def test_buried_and_academy_ruins_use_exact_graveyard_destinations(self):
        cases = (
            ("Buried Ruin", {"C": 2}, "hand"),
            ("Academy Ruins", {"C": 1, "U": 1}, "library"),
        )
        for index, (land_name, mana, expected_zone) in enumerate(cases):
            with self.subTest(land=land_name):
                session = self.make_session(861 + index)
                engine = session.engine
                land = self.card(engine, "A", land_name)
                artifact = self.card(engine, "A", "Sol Ring")
                engine.move_card(
                    land.object_id,
                    "battlefield",
                    controller="A",
                    log=False,
                )
                engine.move_card(artifact.object_id, "graveyard", log=False)
                engine.state.players["A"].mana_pool.update(mana)
                engine.state.priority_player = "A"
                engine._activate(
                    "A",
                    {
                        "source": land.ref,
                        "ability": "ab2",
                        "targets": [artifact.ref],
                        "pay": "manual",
                        "payment": mana,
                    },
                )
                self.resolve_top(engine)

                self.assertEqual(expected_zone, artifact.zone)
                if expected_zone == "library":
                    self.assertEqual(
                        artifact.object_id,
                        engine.state.players["A"].zones["library"][-1],
                    )

    def test_minamo_targets_only_legendary_permanents_and_untaps(self):
        session = self.make_session(863)
        engine = session.engine
        minamo = self.card(engine, "B", "Minamo, School at Water's Edge")
        zimone = self.card(engine, "B", "Zimone and Dina")
        island = self.card(engine, "B", "Island")
        for card in (minamo, zimone, island):
            engine.move_card(
                card.object_id,
                "battlefield",
                controller="B",
                tapped=card is zimone,
                log=False,
            )
        engine.state.players["B"].mana_pool["U"] = 1
        engine.state.priority_player = "B"
        engine._activate(
            "B",
            {
                "source": minamo.ref,
                "ability": "ab2",
                "targets": [zimone.ref],
                "pay": "manual",
                "payment": {"U": 1},
            },
        )
        self.resolve_top(engine)
        self.assertFalse(zimone.tapped)

        minamo.tapped = False
        engine.state.players["B"].mana_pool["U"] = 1
        engine.state.priority_player = "B"
        with self.assertRaisesRegex(Exception, "target"):
            engine._activate(
                "B",
                {
                    "source": minamo.ref,
                    "ability": "ab2",
                    "targets": [island.ref],
                    "pay": "manual",
                    "payment": {"U": 1},
                },
            )

    def test_ghost_town_only_returns_during_another_players_turn(self):
        session = self.make_session(864)
        engine = session.engine
        ghost_town = self.card(engine, "B", "Ghost Town")
        engine.move_card(
            ghost_town.object_id,
            "battlefield",
            controller="B",
            log=False,
        )
        ability = next(
            ability
            for ability in engine._activated_abilities(ghost_town)
            if ability.ability_id == "ab2"
        )
        engine.state.active_player = "B"
        self.assertEqual(
            "unavailable",
            activation_condition_status(engine, "B", ability)[0],
        )
        engine.state.active_player = "A"
        self.assertEqual(
            "payable",
            activation_condition_status(engine, "B", ability)[0],
        )

        engine.state.priority_player = "B"
        engine._activate(
            "B",
            {"source": ghost_town.ref, "ability": "ab2"},
        )
        self.resolve_top(engine)
        self.assertEqual("hand", ghost_town.zone)

    def test_takenuma_mills_then_returns_a_qualifying_card(self):
        session = self.make_session(865)
        engine = session.engine
        takenuma = self.card(engine, "B", "Takenuma, Abandoned Mire")
        target = self.card(engine, "B", "Deathrite Shaman")
        filler_one = self.card(engine, "B", "Island")
        filler_two = self.card(engine, "B", "Forest")
        engine.move_card(takenuma.object_id, "hand", log=False)
        for card in (filler_one, filler_two, target):
            engine.move_card(
                card.object_id,
                "library",
                position="top",
                log=False,
            )
        engine.state.players["B"].mana_pool.update({"C": 3, "B": 1})
        engine.state.priority_player = "B"
        engine._activate(
            "B",
            {
                "source": takenuma.ref,
                "ability": "ab2",
                "pay": "manual",
                "payment": {"C": 3, "B": 1},
            },
        )
        self.resolve_top(engine)

        packet = session.packet("pilot:B", full=True)
        options = {
            row["id"] for row in packet["decision"]["ctx"]["objects"]
        }
        self.assertIn(target.ref, options)
        result = session.act(
            "pilot:B",
            {
                "action_id": "choose",
                "card": target.ref,
                "plan": "RECUR_VALUE",
                "reason": "Return the creature milled by Takenuma.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", takenuma.zone)
        self.assertEqual("graveyard", filler_one.zone)
        self.assertEqual("graveyard", filler_two.zone)

    def test_inventors_fair_checks_metalcraft_for_upkeep_and_search(self):
        session = self.make_session(866)
        engine = session.engine
        fair = self.card(engine, "A", "Inventors' Fair")
        artifacts = [
            self.card(engine, "A", name)
            for name in ("Sol Ring", "Ichor Wellspring", "Idol of Oblivion")
        ]
        target = self.card(engine, "A", "Panharmonicon")
        engine.move_card(
            fair.object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        for artifact in artifacts[:2]:
            engine.move_card(
                artifact.object_id,
                "battlefield",
                controller="A",
                log=False,
            )
        engine.state.active_player = "A"
        engine.state.phase = "beginning"
        engine.state.step = "upkeep"
        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine.state.stack)

        engine.move_card(
            artifacts[2].object_id,
            "battlefield",
            controller="A",
            log=False,
        )
        before_life = engine.state.players["A"].life
        engine._dispatch_semantic_event(
            "step.begin",
            {"phase": "beginning", "step": "upkeep", "player": "A"},
        )
        self.assertFalse(engine._stabilize())
        self.assertEqual([('triggered_ability',fair.object_id)], [
            (item.kind,item.source_object_id) for item in engine.state.stack
        ])
        self.resolve_top(engine)
        self.assertEqual(before_life + 1, engine.state.players["A"].life)

        engine.move_card(target.object_id, "library", log=False)
        engine.state.players["A"].mana_pool["C"] = 4
        engine.state.priority_player = "A"
        engine._activate(
            "A",
            {
                "source": fair.ref,
                "ability": "ab3",
                "pay": "manual",
                "payment": {"C": 4},
            },
        )
        self.resolve_top(engine)
        packet = session.packet("pilot:A", full=True)
        search_refs = {
            row["id"]
            for row in packet["decision"]["ctx"]["search_cards"]
        }
        self.assertIn(target.ref, search_refs)
        result = session.act(
            "pilot:A",
            {
                "action_id": "choose",
                "search_card": target.ref,
                "plan": "FIND_ENGINE",
                "reason": "Find the artifact engine piece.",
            },
        )
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", target.zone)
        self.assertEqual("graveyard", fair.zone)


class GhostTownNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "ghost-town.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/scryfall-exact-lists.json",
            ROOT / "tests/fixtures/actual-card-play-and-fixed-cleanup-limit-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Land return", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def set_priority_window(self, game, active, first):
        engine = game.engine
        engine.permissions.invalidate_current()
        game.state.pending_decision = None
        game.state.active_player = active
        game.state.last_normal_turn_player = active
        game.state.started = True
        game.state.phase, game.state.step = "precombat_main", "main"
        game.state.phase_index = TURN_STEPS.index((game.state.phase, game.state.step))
        game.state.priority_passes = []
        engine._grant_priority(first)
        engine.pump()

    def action_ids(self, game, seat):
        packet = game.packet(f"pilot:{seat}", full=True)
        return {row["id"] for row in packet["decision"]["ctx"]["legal"]["actions"]}

    def act(self, game, seat, action, **payload):
        result = game.act(f"pilot:{seat}", {"action_id": action, **payload})
        self.assertTrue(result.ok, result.summary)

    def test_ghost_town_native_mana_and_controller_turn_preserve_owner_return(self):
        game = self.session(86401)
        source = self.add(game.engine, "Ghost Town")
        game.engine.change_control(source.object_id, "B", reason="Foreign ownership fixture")
        self.assert_closed(game, source)
        mana_key = f"activate:{source.ref}:ab1"
        return_key = f"activate:{source.ref}:ab2"
        self.set_priority_window(game, "B", "B")
        self.assertNotIn(return_key, self.action_ids(game, "B"))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": return_key}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.assertIn(mana_key, self.action_ids(game, "B"))
        self.checkpoint(game)
        mana_before = game.state.players["B"].mana_pool["C"]
        self.act(game, "B", mana_key)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(mana_before + 1, game.state.players["B"].mana_pool["C"])
        self.assertFalse(game.state.stack)
        self.replay(game)

        # B controls the land on its owner A's turn. A and B are different roles.
        self.set_priority_window(game, "A", "A")
        self.assertNotIn(return_key, self.action_ids(game, "A"))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", {"action_id": return_key}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.checkpoint(game)
        self.act(game, "A", "pass")
        self.assertIn(return_key, self.action_ids(game, "B"))
        self.assertNotIn(mana_key, self.action_ids(game, "B"))
        mana_before = dict(game.state.players["B"].mana_pool)
        self.act(game, "B", return_key)
        item, = game.state.stack
        self.assertEqual("B", item.controller)
        self.assertFalse(item.targets)
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual(mana_before, game.state.players["B"].mana_pool)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertIn(source.object_id, game.state.players["A"].zones["hand"])
        self.assertNotIn(source.object_id, game.state.players["B"].zones["hand"])
        self.replay(game)

    def test_ghost_town_native_pending_return_ignores_reentered_incarnation(self):
        game = self.session(86402)
        source = self.add(game.engine, "Ghost Town")
        spiral = self.add(game.engine, "Growth Spiral", zone="hand")
        self.assert_closed(game, source)
        self.assert_closed(game, spiral)
        self.set_priority_window(game, "C", "C")
        game.state.players["A"].mana_pool.update({"G": 1, "U": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        original = game.state.cards[source.object_id].logical_object_id
        return_key = f"activate:{source.ref}:ab2"
        self.act(game, "A", return_key)
        self.priority(game, "A")
        self.act(game, "A", return_key)
        self.assertEqual(2, len(game.state.stack))
        self.resolve_top(game)
        self.assertIn(source.object_id, game.state.players["A"].zones["hand"])
        self.assertEqual(1, len(game.state.stack))
        self.cast_for(game, "A", spiral)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.act(game, "A", "choose", card=source.ref)
        reentered = game.state.cards[source.object_id]
        self.assertEqual("battlefield", reentered.zone)
        self.assertNotEqual(original, reentered.logical_object_id)
        replacement_id = reentered.logical_object_id
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertFalse(game.state.stack)
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        self.assertEqual(replacement_id, current.logical_object_id)
        self.replay(game)


if __name__ == "__main__":
    unittest.main()
