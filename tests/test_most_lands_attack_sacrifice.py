from copy import deepcopy
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
from quorune.compiler.affected_player_sacrifice_templates import (
    FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY,
    MOST_LANDS_ATTACK_SACRIFICE,
)
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.zone_trigger_events import ZoneTransitionKind
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
HELLION = "Tectonic Hellion"
ATTACK = "trigger.event.normalized_self_attack"
SACRIFICE = FIXED_AFFECTED_PLAYER_SACRIFICE_CAPABILITY
FIXTURES = ROOT / "tests" / "fixtures"


class MostLandsAttackSacrificeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "bound-effect-program-cards.json": "Generic Bound Plains",
            "source-attack-alone-cards.json": "Isamaru, Hound of Konda",
        }
        parts = [FIXTURES / "most-lands-attack-sacrifice-cards.json"]
        for filename, name in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            card, = (row for row in data["cards"] if row["name"] == name)
            data["cards"] = [card]
            data["rulings"] = [
                row for row in data.get("rulings", ()) if row["oracle_id"] == card["oracle_id"]
            ]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            parts.append(piece)
        database = folder / "most-lands-attack-sacrifice.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Most lands at resolution", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_program(self):
        record = self.db.lookup(HELLION, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, HELLION)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        program, = (program for program in semantics.programs_for_oracle(record.oracle_id)
                    if program.event == "creature.attacks")
        return record, program

    def test_complete_original_keeps_haste_and_exact_current_land_choice(self):
        card, program = self.original_program()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(["keyword_ability", "triggered_ability"], [node.kind for node in ir.faces[0].nodes])
        self.assertIn("Haste", ir.faces[0].nodes[0].text)
        self.assertEqual(("creature.attacks", "battlefield"), (program.event, program.active_zone))
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, program.event_condition)
        self.assertIsNone(program.target_schema)
        self.assertTrue({ATTACK, SACRIFICE, "trigger.placement.apnap", "zone.change.destination_replacement"}
                        <= set(program.capability_dependencies))
        self.assertEqual(list(MOST_LANDS_ATTACK_SACRIFICE.effects), thaw_value(program.effects))

    def test_changed_source_scope_count_and_selector_fail_closed(self):
        card, program = self.original_program()
        effect, = thaw_value(program.effects)
        players = effect["players"]
        mutations = (
            {"event": "permanent.enter.self"},
            {"active_zone": "graveyard"},
            {"event_condition": None, "coverage": ()},
            {"effects": [{**effect, "players": "all"}]},
            {"effects": [{**effect, "players": {**players, "schema_version": True}}], "coverage": ()},
            {"effects": [{**effect, "players": {**players, "metric": "creatures"}}]},
            {"effects": [{**effect, "count": 2.0}]},
            {"effects": [{**effect, "count": 1}]},
            {"effects": [{**effect, "predicate": {**effect["predicate"], "include_phased_out": 0}}]},
            {"effects": [{**effect, "then": "exile"}]},
            {"target_schema": {"zones": ["player"], "categories": ["player"], "count": 1}},
        )
        for changes in mutations:
            with self.subTest(changes=changes):
                binding = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(binding["strict"], binding)
        for old, new in (
            ("attacks,", "attacks alone,"),
            ("each player", "each opponent"),
            ("the most lands", "the fewest lands"),
            ("two lands", "one land"),
        ):
            with self.subTest(text=new):
                changed = replace(card, oracle_text=card.oracle_text.replace(old, new))
                ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def _attack(self, game, source, defender="B"):
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, defender)])
        trigger, = game.state.stack
        self.assertEqual((source.object_id, [], "creature.attacks"),
                         (trigger.source_object_id, trigger.targets, trigger.context["event"]))

    def _choice(self, game, seat, lands, count, *, prior=None):
        self.assertEqual(("choice.apnap", [seat]),
                         (game.state.pending_decision.kind, game.state.pending_decision.actors))
        ctx = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual(count, ctx["count"])
        self.assertEqual({card.ref for card in lands}, set(ctx["options"]))
        self.assertEqual(prior or {}, ctx["prior_public_choices"])
        for other in set("ABCD") - {seat}:
            self.assertIsNone(game.packet("pilot:" + other, full=True).get("decision"))

    def _choose_lands(self, game, seat, lands):
        return game.act("pilot:" + seat, {"action_id": "choose", "cards": [card.ref for card in lands]})

    def test_actual_response_creates_two_leaders_and_all_four_lands_move_together(self):
        game = self.session(76156650)
        source = self.add(game.engine, HELLION)
        source.acquired_control_turn_count = -1
        response = self.add(game.engine, "Boomerang", zone="hand")
        payer = [self.add(game.engine, "Island", ref=f"hellion-response-mana-{n}") for n in range(2)]
        b_lands = [self.add(game.engine, "Forest", seat="B", ref=f"hellion-b-land-{n}") for n in range(4)]
        c_lands = [self.add(game.engine, "Mountain", seat="C", ref=f"hellion-c-land-{n}") for n in range(3)]
        borrowed = b_lands[0]
        borrowed.owner = "D"
        owned_by_b = self.add(game.engine, "Forest", seat="D", ref="land-owned-by-b-controlled-by-d")
        owned_by_b.owner = "B"
        self.add(game.engine, "Grizzly Bears", seat="B")
        private = self.add(game.engine, "Unsummon", seat="D", zone="hand", ref="hellion-private-spell")
        self.assert_closed(game, source)
        self.assert_closed(game, response)
        self._attack(game, source)
        self.cast(game, "A", response, [b_lands[-1].ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[b_lands[-1].object_id].zone)
        self.assertIsNotNone(self.resolve(game))
        self._choice(game, "B", b_lands[:3], 2)
        game = self.replay(game, load=True)

        first = b_lands[:2]
        second = c_lands[:2]
        selected = first + second
        before = authoritative_state_hash(game.state)
        for actor, attempted in (("A", first), ("B", [borrowed]), ("B", [borrowed, owned_by_b])):
            with self.subTest(actor=actor, refs=[card.ref for card in attempted]):
                self.assertFalse(self._choose_lands(game, actor, attempted).ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
        result = self._choose_lands(game, "B", first)
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in selected))
        self._choice(game, "C", c_lands, 2, prior={"B": [card.ref for card in first]})

        before = authoritative_state_hash(game.state)
        move_objects = game.engine.move_objects_simultaneously_intent

        def reject_after_movement(intent):
            move_objects(intent)
            raise GameRuleError("Reject the whole choice after the zone mutation")

        with patch.object(game.engine, "move_objects_simultaneously_intent", side_effect=reject_after_movement):
            self.assertFalse(self._choose_lands(game, "C", second).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self._choice(game, "C", c_lands, 2, prior={"B": [card.ref for card in first]})

        with patch.object(game.engine, "move_objects_simultaneously_intent", wraps=move_objects) as committed:
            result = self._choose_lands(game, "C", second)
            self.assertTrue(result.ok, result.summary)
        move, = [call.args[0] for call in committed.call_args_list
                 if call.args[0].transition_kind is ZoneTransitionKind.SACRIFICE]
        self.assertEqual(tuple(card.ref for card in selected), move.object_refs)
        self.assertEqual((("battlefield",), "graveyard"), (move.expected_zones, move.destination))
        self.assertIn(borrowed.object_id, game.state.players["D"].zones["graveyard"])
        self.assertIn(first[1].object_id, game.state.players["B"].zones["graveyard"])
        self.assertTrue(all(card.object_id in game.state.players["C"].zones["graveyard"] for card in second))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield"
                            for card in (*payer, b_lands[2], c_lands[2], owned_by_b)))
        self.assertFalse(game.state.stack)
        for viewer in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + viewer, full=True)))
        self.replay(game, load=True)

    def test_actual_all_seats_tie_at_one_with_haste_and_source_departure(self):
        game = self.session(76156651)
        source = self.add(game.engine, HELLION, zone="hand")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        lands = {seat: self.add(game.engine, "Island", seat=seat, ref=f"one-land-{seat}") for seat in "ABCD"}
        self.assert_closed(game, source)
        self.assert_closed(game, response)
        self.prepare(game, mana={"C": 7, "R": 3})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(source, "B")])
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", response, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)

        self.assertIsNotNone(self.resolve(game))
        prior = {}
        for seat, land in lands.items():
            self._choice(game, seat, [land], 1, prior=prior)
            self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in lands.values()))
            result = self._choose_lands(game, seat, [land])
            self.assertTrue(result.ok, result.summary)
            prior[seat] = [land.ref]
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in lands.values()))
        self.assertFalse(game.state.stack)
        self.replay(game, load=True)

    def test_actual_empty_sets_and_departed_land_leader_use_current_active_seats(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(76156652 + departed)
                source = self.add(game.engine, HELLION)
                source.acquired_control_turn_count = -1
                if departed:
                    self.add(game.engine, "Island", seat="B", ref="departing-first-land")
                    self.add(game.engine, "Island", seat="B", ref="departing-second-land")
                    remaining = self.add(game.engine, "Forest", seat="D", ref="last-active-land")
                self._attack(game, source, defender="C")
                if departed:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                    self.assertNotIn("B", game.engine.active_seats)
                    self.assertIsNotNone(self.resolve(game))
                    self._choice(game, "D", [remaining], 1)
                    result = self._choose_lands(game, "D", [remaining])
                    self.assertTrue(result.ok, result.summary)
                    self.assertEqual("graveyard", game.state.cards[remaining.object_id].zone)
                else:
                    self.assertIsNone(self.resolve(game))
                self.assertFalse(game.state.stack)
                self.assertFalse(game.state.game_over)
                self.replay(game, load=True)

    def test_removing_exact_most_lands_lowering_restores_the_original_residual(self):
        with patch("quorune.compiler.affected_player_sacrifice_templates.most_lands_attack_sacrifice_result", return_value=None):
            bound, _ = register_card(self.db, self.registry, HELLION)
            other, _ = register_card(self.db, self.registry, "Boomerang")
        self.assertFalse(bound["strict_capability_ready"], bound)
        self.assertTrue(other["strict_capability_ready"], other["blockers"])

    def test_verified_withdrawals_require_attack_and_public_sacrifice_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (ATTACK, SACRIFICE):
            with self.subTest(capability=capability):
                changed = deepcopy(raw)
                next(row for row in changed["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Current lands choice authority withdrawn"])
                registry = CapabilityRegistry(changed)
                index = build_index(registry_value=changed, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                self.assertIn("status:" + capability + ":blocked", registry.closure((capability,), profile=PROFILE).blockers)
                bound, _ = register_card(self.db, registry, HELLION)
                other, _ = register_card(self.db, registry, "Boomerang")
                self.assertFalse(bound["strict_capability_ready"], bound)
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
