"""Native and shape witnesses for tokens earned by committed creature exile."""

from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
import json

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.descriptor_requirements import typed_result_amount_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import targeted_exile_token_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.committed_quantity_followups import COMMITTED_TOKEN_CREATOR_COUNTS_FIELD
from quorune.rules.targeted_exile_token_results import TARGET_EXILE_TOKEN_CAPABILITY, TargetedExileTokenFollowup
from quorune.semantic_choices.simultaneous_movement import commit_simultaneous_movement
from quorune.semantic_runtime.targeted_exile_token_intent import TargetedExileTokensIntent
from scripts.build_test_database import build_fixture_database


class TargetedExileTokenTests(InheritedNativeFixture, TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "target-exile-tokens.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/targeted-exile-token-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed targeted exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _lowered(self, name):
        card = self.db.lookup(name, fuzzy=False)
        return compile_oracle_card(card, capability_registry=self.registry, capability_profile="commander_review")

    def _issued_cast(self, game, card):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row.get("card") == card.ref)

    def _tokens(self, game, name, expected, power):
        tokens = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual(expected, Counter(card.controller for card in tokens))
        self.assertTrue(all(card.printed_name == name and card.owner == card.controller for card in tokens))
        self.assertEqual(1, len({card.zone_timestamp for card in tokens}))
        for token in tokens:
            self.assertEqual(power, game.engine._numeric_stat(token.object_id, "power"))
        return tokens

    def test_original_shapes_and_payload_minimum_are_closed(self):
        for name, parameter in (("Curse of the Swine", "x"), ("Hour of Need", "any_number")):
            with self.subTest(name=name):
                ir = self._lowered(name)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = (n for face in ir.faces for n in face.nodes if n.effects)
                self.assertEqual(parameter, node.target_schema["count_parameter"]["parameter"])
                self.assertIn(TARGET_EXILE_TOKEN_CAPABILITY, node.capability_dependencies)
                effect = thaw_value(node.effects[0])
                dependencies = typed_result_amount_runtime_dependencies(effect)
                self.assertIn(TARGET_EXILE_TOKEN_CAPABILITY, dependencies)
                self.assertIn("token.creation.fixed_distributed", dependencies)
                followup = effect["followup"]
                for bad in (
                    dict(followup, schema_version=True),
                    dict(followup, token={**followup["token"], "controller": "B"}),
                    dict(followup, token={**followup["token"], "quantity": 2}),
                ):
                    with self.assertRaises(ValueError):
                        TargetedExileTokenFollowup.from_dict(bad)

    def test_paid_x_counts_only_surviving_creatures_and_uses_last_controllers(self):
        game = self.session(58102701)
        curse = self.add(game.engine, "Curse of the Swine", zone="hand")
        theft = self.add(game.engine, "Act of Treason", zone="hand")
        stolen = self.add(game.engine, "Grizzly Bears", seat="B", ref="exile-stolen")
        doubled = self.add(game.engine, "Grizzly Bears", seat="B", ref="exile-doubled")
        stale = self.add(game.engine, "Grizzly Bears", seat="C", ref="exile-stale")
        bounce = self.add(game.engine, "Unsummon", seat="C", zone="hand")
        self.add(game.engine, "Parallel Lives", seat="B")
        hidden = self.add(game.engine, "Generic Bound Growth", seat="D", zone="hand", ref="exile-hidden")
        self.assert_closed(game, curse)
        self.prepare(game, mana={"C": 5, "U": 2, "R": 1})
        game.state.players["C"].mana_pool.update({"U": 1})
        self.checkpoint(game)
        self.cast(game, "A", theft, [stolen.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("B", "A"), (stolen.owner, game.state.cards[stolen.object_id].controller))
        offer = self._issued_cast(game, curse)
        targets = [stolen.ref, doubled.ref, stale.ref]
        command = {"action_id": offer["id"], "targets": targets, "x": 3, "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertFalse(game.act("pilot:A", {**command, "targets": [stolen.ref] * 3}).ok)
        self.assertFalse(game.act("pilot:A", {**command, "x": 2}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "C", bounce, [stale.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[stale.object_id].zone)
        self.assertEqual(["exile", "exile"], [game.state.cards[card.object_id].zone for card in (stolen, doubled)])
        self._tokens(game, "Boar Token", {"A": 1, "B": 2}, 2)
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_hour_of_need_pays_strive_and_resolves_the_empty_selection(self):
        game = self.session(58102702)
        spell = self.add(game.engine, "Hour of Need", zone="hand")
        empty = self.add(game.engine, "Hour of Need", zone="hand", ref="exile-empty-hour")
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="exile-b-creature")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="exile-c-creature")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"C": 5, "U": 3})
        self.checkpoint(game)
        self.cast(game, "A", empty)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], [card for card in game.state.cards.values() if card.is_token])
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(5, sum(game.state.players["A"].mana_pool.values()))
        self.cast(game, "A", spell, [creature.ref, other.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        for token in self._tokens(game, "Sphinx Token", {"B": 1, "C": 1}, 4):
            self.assertIn("flying", game.engine._combat_keywords(token))
        self.replay(game, load=True)

    def test_only_actual_exile_receipts_count_and_omitting_the_lowering_is_detected(self):
        ir = self._lowered("Curse of the Swine")
        node, = (n for face in ir.faces for n in face.nodes if n.effects)
        followup = TargetedExileTokenFollowup.from_dict(node.effects[0]["followup"])
        cards = {
            "one": SimpleNamespace(object_id="first", logical_object_id="before-first", controller="A"),
            "two": SimpleNamespace(object_id="second", logical_object_id="before-second", controller="B"),
            "three": SimpleNamespace(object_id="third", logical_object_id="before-third", controller="C"),
        }
        moved = [
            SimpleNamespace(object_id="first", logical_object_id="after-first", zone="exile"),
            SimpleNamespace(object_id="second", logical_object_id="after-second", zone="graveyard"),
            SimpleNamespace(object_id="third", logical_object_id="before-third", zone="exile"),
        ]
        host = SimpleNamespace(
            _resolve_object=lambda actor, ref, **ranges: cards[ref],
            _move_cards_simultaneously=lambda changes, **options: moved,
        )
        intent = TargetedExileTokensIntent("A", ("one", "two", "three"), ("battlefield",), "exile", "test", followup=followup)
        result = commit_simultaneous_movement(host, intent)
        effect, = result.effects
        self.assertEqual({"A": 1}, dict(effect[COMMITTED_TOKEN_CREATOR_COUNTS_FIELD]))
        with patch.object(targeted_exile_token_templates, "targeted_exile_token_template", return_value=None):
            self.assertNotEqual("exact", self._lowered("Curse of the Swine").status)
            self.assertNotEqual("exact", self._lowered("Hour of Need").status)

    def test_frogs_closed_result_rejects_an_unowned_selector_or_version(self):
        from quorune.rules.opponent_exile_draw_results import (
            OPPONENT_EXILE_DRAW_CAPABILITY, OPPONENT_EXILE_DRAW_FIELD, opponent_exile_draw_payload_dependencies,
        )
        from quorune.rules.per_player_target_sets import per_player_target_node_capabilities

        ir = self._lowered("King Solomon's Frogs")
        self.assertEqual("exact", ir.status, ir.material_residuals)
        nodes = ir.faces[0].nodes
        node = next(value for value in nodes if value.event == "permanent.enter.self" and value.effects)
        self.assertEqual({"field": "source_entry_cast_by_controller", "op": "eq", "value": True}, node.event_condition)
        self.assertIn("cast.permission", {value.event for value in nodes})
        self.assertIn("activate", {value.event for value in nodes})
        self.assertIn(OPPONENT_EXILE_DRAW_CAPABILITY, node.capability_dependencies)
        self.assertEqual(3, node.target_schema["mana_value_min"])
        effect = thaw_value(node.effects[0])
        for changed in (
            {**effect, OPPONENT_EXILE_DRAW_FIELD: True},
            {**effect, "cards": ["unbound"]},
            {**effect, "maximum_targets": 2},
        ):
            with self.assertRaises(ValueError):
                opponent_exile_draw_payload_dependencies(changed)
        for field, value in (("retain_controller", False), ("distinct_controllers", False), ("controller_relation", "any")):
            schema = {**node.target_schema, field: value}
            self.assertEqual((), per_player_target_node_capabilities(
                effects=node.effects, target_schema=schema, mechanic_ids=node.mechanics,
            ))

    def test_actual_frogs_exiles_per_opponent_and_draws_for_prior_controllers(self):
        for stale in (False, True):
            with self.subTest(stale=stale):
                session = self.session(58102710 + stale)
                frogs = self.add(session.engine, "King Solomon's Frogs", zone="hand")
                borrowed = self.add(session.engine, "Hill Giant", seat="B", ref="frogs-borrowed")
                borrowed.owner = "D"
                second = self.add(session.engine, "Hill Giant", seat="B", ref="frogs-second")
                opposing = self.add(session.engine, "Air Elemental", seat="C")
                low = self.add(session.engine, "Grizzly Bears", seat="D")
                own = self.add(session.engine, "Hill Giant", ref="frogs-own")
                unsummon = self.add(session.engine, "Unsummon", seat="B", zone="hand")
                self.add(session.engine, "Island", seat="B")
                private = self.add(session.engine, "Generic Bound Growth", seat="B", zone="library", ref="frogs-private")
                library = session.state.players["B"].zones["library"]
                library.remove(private.object_id)
                library.append(private.object_id)
                self.assert_closed(session, frogs)
                self.prepare(session, actor="A", active="B", mana={"C": 3, "W": 1})
                self.checkpoint(session)
                self.cast(session, "A", frogs)
                decision = self.resolve(session)
                self.assertEqual("semantic.target", decision.kind)
                self.assertEqual(["pilot:A"], session.pending_principals())
                before = authoritative_state_hash(session.state)
                for targets in ([borrowed.ref, second.ref], [low.ref], [own.ref]):
                    rejected = session.act("pilot:A", {"action_id": "choose", "targets": targets})
                    self.assertFalse(rejected.ok)
                request = {"action_id": "choose", "targets": [opposing.ref, borrowed.ref]}
                self.assertFalse(session.act("pilot:C", request).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                session = self.replay(session, load=True)
                chosen = session.act("pilot:A", request)
                self.assertTrue(chosen.ok, chosen.summary)
                if stale:
                    self.cast(session, "B", unsummon, [opposing.ref])
                    self.resolve_top(session)
                    self.assertEqual("hand", session.state.cards[opposing.object_id].zone)
                session = self.replay(session, load=True)
                sizes = {seat: len(player.zones["hand"]) for seat, player in session.state.players.items()}
                self.assertIsNone(self.resolve(session))
                self.assertEqual("exile", session.state.cards[borrowed.object_id].zone)
                self.assertEqual("hand" if stale else "exile", session.state.cards[opposing.object_id].zone)
                self.assertEqual("battlefield", session.state.cards[second.object_id].zone)
                expected = {"A": 0, "B": 1, "C": int(not stale), "D": 0}
                self.assertEqual(expected, {seat: len(player.zones["hand"]) - sizes[seat]
                                            for seat, player in session.state.players.items()})
                self.assertIn(private.object_id, session.state.players["B"].zones["hand"])
                self.assertIn(private.ref, json.dumps(session.packet("pilot:B", full=True)))
                for seat in "ACD":
                    self.assertNotIn(private.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
                self.replay(session, load=True)

    def test_actual_frogs_entering_without_a_cast_never_offers_exile_targets(self):
        session = self.session(58102712)
        frogs = self.add(session.engine, "King Solomon's Frogs")
        target = self.add(session.engine, "Hill Giant", seat="B")
        flicker = self.add(session.engine, "Flicker", zone="hand")
        self.assert_closed(session, frogs)
        self.prepare(session, mana={"C": 4, "W": 1})
        self.checkpoint(session)
        incarnation = frogs.logical_object_id
        self.cast(session, "A", flicker, [frogs.ref])
        self.assertIsNone(self.resolve(session))
        self.assertNotEqual(incarnation, session.state.cards[frogs.object_id].logical_object_id)
        self.assertEqual("battlefield", session.state.cards[target.object_id].zone)
        current = session.state.cards[frogs.object_id]
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offer = next(row for row in actions if row["id"].startswith(f"activate:{current.ref}:"))
        accepted = session.act("pilot:A", {"action_id": offer["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.monarch)
        self.assertEqual("exile", session.state.cards[frogs.object_id].zone)
        self.replay(session, load=True)

    def test_exile_draw_reads_actual_commits_and_keeps_apnap_order(self):
        from quorune.errors import GameRuleError

        before = {
            "one": SimpleNamespace(object_id="a", logical_object_id="old-a", controller="B"),
            "two": SimpleNamespace(object_id="b", logical_object_id="old-b", controller="C"),
            "three": SimpleNamespace(object_id="c", logical_object_id="old-c", controller="D"),
        }
        result = [
            SimpleNamespace(object_id="c", logical_object_id="new-c", zone="exile"),
            SimpleNamespace(object_id="b", logical_object_id="new-b", zone="graveyard"),
            SimpleNamespace(object_id="a", logical_object_id="new-a", zone="exile"),
        ]
        from quorune.semantic_runtime.targeted_exile_token_intent import OpponentExileDrawIntent
        host = SimpleNamespace(
            _resolve_object=lambda actor, ref, **ranges: before[ref],
            _move_cards_simultaneously=lambda changes, **options: result,
            apnap_order=lambda: ("B", "C", "D", "A"),
        )
        intent = OpponentExileDrawIntent("A", ("one", "two", "three"), ("battlefield",), "exile", "test")
        expected = [{"op": "draw_each_player", "players": ["B", "D"], "count": 1, "private": True}]

        def witness():
            followup = commit_simultaneous_movement(host, intent)
            self.assertEqual(expected, [thaw_value(effect) for effect in followup.effects])

        witness()
        from quorune.rules.opponent_exile_draw_results import opponent_exile_draw_followup
        with patch("quorune.rules.opponent_exile_draw_results.opponent_exile_draw_followup",
                   side_effect=lambda recipients: opponent_exile_draw_followup((*recipients, "C"))):
            with self.assertRaises(AssertionError):
                witness()
        before["two"].controller = "B"
        with self.assertRaises(GameRuleError):
            commit_simultaneous_movement(host, intent)
