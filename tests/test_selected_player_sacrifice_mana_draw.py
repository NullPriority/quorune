"""Paid selected-player life loss and APNAP sacrifice before controller mana and draw."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.any_number_target_shapes import selected_player_result_node_capabilities
from quorune.rules.capabilities import load_default_capability_registry
from quorune.target_cardinality import SELECTED_PLAYER_RESULT_CAPABILITY
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


SOURCE = "Priest of Forgotten Gods"
PROFILE = "commander_review"


class SelectedPlayerSacrificeManaDrawTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "selected-sacrifice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/selected-player-sacrifice-mana-draw-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Isamaru, Hound of Konda"
        cls.deck = DeckDefinition("Paid sacrifice", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Plains", 30),
        ], [commander])

    def setup_ability(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, SOURCE, ref="priest-source")
        source.acquired_control_turn_count = -1
        costs = [self.add(game.engine, "Grizzly Bears", ref=f"priest-payment-{i}") for i in range(2)]
        top_id = game.state.players["A"].zones["library"][-1]
        return game, source, costs, game.state.cards[top_id]

    def ready(self, game, source):
        game.state.config.semantic_policy = "trusted_only"
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 0})
        self.checkpoint(game)

    def offered(self, game, source):
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return [action for action in actions if action["id"].startswith(f"activate:{source.ref}:")]

    def command(self, game, source, costs, targets):
        action, = self.offered(game, source)
        return {"action_id": action["id"], "cost_cards": [card.ref for card in costs],
                "targets": list(targets), "pay": "auto"}

    def act(self, game, actor, command):
        result = game.act("pilot:" + actor, command)
        self.assertTrue(result.ok, result.summary)

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:" + actor, command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def zones(self, game, cards):
        return [game.state.cards[card.object_id].zone for card in cards]

    def context(self, game, seat):
        pending = self.resolve(game)
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]

    def result(self, game, top, *, happened):
        self.assertEqual(2 if happened else 0, game.state.players["A"].mana_pool["B"])
        self.assertEqual(["hand" if happened else "library"], self.zones(game, [top]))
        current = game.state.cards[top.object_id]
        for principal in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(principal, full=True))
            self.assertNotIn(current.ref, packet)
            self.assertNotIn(current.object_id, packet)

    def test_actual_paid_multiple_targets_resolve_life_then_one_apnap_sacrifice_batch(self):
        game, source, costs, top = self.setup_ability(704051701)
        bear_b = self.add(game.engine, "Grizzly Bears", seat="B", ref="priest-b-earliest")
        alternate_b = self.add(game.engine, "Grizzly Bears", seat="B", ref="priest-b-alt")
        bear_c = self.add(game.engine, "Grizzly Bears", seat="C", ref="priest-c-earliest")
        land_d = self.add(game.engine, "Plains", seat="D", ref="priest-d-land")
        self.ready(game, source)
        cost, = self.offered(game, source)[0]["cost_summary"]["choose_cost"]
        self.assertEqual(2, cost["n"])
        self.assertEqual({card.ref for card in costs}, set(cost["legal_refs"]))
        command = self.command(game, source, costs, ["D", "C", "B"])
        for refs in ([costs[0].ref], [costs[0].ref] * 2, [source.ref, costs[0].ref],
                     [bear_b.ref, costs[0].ref]):
            self.reject(game, "A", {**command, "cost_cards": refs})
        self.reject(game, "B", command)
        self.reject(game, "A", {**command, "targets": ["B", "B"]})
        with patch.object(game.engine, "_move_cards_simultaneously",
                          wraps=game.engine._move_cards_simultaneously) as movement:
            self.act(game, "A", command)
        paid = {card.object_id for card in costs}
        self.assertTrue(any({object_id for object_id, _ in call.args[0]} == paid
                            for call in movement.call_args_list))
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(["graveyard", "graveyard"], self.zones(game, costs))
        item, = game.state.stack
        self.assertEqual("activated_ability", item.kind)
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.result(game, top, happened=False)

        context = self.context(game, "B")
        self.assertEqual({bear_b.ref, alternate_b.ref}, set(context["options"]))
        self.assertEqual([40, 38, 38, 38], [game.state.players[seat].life for seat in "ABCD"])
        self.result(game, top, happened=False)
        self.act(game, "B", {"action_id": "choose", "cards": [alternate_b.ref]})
        game = self.replay(game, load=True)
        context = self.context(game, "C")
        self.assertEqual([bear_c.ref], context["options"])
        self.assertEqual([alternate_b.ref], context["prior_public_choices"]["B"])
        self.assertEqual(["battlefield"] * 4, self.zones(game, [bear_b, alternate_b, bear_c, land_d]))
        self.reject(game, "C", {"action_id": "choose", "cards": [alternate_b.ref]})
        self.act(game, "C", {"action_id": "choose", "cards": [bear_c.ref]})
        self.assertIsNone(self.resolve(game))
        self.assertEqual(["battlefield", "graveyard", "graveyard", "battlefield"],
                         self.zones(game, [bear_b, alternate_b, bear_c, land_d]))
        self.result(game, top, happened=True)
        self.replay(game, load=True)

    def test_zero_targets_use_stack_and_still_draw_after_an_actual_source_bounce(self):
        game, source, costs, top = self.setup_ability(704051702)
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        game.state.players["B"].mana_pool["U"] = 1
        source.acquired_control_turn_count = game.state.players["A"].turns_begun
        self.ready(game, source)
        self.assertEqual([], self.offered(game, source))
        source.acquired_control_turn_count = -1
        self.ready(game, source)
        abilities = game.engine._activated_abilities(source)
        self.assertEqual([False], [ability.mana_ability for ability in abilities])
        self.act(game, "A", self.command(game, source, costs, []))
        self.assertEqual(1, len(game.state.stack))
        self.result(game, top, happened=False)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual(["hand"], self.zones(game, [source]))
        self.assertEqual(1, len(game.state.stack))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.result(game, top, happened=True)
        self.replay(game, load=True)

    def test_partial_targets_preserve_own_result_and_sole_illegal_target_fizzles_every_result(self):
        for targets in (["C"], ["C", "D"]):
            with self.subTest(targets=targets):
                game, source, costs, top = self.setup_ability(704051705 + len(targets))
                survivor = self.add(game.engine, "Grizzly Bears", seat="D", ref="priest-surviving-target")
                self.ready(game, source)
                self.act(game, "A", self.command(game, source, costs, targets))
                self.priority(game, "C")
                self.act(game, "C", {"action_id": "concede", "choices": {"confirm_concede": True}})
                game = self.replay(game, load=True)
                legal_remains = "D" in targets
                if legal_remains:
                    self.assertEqual([survivor.ref], self.context(game, "D")["options"])
                    self.act(game, "D", {"action_id": "choose", "cards": [survivor.ref]})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(38 if legal_remains else 40, game.state.players["D"].life)
                self.assertEqual(["graveyard" if legal_remains else "battlefield"], self.zones(game, [survivor]))
                self.assertEqual(["graveyard", "graveyard"], self.zones(game, costs))
                self.result(game, top, happened=legal_remains)
                self.replay(game, load=True)

    def test_actual_competing_replacements_pause_entire_paid_price_and_resume_once(self):
        game, source, costs, top = self.setup_ability(704051709)
        self.add(game.engine, "Leyline of the Void", seat="B")
        self.add(game.engine, "Rest in Peace", seat="C")
        costs[1].phased_out = True
        self.ready(game, source)
        self.assertEqual([], self.offered(game, source))
        costs[1].phased_out = False
        self.ready(game, source)
        self.act(game, "A", self.command(game, source, costs, []))
        self.assertIn("replacement", game.state.pending_decision.kind)
        self.assertFalse(game.state.stack)
        self.assertFalse(game.state.cards[source.object_id].tapped)
        self.assertEqual(["battlefield", "battlefield"], self.zones(game, costs))
        game = self.replay(game, load=True)
        decisions = 0
        while "replacement" in game.state.pending_decision.kind:
            self.assertLess(decisions, 4)
            self.assertEqual(["pilot:A"], game.pending_principals())
            context = game.packet("pilot:A", full=True)["decision"]["ctx"]
            command = {"action_id": "choose", "replacement": context["options"][0]["id"]}
            self.reject(game, "B", command)
            self.act(game, "A", command)
            decisions += 1
        self.assertEqual(2, decisions)
        self.assertTrue(game.state.cards[source.object_id].tapped)
        self.assertEqual(["exile", "exile"], self.zones(game, costs))
        self.assertEqual(1, len(game.state.stack))
        self.assertIsNone(self.resolve(game))
        self.result(game, top, happened=True)
        self.replay(game, load=True)

    def test_full_original_shape_and_independent_runtime_dependencies(self):
        card = self.db.lookup("Priest of Forgotten Gods", fuzzy=False)
        expected = ("lose_life", "choose_cards_apnap", "mana", "draw")
        for profile in ("traditional", "commander_duel", PROFILE):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=profile)
            self.assertEqual("exact", ir.status, ir.material_residuals)
            node, = ir.faces[0].nodes
            self.assertEqual(card.oracle_text, card.oracle_text[node.span.start:node.span.end])
            self.assertEqual("activated_ability", node.kind)
            self.assertTrue(node.cost["tap_source"])
            choice, = node.cost["choices"]
            self.assertEqual(("sacrifice_one", 2, 1), (choice["k"], choice["n"], choice["other"]))
            self.assertEqual(expected, tuple(effect["op"] for effect in node.effects))
            self.assertIn("mana.production.fixed_resolution", node.capability_dependencies)
        report, programs = register_card(self.db, self.registry, "Priest of Forgotten Gods")
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = (row for row in programs.programs_for_oracle(card.oracle_id) if row.event == "activate")
        for cap in (SELECTED_PLAYER_RESULT_CAPABILITY, "choice.affected_player.fixed_sacrifice",
                    "mana.production.fixed_resolution"):
            deps = tuple(key for key in program.capability_dependencies if key != cap)
            altered = replace(program, capability_dependencies=deps,
                              capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
            bound = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"])
            self.assertIn(f"capability:undeclared_runtime_dependency:{cap}", bound["blockers"])
        for effects in (program.effects[:-1], tuple(reversed(program.effects)),
                        (program.effects[0], {**program.effects[1], "count": 2}, *program.effects[2:])):
            self.assertEqual((), selected_player_result_node_capabilities(
                effects=effects, target_schema=program.target_schema, mechanic_ids=program.coverage,
            ))
        for before, after in (("lose 2 life", "lose 3 life"), ("sacrifice a creature", "may sacrifice a creature"),
                              ("lose 2 life and ", ""), ("draw a card.", "draw a card. Rewrite history.")):
            altered = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(altered, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)
