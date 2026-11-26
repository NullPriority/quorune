from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.abilities import ActivatedAbility
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.land_play_mana_model import LAND_PLAY_MANA_CAPABILITY, LandPlayManaOutput
from quorune.mana_ability_runtime import _land_ability_colors
from quorune.oracle_ir import compile_oracle_card
from quorune.preflight import semantic_preflight
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.mana_abilities import fixed_mana_specs_from_descriptors
from quorune.turn_history import land_played_this_turn, reset_turn_history
from quorune.turn_history_model import LAND_PLAY_HISTORY_EVENT, TurnHistory, TurnHistoryEvent
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_reflexive_payments as witnesses


PRINTED = "{T}: Add {U}. If you played a land this turn, add {B} instead."
PROFILE = "commander_review"


class LandPlayManaModelTests(unittest.TestCase):
    def test_committed_fact_is_current_player_scoped_and_legacy_shape_preserved(self):
        from types import SimpleNamespace

        prior = TurnHistory(turn_sequence=4)
        self.assertNotIn("land_play_history_version", prior.to_dict())
        self.assertEqual(prior.to_dict(), TurnHistory.from_dict(prior.to_dict()).to_dict())
        old = SimpleNamespace(turn_sequence=4, turn_history=prior)
        self.assertIsNone(land_played_this_turn(old, "A"))
        event = TurnHistoryEvent(LAND_PLAY_HISTORY_EVENT, actor="A", object_incarnation="land@1")
        typed = TurnHistory(turn_sequence=4, events=[event], land_play_history_version=1)
        state = SimpleNamespace(turn_sequence=4, turn_history=typed)
        self.assertTrue(land_played_this_turn(state, "A"))
        self.assertFalse(land_played_this_turn(state, "B"))
        self.assertEqual(typed, TurnHistory.from_dict(typed.to_dict()))
        for forged in (None, True, 2):
            with self.subTest(forged=forged), self.assertRaises(ValueError):
                TurnHistory.from_dict({**typed.to_dict(), "land_play_history_version": forged})
        with self.assertRaises(ValueError):
            TurnHistory(turn_sequence=4, events=[event])
        with self.assertRaises(ValueError):
            TurnHistoryEvent(LAND_PLAY_HISTORY_EVENT, actor="A")
        state.turn_sequence = 5
        self.assertIsNone(land_played_this_turn(state, "A"))
        state.turn_history = reset_turn_history(typed, 5)
        self.assertFalse(land_played_this_turn(state, "A"))
        self.assertEqual(1, state.turn_history.land_play_history_version)

    def test_compiler_rejects_partial_clauses_targets_and_competing_prices(self):
        registry = load_default_capability_registry()
        land = replace(record(PRINTED), type_line="Land", mana_cost="", mana_value=0.0)
        ir = compile_oracle_card(land, capability_registry=registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("mana_ability", node.kind)
        self.assertIn(LAND_PLAY_MANA_CAPABILITY, node.capability_dependencies)
        spec, = fixed_mana_specs_from_descriptors(node.handlers)
        self.assertEqual(LandPlayManaOutput("U", "B"), spec.output)
        ability = spec.to_activated_ability().to_dict()
        self.assertEqual(ability, ActivatedAbility.from_dict(ability).to_dict())
        for change in ({"played_color":"U"}, {"normal_color":"X"}, {"schema_version":True},
                       {"opponent":True}):
            invalid = deepcopy(ability)
            invalid["dynamic_mana_output"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                ActivatedAbility.from_dict(invalid)
        for changed in (
            PRINTED.replace("you played", "an opponent played"),
            PRINTED.replace("played a land", "put a land onto the battlefield"),
            PRINTED.replace("{T}:", "{1}, {T}:"),
            PRINTED.replace("this turn", "last turn"),
            PRINTED + " Draw a card.",
        ):
            with self.subTest(changed=changed):
                result = compile_oracle_card(replace(land, oracle_text=changed),
                                            capability_registry=registry, capability_profile=PROFILE)
                self.assertFalse(any(LAND_PLAY_MANA_CAPABILITY in item.capability_dependencies
                                     for face in result.faces for item in face.nodes))


class LandPlayManaRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    helper = witnesses.ReflexivePaymentRuntimeTests
    session = helper.session
    add = helper.add
    prepare = helper.prepare
    checkpoint = helper.checkpoint
    resolve = helper.resolve
    replay = helper.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "land-play-mana.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "current-land-play-mana-cards.json",
            fixtures / "incubation-druid-mana-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Land-play mana witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 24),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def river(self, game):
        source = self.add(game.engine, "River of Tears", seat="B", zone="exile", ref="river-source")
        game.engine.move_card(source.object_id, "battlefield", controller="A", reason="fixture control", log=False)
        self.assertEqual(("B", "A"), (source.owner, source.controller))
        return source

    def river_colors(self, game, river):
        ability, = game.engine._activated_abilities(river)
        return _land_ability_colors(game.engine, river.controller, river, ability)

    def actions(self, game, actor):
        return game.packet("pilot:" + actor, full=True)["decision"]["ctx"]["legal"]["actions"]

    def find(self, game, card, actor="A"):
        return next((row for row in self.actions(game, actor)
                    if row.get("source") == card.ref or row.get("card") == card.ref), None)

    def play(self, game, land, actor="A"):
        offer = self.find(game, land, actor)
        self.assertIsNotNone(offer)
        self.assertEqual("play_land", offer["action"])
        result = game.act("pilot:" + actor, {"action_id": offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)

    def cast(self, game, card):
        offer = self.find(game, card)
        self.assertIsNotNone(offer)
        result = game.act("pilot:A", {"action_id": offer["id"], "pay":"auto"})
        self.assertTrue(result.ok, result.summary)

    def activate(self, game, river, color):
        offer = self.find(game, river)
        self.assertIsNotNone(offer)
        self.assertEqual({color}, self.river_colors(game, river))
        before = authoritative_state_hash(game.state)
        other = "B" if color == "U" else "U"
        for principal, output in (("pilot:B", {color:1}), ("pilot:A", {other:1}), ("pilot:A", {color:2})):
            denied = game.act(principal, {"action_id":offer["id"], "mana_output":output})
            self.assertFalse(denied.ok, denied.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id":offer["id"]})
        self.assertTrue(result.ok, result.summary)
        self.assertFalse(game.state.stack)
        self.assertTrue(game.state.cards[river.object_id].tapped)
        self.assertEqual({color:1}, {key:value for key,value in game.state.players["A"].mana_pool.items() if value})
        self.assertEqual(0, sum(game.state.players["B"].mana_pool.values()))

    def test_actual_blue_and_black_activation_follow_the_playing_controller_not_owner(self):
        for played in (False, True):
            with self.subTest(played=played):
                game = self.session(305400 + played)
                river = self.river(game)
                land = self.add(game.engine, "Plains", zone="hand", ref="river-plains")
                private = self.add(game.engine, "Land Play Mana Witness", seat="C", zone="hand", ref="river-private-c")
                self.prepare(game)
                self.checkpoint(game)
                if played:
                    self.play(game, land)
                self.assertIs(played, land_played_this_turn(game.state, "A"))
                self.assertFalse(land_played_this_turn(game.state, "B"))
                self.activate(game, river, "B" if played else "U")
                for seat in ("A", "B", "D"):
                    self.assertNotIn(private.ref, str(game.packet("pilot:" + seat, full=True)))
                self.replay(game, load=True)

    def test_put_land_does_not_change_mana_but_actual_play_funds_black_spell(self):
        game = self.session(305410)
        river = self.river(game)
        ramp = self.add(game.engine, "Rampant Growth", zone="hand")
        placed = self.add(game.engine, "Swamp", zone="library", ref="river-effect-swamp")
        played = self.add(game.engine, "Plains", zone="hand", ref="river-native-plains")
        spell = self.add(game.engine, "Land Play Mana Witness", zone="hand", ref="river-black-output")
        self.prepare(game, mana={"C":1,"G":1})
        self.checkpoint(game)
        self.cast(game, ramp)
        self.assertEqual("semantic.search", self.resolve(game).kind)
        accepted = game.act("pilot:A", {"action_id":"choose", "search_cards":[placed.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("battlefield", game.state.cards[placed.object_id].zone)
        self.assertTrue(game.state.cards[placed.object_id].tapped)
        self.assertFalse(land_played_this_turn(game.state, "A"))
        self.assertEqual({"U"}, self.river_colors(game, river))
        self.assertIsNone(self.find(game, spell))
        self.play(game, played)
        self.assertTrue(land_played_this_turn(game.state, "A"))
        game = self.replay(game, load=True)
        self.cast(game, spell)
        self.assertTrue(game.state.cards[river.object_id].tapped)
        self.assertFalse(game.state.cards[played.object_id].tapped)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.replay(game, load=True)

    def test_previous_turn_and_an_opponents_land_play_keep_the_current_output_blue(self):
        game = self.session(305411)
        river = self.river(game)
        our_land = self.add(game.engine, "Plains", zone="hand", ref="river-first-turn-land")
        their_land = self.add(game.engine, "Plains", seat="B", zone="hand", ref="river-second-turn-land")
        self.prepare(game)
        self.checkpoint(game)
        self.play(game, our_land)
        self.assertTrue(land_played_this_turn(game.state, "A"))
        for _ in range(128):
            if (game.state.active_player, game.state.phase, game.state.priority_player) == ("B", "precombat_main", "B"):
                break
            principal, = game.pending_principals()
            result = game.act(principal, {"action_id":"pass"})
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("The next player's main phase never received priority")
        self.play(game, their_land, actor="B")
        self.assertTrue(land_played_this_turn(game.state, "B"))
        self.assertFalse(land_played_this_turn(game.state, "A"))
        for _ in range(4):
            if game.state.priority_player == "A":
                break
            principal, = game.pending_principals()
            self.assertTrue(game.act(principal, {"action_id":"pass"}).ok)
        self.assertEqual("A", game.state.priority_player)
        self.activate(game, river, "U")
        self.replay(game, load=True)

    def test_legacy_history_omits_new_facts_and_any_unsealed_mana_offer(self):
        game = self.session(305412)
        river = self.river(game)
        druid = self.add(game.engine, "Incubation Druid")
        land = self.add(game.engine, "Plains", zone="hand", ref="river-legacy-land")
        game.state.turn_history.land_play_history_version = None
        self.prepare(game)
        self.checkpoint(game)
        self.assertIsNone(self.find(game, river))
        self.play(game, land)
        self.assertIsNone(land_played_this_turn(game.state, "A"))
        raw = game.state.turn_history.to_dict()
        self.assertNotIn("land_play_history_version", raw)
        self.assertFalse(any(event["kind"] == LAND_PLAY_HISTORY_EVENT for event in raw["events"]))
        self.assertIsNone(self.find(game, river))
        action = f"activate:{druid.ref}:ab1"
        before = authoritative_state_hash(game.state)
        for color in ("U", "B"):
            self.assertFalse(game.act("pilot:A", {"action_id": action, "mana_output": {color: 1}}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        accepted = game.act("pilot:A", {"action_id": action, "mana_output": {"W": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({"W": 1}, {
            color: quantity for color, quantity in game.state.players["A"].mana_pool.items() if quantity
        })
        resumed = self.replay(game, load=True)
        self.assertEqual(raw, resumed.state.turn_history.to_dict())

    def test_druid_uses_the_current_output_of_a_tapped_river_before_and_after_land_play(self):
        from quorune.rules.mana_pool_payment import PoolManaUnit

        game = self.session(305414)
        river = self.river(game)
        river.tapped = True
        played = self.add(game.engine, "River of Tears", zone="hand", ref="druid-played-river")
        druid = self.add(game.engine, "Incubation Druid")
        untap = self.add(game.engine, "Vitalize", zone="hand")
        self.prepare(game, mana={"C": 3, "G": 3})
        self.checkpoint(game)

        action = f"activate:{druid.ref}:ab1"
        self.assertIn(action, [row["id"] for row in self.actions(game, "A")])
        self.assertFalse(land_played_this_turn(game.state, "A"))
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": action, "mana_output": {"B": 1}}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        first = game.act("pilot:A", {"action_id": action, "mana_output": {"U": 1}})
        self.assertTrue(first.ok, first.summary)
        self.assertEqual(1, game.state.players["A"].mana_pool["U"])
        self.assertFalse(game.state.stack)

        self.play(game, played)
        self.assertTrue(land_played_this_turn(game.state, "A"))
        adapt = game.act("pilot:A", {"action_id": f"activate:{druid.ref}:ab2", "mana_pool_payment": {PoolManaUnit("C").key: 3, PoolManaUnit("G").key: 2}})
        self.assertTrue(adapt.ok, adapt.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3, game.state.cards[druid.object_id].counters["+1/+1"])
        self.cast(game, untap)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.cards[druid.object_id].tapped)
        self.assertIn(action, [row["id"] for row in self.actions(game, "A")])

        before = authoritative_state_hash(game.state)
        for illegal in ({"U": 3}, {"B": 1}):
            self.assertFalse(game.act("pilot:A", {"action_id": action, "mana_output": illegal}).ok)
            self.assertEqual(before, authoritative_state_hash(game.state))
        last = game.act("pilot:A", {"action_id": action, "mana_output": {"B": 3}})
        self.assertTrue(last.ok, last.summary)
        self.assertEqual({"U": 1, "B": 3}, {
            color: quantity for color, quantity in game.state.players["A"].mana_pool.items() if quantity
        })
        self.assertTrue(game.state.cards[river.object_id].tapped)
        self.assertFalse(game.state.cards[played.object_id].tapped)
        self.assertTrue(game.state.cards[druid.object_id].tapped)
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_pinned_preflight_and_mismatched_catalog_guard(self):
        game = self.session(305413)
        river = self.river(game)
        row, = semantic_preflight(self.db, DeckDefinition("River preflight", [DeckEntry("River of Tears")], []))["cards"]
        self.assertEqual("fully_playable", row["status"])
        self.assertTrue(row["card_program_strict_capability_ready"])
        program, = (item for item in game.engine.semantics.programs_for_oracle(river.oracle_id)
                    if LAND_PLAY_MANA_CAPABILITY in item.capability_dependencies)
        handlers = deepcopy(program.handlers)
        carrier = next(item for item in handlers if item["handler_id"] == "activation.catalog.pinned.v1")
        carrier["ability"]["dynamic_mana_output"]["played_color"] = "R"
        result = bind_semantic_program_runtime(replace(program, handlers=handlers),
                                              capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"])

    def test_native_witness_kills_reusing_the_normal_color_after_play(self):
        def witness(seed):
            game = self.session(seed)
            river = self.river(game)
            land = self.add(game.engine, "Plains", zone="hand", ref="river-mutation-land")
            self.prepare(game)
            self.checkpoint(game)
            self.play(game, land)
            result = game.act("pilot:A", {"action_id":self.find(game, river)["id"]})
            self.assertTrue(result.ok, result.summary)
            self.assertEqual(1, game.state.players["A"].mana_pool["B"])

        witness(305420)
        with patch("quorune.mana_ability_runtime.land_played_this_turn", return_value=False):
            with self.assertRaisesRegex(AssertionError, "1 != 0"):
                witness(305421)
