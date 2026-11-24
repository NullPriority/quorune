"""Flagbearer eligibility uses the canonical complete-target assignment owner."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from quorune.selection.target_validation import (
    target_assignment_can_include,
    target_assignment_witnesses,
    target_choice_requirement,
)
from quorune.targets import target_plan
from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.attachments import attach_objects
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantic_runtime.target_requirements import (
    FLAGBEARER_TARGET_CAPABILITY, FLAGBEARER_TARGET_EVENT,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PERMANENT = {"categories": ["permanent"], "zones": ["battlefield"]}


class TargetSnapshots:
    def _target_snapshot(self, ref):
        return {"controller": ref.split("-", 1)[0]}


class FlagbearerTargetFeasibilityTests(unittest.TestCase):
    def test_requires_a_complete_same_controller_tuple(self):
        plan = target_plan({
            "groups": [
                {**PERMANENT, "id": "first", "count": 1},
                {**PERMANENT, "id": "second", "count": 1},
            ],
            "same_player_groups": [["first", "second"]],
            "globally_distinct": True,
        })
        host = TargetSnapshots()
        counts = {"first": 1, "second": 1}
        flags = frozenset({"C-flagbearer"})
        candidates = {"first": ("C-flagbearer", "B-creature"), "second": ("B-partner",)}
        self.assertFalse(target_assignment_can_include(host, plan, candidates, counts, flags))
        candidates["second"] = ("B-partner", "C-partner")
        self.assertTrue(target_assignment_can_include(host, plan, candidates, counts, flags))

    def test_zero_targets_and_the_chosen_count_are_independent(self):
        plan = target_plan({**PERMANENT, "id": "choice", "min": 0, "max": 3})
        host = TargetSnapshots()
        flags = frozenset({"B-flagbearer"})
        candidates = {"choice": ("B-flagbearer", "C-creature")}
        self.assertFalse(target_assignment_can_include(host, plan, candidates, {"choice": 0}, flags))
        self.assertTrue(target_assignment_can_include(host, plan, candidates, {"choice": 2}, flags))
        self.assertFalse(target_assignment_can_include(host, plan, candidates, {"choice": 3}, flags))

    def test_requirement_projects_large_independent_counts_as_one_range(self):
        plan = target_plan({**PERMANENT, "id": "choice", "min": 0, "max": 200})
        refs = ("B-flagbearer", *(f"C-{index}" for index in range(150)))
        candidates = {"choice": refs}
        flags = frozenset({"B-flagbearer"})
        with patch("quorune.selection.target_validation.target_assignment_can_include") as witnesses:
            scope = target_choice_requirement(TargetSnapshots(), plan, candidates, flags)
            witnesses.assert_not_called()
        self.assertEqual({"kind": "flagbearer", "legal_refs": ["B-flagbearer"],
                          "when_target_counts": [{"choice": [1, 151]}]}, scope)

    def test_combined_relations_use_only_the_counts_that_can_include_a_required_target(self):
        plan = target_plan({"groups": [
            {**PERMANENT, "id": "first", "min": 0, "max": 2},
            {**PERMANENT, "id": "second", "min": 0, "max": 1},
        ], "same_player_groups": [["first", "second"]]})
        candidates = {"first": ("C-flagbearer", "B-first", "B-second"),
                      "second": ("C-partner", "B-partner")}
        scope = target_choice_requirement(TargetSnapshots(), plan, candidates, frozenset({"C-flagbearer"}))
        self.assertEqual({"kind": "flagbearer", "legal_refs": ["C-flagbearer"],
                          "when_target_counts": [{"first": [1, 1], "second": [1, 1]}]}, scope)

    def test_first_witness_streams_group_combinations(self):
        from itertools import combinations

        plan = target_plan({**PERMANENT, "id": "choice", "count": 6})
        refs = tuple(f"B-{index}" for index in range(20))
        used = []

        def counted(values, count):
            for selection in combinations(values, count):
                used.append(selection)
                yield selection

        with patch("itertools.combinations", counted):
            result = next(target_assignment_witnesses(TargetSnapshots(), plan, {"choice": refs}))
        self.assertEqual((refs[:6], {"choice": list(refs[:6])}), result)
        self.assertEqual([refs[:6]], used)

    def test_counted_prices_keep_flagbearers_separate_from_equal_price_targets(self):
        from quorune.rules.casting.distinct_target_costs import distinct_target_tax_assignments
        from quorune.rules.casting.target_count_costs import counted_target_tax_assignments

        class Host(TargetSnapshots):
            def _effective_card_data(self, card):
                return {"type_line": "Instant", "colors": ["G"]}

            def _target_candidate_map(self, *args, **kwargs):
                return {"choice": ("B-normal", "B-second", "B-flagbearer")}

        class Tax:
            def __init__(self, version):
                self.spec = SimpleNamespace(schema_version=version)
                self.component_id = "equal-price"
                self.eligible_target_refs = ("B-normal", "B-second", "B-flagbearer")

            def matches_target(self, ref):
                return ref in self.eligible_target_refs

        host = Host()
        spell = SimpleNamespace(controller="A", owner="A", ref="card")
        schema = {**PERMANENT, "id": "choice", "min": 0, "max": 2}
        flags = frozenset({"B-flagbearer"})
        for witness_owner, tax in ((counted_target_tax_assignments, Tax(4)),
                                   (distinct_target_tax_assignments, Tax(5))):
            with self.subTest(version=tax.spec.schema_version):
                rows = list(witness_owner(host, "A", spell, schema, (tax,), required_refs=flags))
                self.assertEqual({0, 1, 2}, {count for _, _, count in rows})
                self.assertTrue(all(not count or flags.intersection(refs) for refs, _, count in rows))
                self.assertTrue(any(set(refs) == {"B-normal", "B-flagbearer"} for refs, _, _ in rows))


class FlagbearerNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "flagbearer.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/flagbearer-target-requirements-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Flagbearer testing", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Plains", 20),
        ], ["Isamaru, Hound of Konda"])

    def offer(self, game, seat, card, *, activation=False):
        self.priority(game, seat)
        actions = game.packet(f"pilot:{seat}", full=True)["decision"]["ctx"]["legal"]["actions"]
        matching = f"activate:{card.ref}:" if activation else f"cast:{card.ref}"
        chosen, = [
            action for action in actions
            if (action["id"].startswith(matching) if activation else action["id"] == matching)
        ]
        return chosen

    def target_refs(self, game, seat, card, *, activation=False):
        return set(self.offer(game, seat, card, activation=activation)["target_schema"]["legal_refs"])

    def reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act(f"pilot:{seat}", command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_all_three_originals_bind_a_closed_static_target_requirement(self):
        for name in ("Standard Bearer", "Coalition Honor Guard", "Coalition Flag"):
            with self.subTest(name=name):
                record = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = [node for node in ir.faces[0].nodes if node.event == FLAGBEARER_TARGET_EVENT]
                self.assertIsNone(node.target_schema)
                self.assertIn(FLAGBEARER_TARGET_CAPABILITY, node.capability_dependencies)
                verdict, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(verdict["strict_capability_ready"], verdict["blockers"])
                program, = semantics.programs_for_oracle(record.oracle_id, event=FLAGBEARER_TARGET_EVENT)
                with self.assertRaisesRegex(ValueError, "Runtime handler event must match"):
                    replace(program, event="step.begin")
                changed = replace(program, active_zone="hand")
                rebind = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(any(reason.startswith("runtime_flagbearer_requirement:invalid:")
                                    for reason in rebind["blockers"]), rebind)
                altered = replace(record, oracle_text=record.oracle_text.replace("Flagbearer", "Elf").replace(
                    "if able", "even if unable"))
                alternative = compile_oracle_card(altered, capability_registry=self.registry, capability_profile="commander_review")
                self.assertFalse(any(child.event == FLAGBEARER_TARGET_EVENT
                                     for face in alternative.faces for child in face.nodes))

    def test_standard_bearer_restricts_cast_offers_and_locks_the_accepted_target(self):
        game = self.session(6012201)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        other = self.add(game.engine, "Grizzly Bears")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        private = self.add(game.engine, "Disenchant", seat="D", zone="hand")
        self.assert_closed(game, flag)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.assertEqual({flag.ref}, self.target_refs(game, "A", growth), "A legal Flagbearer narrows the offered targets")
        rejected = {"action_id": f"cast:{growth.ref}", "targets": [other.ref], "pay": "auto"}
        self.reject(game, "A", rejected)
        self.cast_for(game, "A", growth, [flag.ref])
        self.assertEqual([flag.ref], game.state.stack[-1].targets)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(4, game.engine._numeric_stat(flag.object_id, "power"))
        self.assertEqual(2, game.engine._numeric_stat(other.object_id, "power"))
        for actor in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(private.ref, json.dumps(game.packet(actor, full=True)))
        self.replay(game, load=True)

    def test_omitting_the_live_requirement_fails_the_native_cast_offer_witness(self):
        with patch("quorune.semantic_runtime.target_requirements.current_flagbearer_target_refs", return_value=frozenset()):
            with self.assertRaisesRegex(AssertionError, "A legal Flagbearer narrows the offered targets"):
                self.test_standard_bearer_restricts_cast_offers_and_locks_the_accepted_target()

    def test_coalition_honor_guard_requires_an_eligible_flagbearer_for_an_activation(self):
        game = self.session(6012202)
        guard = self.add(game.engine, "Coalition Honor Guard", seat="B")
        other_flag = self.add(game.engine, "Universal Automaton", seat="C")
        pinger = self.add(game.engine, "Prodigal Sorcerer")
        body = self.add(game.engine, "Grizzly Bears")
        pinger.acquired_control_turn_count = -1
        for card in (guard, other_flag, pinger):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        offer = self.offer(game, "A", pinger, activation=True)
        self.assertEqual({guard.ref, other_flag.ref}, set(offer["target_schema"]["legal_refs"]))
        for illegal in (body.ref, "B"):
            self.reject(game, "A", {"action_id": offer["id"], "targets": [illegal], "pay": "auto"})
        placed = game.act("pilot:A", {"action_id": offer["id"], "targets": [other_flag.ref], "pay": "auto"})
        self.assertTrue(placed.ok, placed.summary)
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[other_flag.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[guard.object_id].zone)
        self.replay(game, load=True)

    def test_coalition_flag_uses_its_attached_creature_and_stops_when_the_aura_leaves(self):
        game = self.session(6012203)
        aura = self.add(game.engine, "Coalition Flag", zone="hand")
        bearer = self.add(game.engine, "Grizzly Bears", ref="aura-bearer")
        other = self.add(game.engine, "Grizzly Bears", seat="B", ref="opponent-bear")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        reply = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        self.add(game.engine, "Plains", seat="B", ref="answer-plains-one")
        self.add(game.engine, "Plains", seat="B", ref="answer-plains-two")
        self.assert_closed(game, aura)
        self.prepare(game, mana={"W": 1})
        self.checkpoint(game)
        self.cast_for(game, "A", aura, [bearer.ref])
        self.resolve_top(game)
        self.assertEqual(bearer.object_id, game.state.cards[aura.object_id].attached_to)
        self.assertEqual({bearer.ref}, self.target_refs(game, "B", growth))
        self.cast_for(game, "B", reply, [aura.ref])
        self.resolve_top(game)
        self.assertEqual({bearer.ref, other.ref}, self.target_refs(game, "B", growth))
        self.cast_for(game, "B", growth, [other.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(other.object_id, "power"))
        self.replay(game, load=True)

    def test_an_opponent_has_a_normal_target_when_the_flagbearer_has_hexproof(self):
        game = self.session(6012204)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        body = self.add(game.engine, "Grizzly Bears")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        guile = self.add(game.engine, "Ranger's Guile", seat="B", zone="hand")
        self.add(game.engine, "Forest", seat="B")
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.assertEqual({flag.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "B", guile, [flag.ref])
        self.resolve_top(game)
        self.assertEqual({body.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "A", growth, [body.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(body.object_id, "power"))
        self.replay(game, load=True)

    def test_a_spell_can_choose_zero_optional_targets(self):
        game = self.session(6012205)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        body = self.add(game.engine, "Grizzly Bears")
        spell = self.add(game.engine, "Plunge into Winter", zone="hand")
        self.assert_closed(game, spell)
        self.prepare(game, mana={"W": 1, "C": 1})
        self.checkpoint(game)
        offer = self.offer(game, "A", spell)
        self.assertEqual({flag.ref}, set(offer["target_schema"]["legal_refs"]))
        self.reject(game, "A", {"action_id": offer["id"], "targets": [body.ref], "pay": "auto"})
        result = game.act("pilot:A", {"action_id": offer["id"], "targets": [], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([], game.state.stack[-1].targets)
        self.replay(game, load=True)

    def test_losing_the_static_ability_removes_the_requirement(self):
        game = self.session(6012206)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        body = self.add(game.engine, "Grizzly Bears")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        blank = self.add(game.engine, "Dress Down", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B", ref="ability-loss-island")
        self.add(game.engine, "Island", seat="B", ref="ability-loss-generic")
        self.assert_closed(game, blank)
        self.prepare(game, mana={"G": 1})
        self.checkpoint(game)
        self.assertEqual({flag.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "B", blank)
        self.resolve_top(game)
        self.assertIsNone(self.resolve(game))
        self.assertIn("flagbearer", game.engine._effective_type_parts(flag)[1])
        self.assertEqual({body.ref, flag.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "A", growth, [body.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(body.object_id, "power"))
        self.replay(game, load=True)

    def test_a_triggered_ability_can_choose_another_creature(self):
        game = self.session(6012207)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        body = self.add(game.engine, "Grizzly Bears", seat="B")
        jelly = self.add(game.engine, "Man-o'-War", zone="hand")
        self.assert_closed(game, jelly)
        self.prepare(game, mana={"U": 1, "C": 2})
        self.checkpoint(game)
        self.cast_for(game, "A", jelly)
        pending = self.resolve(game)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, list(pending.actors)))
        schema = game.packet("pilot:A", full=True)["decision"]["ctx"]["target_schema"]
        self.assertLessEqual({flag.ref, body.ref}, set(schema["legal_refs"]))
        accepted = game.act("pilot:A", {"action_id": "choose", "targets": [body.ref]})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[body.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[flag.object_id].zone)
        self.replay(game, load=True)

    def test_target_pricing_cannot_offer_the_cheaper_forbidden_creature(self):
        for payable in (False, True):
            with self.subTest(payable=payable):
                game = self.session(6012208 + payable)
                creature = self.add(game.engine, "Charix, the Raging Isle", seat="B")
                aura = self.add(game.engine, "Coalition Flag", seat="B")
                body = self.add(game.engine, "Grizzly Bears")
                growth = self.add(game.engine, "Giant Growth", zone="hand")
                for card in (creature, aura):
                    self.assert_closed(game, card)
                attach_objects(game.state.cards, aura, creature, source_timestamp=aura.zone_timestamp,
                               players=game.state.players)
                self.prepare(game, mana={"G": 1, "C": 2} if payable else {"G": 1})
                self.checkpoint(game)
                request = {"action_id": f"cast:{growth.ref}", "targets": [body.ref], "pay": "auto"}
                self.priority(game, "A")
                actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                offered = [action for action in actions if action["id"] == request["action_id"]]
                self.assertEqual(payable, bool(offered))
                self.reject(game, "A", request)
                if payable:
                    self.assertEqual({creature.ref}, self.target_refs(game, "A", growth))
                    self.cast_for(game, "A", growth, [creature.ref])
                    self.resolve_top(game)
                    self.assertEqual(3, game.engine._numeric_stat(creature.object_id, "power"))
                self.replay(game, load=True)

    def test_a_complete_multi_group_cast_needs_a_flagbearer_in_one_role(self):
        game = self.session(6012210)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        body = self.add(game.engine, "Grizzly Bears")
        bond = self.add(game.engine, "Common Bond", zone="hand")
        self.assert_closed(game, bond)
        self.prepare(game, mana={"G": 1, "W": 1, "C": 1})
        self.checkpoint(game)
        offer = self.offer(game, "A", bond)
        schema = offer["target_schema"]
        self.assertEqual(2, len(schema["groups"]))
        self.assertEqual({"kind": "flagbearer", "legal_refs": [flag.ref]}, schema["target_choice_requirement"])
        self.reject(game, "A", {"action_id": offer["id"], "targets": [body.ref, body.ref], "pay": "auto"})
        self.cast_for(game, "A", bond, [body.ref, flag.ref])
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[body.object_id].counters["+1/+1"])
        self.assertEqual(1, game.state.cards[flag.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_optional_multi_target_spell_only_requires_a_bearer_for_positive_legal_counts(self):
        for choose_targets in (False, True):
            with self.subTest(choose_targets=choose_targets):
                game = self.session(6012212 + choose_targets)
                flag = self.add(game.engine, "Standard Bearer", seat="B")
                body = self.add(game.engine, "Grizzly Bears", ref="normal-target-first")
                other = self.add(game.engine, "Grizzly Bears", seat="C", ref="normal-target-second")
                breath = self.add(game.engine, "Frost Breath", zone="hand")
                self.assert_closed(game, breath)
                self.prepare(game, mana={"U": 1, "C": 2})
                self.checkpoint(game)
                offer = self.offer(game, "A", breath)
                schema = offer["target_schema"]
                group, = schema["groups"]
                self.assertEqual({"kind": "flagbearer", "legal_refs": [flag.ref],
                                  "when_target_counts": [{group["id"]: [1, 2]}]},
                                 schema["target_choice_requirement"])
                for forbidden in ([body.ref], [body.ref, other.ref]):
                    self.reject(game, "A", {"action_id": offer["id"], "targets": forbidden, "pay": "auto"})
                chosen = [body.ref, flag.ref] if choose_targets else []
                self.cast_for(game, "A", breath, chosen)
                self.resolve_top(game)
                self.assertEqual(choose_targets, game.state.cards[flag.object_id].tapped)
                self.assertEqual(choose_targets, game.state.cards[body.object_id].tapped)
                self.assertFalse(game.state.cards[other.object_id].tapped)
                self.replay(game, load=True)

    def test_actual_two_mode_spell_uses_one_combined_flagbearer_requirement(self):
        for damage in (False, True):
            with self.subTest(damage=damage):
                game = self.session(6012214 + damage)
                flag = self.add(game.engine, "Standard Bearer", seat="B")
                body = self.add(game.engine, "Grizzly Bears", seat="C")
                artifact = self.add(game.engine, "Leather Armor", seat="C")
                spell = self.add(game.engine, "Prismari Command", zone="hand")
                self.assert_closed(game, spell)
                self.prepare(game, mana={"R": 1, "U": 1, "C": 1})
                self.checkpoint(game)
                offer = self.offer(game, "A", spell)
                rules = {tuple(row["modes"]): row["legal_refs"]
                         for row in offer["target_schema"]["combined_target_choice_requirements"]}
                self.assertEqual({
                    ("mode_1", "mode_2"): [flag.ref],
                    ("mode_1", "mode_3"): [flag.ref],
                    ("mode_1", "mode_4"): [flag.ref],
                }, rules)
                self.reject(game, "A", {"action_id": offer["id"], "modes": ["mode_1", "mode_3"],
                                         "targets": [body.ref, "A"], "pay": "auto"})
                modes, targets = (
                    (["mode_1", "mode_3"], [flag.ref, "A"]) if damage
                    else (["mode_3", "mode_4"], ["A", artifact.ref])
                )
                result = game.act("pilot:A", {"action_id": offer["id"], "modes": modes,
                                               "targets": targets, "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                self.resolve_top(game)
                self.assertEqual("graveyard" if damage else "battlefield", game.state.cards[flag.object_id].zone)
                self.assertEqual("battlefield" if damage else "graveyard", game.state.cards[artifact.object_id].zone)
                self.assertTrue(any(card.zone == "battlefield" and card.controller == "A"
                                    and "Treasure" in card.printed_name for card in game.state.cards.values()))
                self.replay(game, load=True)

    def test_reverberate_can_retarget_the_original_flagbearer_spell_to_another_creature(self):
        game = self.session(6012216)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        other = self.add(game.engine, "Grizzly Bears")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        copy = self.add(game.engine, "Reverberate", zone="hand")
        self.assert_closed(game, copy)
        self.prepare(game, mana={"G": 1, "R": 2})
        self.checkpoint(game)
        self.assertEqual({flag.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "A", growth, [flag.ref])
        original = game.state.stack[-1]
        self.cast_for(game, "A", copy, [original.ref])
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [other.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(4, game.engine._numeric_stat(flag.object_id, "power"))
        self.assertEqual(5, game.engine._numeric_stat(other.object_id, "power"))
        self.replay(game, load=True)

    def test_ward_on_a_flagbearer_follows_legal_targeting_and_keeps_its_normal_payment(self):
        game = self.session(6012217)
        flag = self.add(game.engine, "Standard Bearer", seat="B")
        armor = self.add(game.engine, "Leather Armor", seat="B")
        self.add(game.engine, "Grizzly Bears")
        growth = self.add(game.engine, "Giant Growth", zone="hand")
        self.assert_closed(game, armor)
        attach_objects(game.state.cards, armor, flag, source_timestamp=armor.zone_timestamp,
                       players=game.state.players)
        self.prepare(game, mana={"G": 1, "C": 1})
        self.checkpoint(game)
        self.assertEqual({flag.ref}, self.target_refs(game, "A", growth))
        self.cast_for(game, "A", growth, [flag.ref])
        ward, = [item for item in game.state.stack if item.semantic_key == "builtin:ward"]
        self.assertEqual(flag.object_id, ward.source_object_id)
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        option = choice.payload_by_actor["A"]
        self.assertEqual("ward_counter_unless_pay", option["operation"])
        self.assertEqual({"GENERIC": 1}, option["cost"])
        self.assertTrue(option["payable"])
        result = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[growth.object_id].zone)
        self.assertEqual(1, game.engine._numeric_stat(flag.object_id, "power"))
        self.replay(game, load=True)
