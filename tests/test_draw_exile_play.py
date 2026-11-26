"""Actual draws yield separate top-two exile results and ordinary turn play."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.impulse_access import current_temporary_play_permission
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.operations import ExileTopForTurnPlayInsteadOfDraw, operation_from_dict
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.draw_exile_play import CAPABILITY, DrawExilePlayReplacementHandler, draw_exile_play_descriptor
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index


class DrawExilePlayTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "draw-exile-play.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/draw-exile-play-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Draw exile play", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def test_complete_descriptor_and_withdrawn_impulse_authority(self):
        source = self.db.lookup("Eruth, Tormented Prophet")
        options = {"capability_registry": self.registry, "capability_profile": "commander_review"}
        code = compile_oracle_card(source, **options)
        self.assertEqual("exact", code.status, code.material_residuals)
        node, = code.faces[0].nodes
        self.assertIn(CAPABILITY, node.capability_dependencies)
        self.assertEqual((draw_exile_play_descriptor(),), tuple(node.handlers))
        for old, new in (("top two cards", "top three cards"), ("play those cards", "cast those cards"),
                         ("this turn", "during your next turn")):
            changed = replace(source, oracle_text=source.oracle_text.replace(old, new))
            self.assertNotEqual("exact", compile_oracle_card(changed, **options).status)
        handler = DrawExilePlayReplacementHandler()
        with self.assertRaises(SemanticNodeError):
            handler.validate({**draw_exile_play_descriptor(), "schema_version": True})
        operation = ExileTopForTurnPlayInsteadOfDraw()
        self.assertEqual(operation, operation_from_dict(operation.to_dict()))
        with self.assertRaises(ValueError):
            operation_from_dict({**operation.to_dict(), "count": 2})
        raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
        row = next(row for row in raw["capabilities"] if row["id"] == "zone.impulse_access.fixed")
        row.update(status="blocked", blockers=["Test-only impulse access withdrawal"])
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        current = read_evidence_document(ROOT / "quorune/rules/capability-evidence.json")
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids={row["test_id"] for row in current["declarations"]})
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        changed = compile_oracle_card(source, **{**options, "capability_registry": registry})
        self.assertNotEqual("exact", changed.status)

    def draw_count(self, game, seat):
        return sum(event.code == "card.draw" and event.actor == seat for event in game.state.events)

    def divination_scene(self, seed):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        source = self.add(game.engine, "Eruth, Tormented Prophet", ref="eruth-provider")
        divination = self.add(game.engine, "Divination", zone="hand")
        self.assert_closed(game, source)
        return game, source, divination

    def opened_divination(self, seed):
        game, source, divination = self.divination_scene(seed)
        land = self.add(game.engine, "Forest", zone="library", ref="exiled-forest")
        bear = self.add(game.engine, "Grizzly Bears", zone="library", ref="exiled-bear")
        top = tuple(reversed(game.state.players["A"].zones["library"][-4:]))
        self.prepare(game, mana={"C": 3, "U": 1, "G": 1})
        self.checkpoint(game)
        before_hand = len(game.state.players["A"].zones["hand"])
        before_draws = self.draw_count(game, "A")
        self.cast_for(game, "A", divination, reserve={"C": 1, "G": 1})
        self.settle(game)
        self.assertEqual(before_hand - 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(before_draws, self.draw_count(game, "A"))
        card_groups = [event.details["objects"] for event in game.state.events if event.code == "library.impulse_access"]
        self.assertEqual([[game.state.cards[oid].ref for oid in top[:2]],
                          [game.state.cards[oid].ref for oid in top[2:]]], card_groups)
        for object_id in top:
            card = game.state.cards[object_id]
            self.assertEqual("exile", card.zone)
            permission = current_temporary_play_permission(game.state, "A", card)
            self.assertIsNotNone(permission)
            self.assertEqual(game.state.turn_sequence, permission["turn_sequence"])
            self.assertTrue(permission["allow_land"] and permission["allow_spell"])
            self.assertFalse(permission["without_mana_cost"])
            self.assertIsNone(current_temporary_play_permission(game.state, "B", card))
        return game, source, land, bear, top

    def test_native_each_draw_exiles_its_own_cards_and_allows_normal_play(self):
        game, source, land, bear, top = self.opened_divination(1217001)
        game = self.replay(game, load=True)
        self.priority(game, "A")
        land_action = f"play-land:{land.ref}"
        packet = game.packet("pilot:A", full=True)["decision"]["ctx"]
        self.assertIn(land_action, {row["id"] for row in packet["legal"]["actions"]})
        result = game.act("pilot:A", {"action_id": land_action})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", game.state.cards[land.object_id].zone)
        self.cast(game, "A", bear)
        self.settle(game)
        self.assertEqual("battlefield", game.state.cards[bear.object_id].zone)
        self.assertEqual(0, game.state.players["A"].mana_pool["G"])
        replayed = self.replay(game, load=True)
        leftover = replayed.state.cards[top[-1]]
        replayed.engine.move_card(source.object_id, "graveyard", log=False)
        self.assertIsNotNone(current_temporary_play_permission(replayed.state, "A", leftover))
        replayed.engine.move_card(leftover.object_id, "hand", log=False)
        replayed.engine.move_card(leftover.object_id, "exile", log=False)
        self.assertIsNone(current_temporary_play_permission(replayed.state, "A", leftover))

    def test_native_short_and_empty_libraries_do_not_fail_the_replaced_draw(self):
        for count in (0, 1):
            with self.subTest(library_count=count):
                game = self.session(1217002 + count)
                game.state.config.semantic_policy = "trusted_only"
                source = self.add(game.engine, "Eruth, Tormented Prophet")
                draw = self.add(game.engine, "Reach Through Mists", zone="hand")
                self.assert_closed(game, source)
                library = tuple(game.state.players["A"].zones["library"])
                for oid in (library if count == 0 else library[:-1]):
                    game.engine.move_card(oid, "graveyard", log=False)
                self.prepare(game, mana={"U": 1})
                self.checkpoint(game)
                old_draws = self.draw_count(game, "A")
                self.cast(game, "A", draw)
                self.settle(game)
                self.assertIn("A", game.engine.active_seats)
                self.assertEqual(old_draws, self.draw_count(game, "A"))
                self.assertEqual(count, len(game.state.players["A"].zones["exile"]))
                self.assertFalse(game.state.players["A"].zones["library"])
                self.replay(game, load=True)

    def choose(self, game, source):
        raw = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("draw.replacement", raw["kind"])
        choice, = [row["id"] for row in raw["ctx"]["options"] if row["label"].startswith(source.ref + ":")]
        command = {"action_id": "choose", "choice": choice}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def test_affected_player_orders_each_draw_and_replays_remaining_draws(self):
        game, source, divination = self.divination_scene(1217004)
        reflection = self.add(game.engine, "Thought Reflection", ref="competing-reflection")
        other = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand")
        self.assert_closed(game, reflection)
        top = set(game.state.players["A"].zones["library"][-6:])
        self.prepare(game, mana={"C": 2, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        before_a, before_b = self.draw_count(game, "A"), self.draw_count(game, "B")
        self.cast(game, "A", divination)
        self.assertEqual("draw.replacement", self.resolve(game).kind)
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        self.choose(game, reflection)
        self.assertEqual(4, len(game.state.players["A"].zones["exile"]))
        self.assertEqual("draw.replacement", game.state.pending_decision.kind)
        self.choose(game, source)
        self.settle(game)
        self.assertEqual(top, set(game.state.players["A"].zones["exile"]))
        self.assertEqual(before_a, self.draw_count(game, "A"))
        self.cast(game, "B", other)
        self.settle(game)
        self.assertEqual(before_b + 1, self.draw_count(game, "B"))
        self.replay(game, load=True)

    def test_removing_the_typed_exile_result_breaks_the_native_draw_witness(self):
        with patch("quorune.impulse_access.resolve_fixed_impulse_access"):
            with self.assertRaises(AssertionError):
                self.opened_divination(1217005)
