"""A revalidated spell leaves the stack ordinarily before one private draw."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_controller_creature_keyword_absence import register_card
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_spell_or_permanent_return import (
    SPELL_CAPABILITY, spell_return_effect, spell_return_target_schema,
)
from scripts.build_test_database import build_fixture_database


CARD = "Reprieve"
PROFILE = "commander_review"


class StackSpellOwnerHandReturnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "stack-spell-owner-hand-return.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "mixed-stack-counter-cards.json",
            fixtures / "stack-spell-owner-hand-return-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Stack spell owner-hand return", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def start(self, game, source, *, b_mana):
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, actor="B", active="B", mana=b_mana)
        game.state.players["A"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)

    @staticmethod
    def recent_drawers(game, sequence):
        return [event.actor for event in game.state.events
                if event.event_id > sequence and event.code == "card.draw"]

    def test_actual_uncounterable_spell_returns_without_counter_and_draw_stays_private(self):
        game = self.session(11201001)
        source = self.add(game.engine, CARD, zone="hand")
        protected = self.add(game.engine, "Carnage Tyrant", seat="B", zone="hand")
        wrong = self.add(game.engine, "Grizzly Bears", seat="B")
        self.assert_closed(game, protected)
        self.start(game, source, b_mana={"C": 4, "G": 2})
        self.cast(game, "B", protected)
        target, = game.state.stack
        previous_incarnation = game.state.cards[protected.object_id].logical_object_id
        self.priority(game, "A")
        offer = self.action(game, "A", source)
        self.assertIn(target.ref, offer["target_schema"]["legal_refs"])
        self.assertNotIn(wrong.ref, offer["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        invalid = {"action_id": offer["id"], "targets": [wrong.ref], "pay": "auto"}
        self.assertFalse(game.act("pilot:A", invalid).ok)
        self.assertFalse(game.act("pilot:D", {**invalid, "targets": [target.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", source, [target.ref])
        sequence = game.state.event_sequence
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))

        returned = game.state.cards[protected.object_id]
        self.assertEqual(("hand", "B"), (returned.zone, returned.owner))
        self.assertNotEqual(previous_incarnation, returned.logical_object_id)
        self.assertFalse(game.state.stack)
        self.assertEqual(["A"], self.recent_drawers(game, sequence))
        self.assertFalse(any(event.code == "stack.counter" and event.event_id > sequence for event in game.state.events))
        drawn = game.state.cards[game.state.players["A"].zones["hand"][-1]].ref
        self.assertIn(drawn, json.dumps(game.packet("pilot:A", full=True)))
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(drawn, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_departed_sole_target_fizzles_the_draw(self):
        game = self.session(11201002)
        source = self.add(game.engine, CARD, zone="hand")
        target_spell = self.add(game.engine, "Opt", seat="B", zone="hand")
        response = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.start(game, source, b_mana={"U": 3})
        self.cast_for(game, "B", target_spell, reserve={"U": 2})
        target, = game.state.stack
        self.cast(game, "A", source, [target.ref])
        self.cast(game, "B", response, [target.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[target_spell.object_id].zone)
        game = self.replay(game, load=True)
        sequence = game.state.event_sequence
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], self.recent_drawers(game, sequence))
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.replay(game, load=True)

    def test_copied_spell_returns_without_leaving_a_card_in_hand(self):
        game = self.session(11201003)
        source = self.add(game.engine, CARD, zone="hand")
        original_card = self.add(game.engine, "Opt", seat="B", zone="hand")
        self.start(game, source, b_mana={"U": 1})
        self.cast(game, "B", original_card)
        original, = game.state.stack
        copy = game.engine._copy_stack_item(controller="A", target=original, targets=(), target_groups={},
                                            reason="Any-controller spell-copy return proof")
        copy_id = copy.card_object_id
        self.checkpoint(game)
        self.cast(game, "A", source, [copy.ref])
        sequence = game.state.event_sequence
        self.resolve_top(game)
        self.assertEqual([original.ref], [item.ref for item in game.state.stack])
        self.assertEqual(["A"], self.recent_drawers(game, sequence))
        self.assertTrue(all(copy_id not in player.zones["hand"] for player in game.state.players.values()))
        self.replay(game, load=True)

    def test_commander_destination_replacement_suspends_then_keeps_the_draw(self):
        game = self.session(11201004)
        source = self.add(game.engine, CARD, zone="hand")
        commander_id, = game.state.players["B"].zones["command"]
        commander = game.state.cards[commander_id]
        self.start(game, source, b_mana={"W": 1})
        self.cast(game, "B", commander)
        target, = game.state.stack
        self.cast(game, "A", source, [target.ref])
        sequence = game.state.event_sequence
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual([], self.recent_drawers(game, sequence))
        game = self.replay(game, load=True)
        options = game.packet("pilot:B", full=True)["decision"]["ctx"]["options"]
        command, = (option["id"] for option in options if not option.get("decline"))
        result = game.act("pilot:B", {"action_id": "choose", "replacement": command})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("command", game.state.cards[commander_id].zone)
        self.assertEqual(["A"], self.recent_drawers(game, sequence))
        self.replay(game, load=True)

    def test_complete_return_and_draw_bind_independent_dependencies_and_reject_neighbors(self):
        card = self.db.lookup(CARD, fuzzy=False)

        def compile_card(record):
            return compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)

        ir = compile_card(card)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        move, draw = ir.faces[0].nodes
        self.assertIn(SPELL_CAPABILITY, move.capability_dependencies)
        self.assertIn("zone.draw.library_to_hand", draw.capability_dependencies)
        self.assertEqual(spell_return_target_schema(), move.target_schema)
        self.assertEqual((spell_return_effect(),), tuple(move.effects))
        self.assertEqual((0, len(card.oracle_text)), (move.span.start, draw.span.end))
        report, semantics = register_card(self.db, self.registry, CARD)
        self.assertTrue(report["strict_capability_ready"], report)
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        self.assertEqual(["move", "draw"], [effect["op"] for effect in program.effects])
        for capability in (SPELL_CAPABILITY, "zone.draw.library_to_hand"):
            deps = tuple(cap for cap in program.capability_dependencies if cap != capability)
            absent = replace(program, capability_dependencies=deps,
                             capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            result = bind_semantic_program_runtime(absent, capability_registry=self.registry, profile=PROFILE)
            self.assertIn("capability:undeclared_runtime_dependency:" + capability, result["blockers"])
        for old, new in (
            ("target spell", "target activated ability"), ("its owner's hand", "your hand"),
            ("target spell", "up to two target spells"),
        ):
            self.assertNotEqual("exact", compile_card(replace(card, oracle_text=card.oracle_text.replace(old, new))).status)
        broken = replace(program, target_schema={"zones": ["battlefield"], "categories": ["permanent"], "count": 1})
        self.assertFalse(bind_semantic_program_runtime(broken, capability_registry=self.registry, profile=PROFILE)["strict"])
        with patch("quorune.compiler.fixed_owner_zone_move_templates.spell_return_target_schema",
                   return_value={"zones": ["stack"], "categories": ["ability"], "count": 1}):
            self.assertNotEqual("exact", compile_card(card).status)
