from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT, keep_all
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import public_trigger_conditions, resolution_condition_templates
from quorune.compiler.public_state_fact_queries import greatest_creature_power_control_condition
from quorune.continuous_conditions import FixedPublicStateConditionError, FixedPublicStateConditionSpec, FixedPublicStateFact
from quorune.counter_placement_targets import controlled_creature_counter_choice_effect
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.public_characteristic_aggregates import current_creature_power_control
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
POWER = "condition.characteristics.public_power_aggregate"
AGGREGATE = "quantity_expression.public_characteristic_aggregate"
COUNTER = "counter.producer.fixed_effect"
ANY = FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE
ALL = FixedPublicStateFact.CONTROLS_EACH_GREATEST_POWER_CREATURE
EOMER = "\u00c9omer of the Riddermark"
ORIGINALS = {"High Score": ("step.begin", ANY, False),
             "Primal Empathy": ("step.begin", ANY, False),
             EOMER: ("creature.attacks", ANY, True),
             "Might Makes Right": ("step.begin", ALL, True)}


class GreatestCreaturePowerConditionTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest", "Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "combat-defending-player-cards.json": {"Snakeskin Veil"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "power-toughness-switch-cards.json": {"Wall of Denial"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "greatest-creature-power-condition-cards.json": set(ORIGINALS),
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, (filename, names)
            selected = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in selected]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        path = folder / "greatest-power-conditions.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Highest creature power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_trigger(self, name):
        record = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        event = ORIGINALS[name][0]
        trigger, = [p for p in semantics.programs_for_oracle(record.oracle_id) if p.event == event]
        return record, trigger, semantics.programs_for_oracle(record.oracle_id)

    def _condition_facts(self, value):
        if isinstance(value, dict):
            fact = [value["fact"]] if value.get("fact") in (ANY.value, ALL.value) else []
            return fact + [result for child in value.values() for result in self._condition_facts(child)]
        if isinstance(value, list):
            return [result for child in value for result in self._condition_facts(child)]
        return []

    def test_all_greatest_power_originals_bind_exact_gate_timing_and_keep_their_siblings(self):
        for name, (event, fact, intervening) in ORIGINALS.items():
            with self.subTest(name=name):
                record, trigger, programs = self.original_trigger(name)
                ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(event, trigger.event)
                self.assertLessEqual({POWER, AGGREGATE}, set(trigger.capability_dependencies))
                bounds = self._condition_facts(thaw_value(trigger.event_condition))
                results = self._condition_facts(thaw_value(trigger.effects))
                self.assertEqual([fact] if intervening else [], bounds)
                self.assertEqual([] if intervening else [fact], results)
                if name == "High Score":
                    siblings = [p for p in programs if p.event == "counter.place"]
                    self.assertEqual(1, len(siblings))
                    self.assertIn("counter.placement.quantity_replacement", siblings[0].capability_dependencies)
                if name == "Primal Empathy":
                    effect, = thaw_value(trigger.effects)
                    self.assertEqual([controlled_creature_counter_choice_effect()], effect["otherwise_effects"])
                    self.assertIn(COUNTER, trigger.capability_dependencies)
                if name == "Might Makes Right":
                    self.assertIsNotNone(trigger.target_schema)
                    self.assertEqual(3, len(trigger.effects))

    def test_maximum_control_compares_signed_ties_and_fails_closed_for_unavailable_power(self):
        first = ObjectQueryResult("A1", "a1", "First", "A", "A", "battlefield",
                                  types=("creature",), effective_power=-2)
        tied = ObjectQueryResult("B1", "b1", "Second", "B", "B", "battlefield",
                                 types=("creature",), effective_power=-2)
        lower = replace(tied, effective_power=-3)
        phased = replace(tied, effective_power=9, phased_out=True)
        hidden = replace(tied, effective_power=99, zone="hand")
        self.assertTrue(current_creature_power_control((first, tied), "A", require_all=False))
        self.assertFalse(current_creature_power_control((first, tied), "A", require_all=True))
        self.assertTrue(current_creature_power_control((first, lower, phased, hidden), "A", require_all=True))
        self.assertFalse(current_creature_power_control((), "A", require_all=False))
        self.assertFalse(current_creature_power_control((lower,), "A", require_all=True))
        self.assertIsNone(current_creature_power_control((first, replace(tied, effective_power=None)),
                                                          "A", require_all=False))

    def test_greatest_power_shapes_reject_unsupported_comparisons_and_targeted_counter_fallbacks(self):
        literal = "you control a creature with the greatest power among creatures on the battlefield"
        condition = greatest_creature_power_control_condition(literal)
        payload = condition.to_dict()
        self.assertEqual(condition, FixedPublicStateConditionSpec.from_dict(payload))
        for change in ({"schema_version": 3}, {"amount": 2}, {"amount": True}, {"fact_parameter": "source"}):
            with self.subTest(change=change), self.assertRaises(FixedPublicStateConditionError):
                FixedPublicStateConditionSpec.from_dict({**payload, **change})
        self.assertIsNone(greatest_creature_power_control_condition(literal.replace("power", "toughness")))
        self.assertIsNone(greatest_creature_power_control_condition(literal.replace("a creature", "another creature")))
        _, trigger, _ = self.original_trigger("Primal Empathy")
        effect, = thaw_value(trigger.effects)
        alternate, = effect["otherwise_effects"]
        changed = {**alternate, "selector": {**alternate["selector"], "controller_relation": "opponent"}}
        malformed = replace(trigger, effects=[{**effect, "otherwise_effects": [changed]}])
        result = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)

    def _queued(self, game, source):
        return [item for item in game.state.stack
                if item.kind == "triggered_ability" and item.source_object_id == source.object_id]

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _private_draw(self, game, before):
        drawn, = self._hand(game) - before
        private = game.state.cards[drawn].ref
        self.assertIn(private, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(private, json.dumps(game.packet("pilot:" + seat, full=True)))

    def _reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_high_score_checks_power_at_resolution_and_keeps_the_counter_replacement(self):
        game = self.session(77133001)
        score = self.add(game.engine, "High Score")
        own = self.add(game.engine, "Walking Corpse")
        opponent = self.add(game.engine, "Ancient Brontodon", seat="B")
        veil = self.add(game.engine, "Snakeskin Veil", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        self.add(game.engine, "Forest")
        self.add(game.engine, "Island")
        for card in (score, own, opponent, veil, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.cast(game, "A", veil, [own.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[own.object_id].counters.get("+1/+1"))
        self.until(game, lambda s: s.state.active_player == "A" and s.state.step == "end_step")
        self.assertEqual(1, len(self._queued(game, score)))
        self.cast(game, "A", bounce, [opponent.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[opponent.object_id].zone)
        before = self._hand(game)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_eomer_triggers_on_a_tie_and_rechecks_current_power_after_a_response(self):
        for grow in (False, True):
            with self.subTest(grow=grow):
                game = self.session(77133002 + grow)
                rider = self.add(game.engine, EOMER)
                rival = self.add(game.engine, EOMER, seat="B", ref="rival-rider")
                boost = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                for card in (rider, rival, boost):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.attack(game, [(rider, "C")])
                self.assertEqual(1, len(self._queued(game, rider)))
                if grow:
                    self.cast(game, "B", boost, [rival.ref])
                    self.resolve_top(game)
                    self.assertGreater(game.engine._numeric_stat(rival.object_id, "power"),
                                       game.engine._numeric_stat(rider.object_id, "power"))
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                tokens = [card for card in game.state.cards.values()
                          if card.zone == "battlefield" and card.is_token and card.controller == "A"]
                self.assertEqual(0 if grow else 1, len(tokens))
                if tokens:
                    data = game.engine._effective_card_data(tokens[0])
                    self.assertEqual(["W"], data["colors"])
                    self.assertEqual((1, 1), tuple(game.engine._numeric_stat(tokens[0].object_id, stat)
                                                    for stat in ("power", "toughness")))
                self.replay(game, load=True)

    def test_actual_might_makes_right_requires_all_maxima_at_trigger_and_resolution(self):
        for mode in ("initial_tie", "later_tie", "uncontested"):
            with self.subTest(mode=mode):
                game = self.session(77133005 + len(mode))
                enchantment = self.add(game.engine, "Might Makes Right")
                leader = self.add(game.engine, EOMER)
                body = self.add(game.engine, EOMER if mode == "initial_tie" else "Walking Corpse",
                                seat="B", ref="opposing-maximum")
                body.tapped = True
                buff = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                for card in (enchantment, leader, body, buff):
                    self.assert_closed(game, card)
                self.prepare(game)
                self.checkpoint(game)
                self.until(game, lambda s: s.state.step == "beginning_combat")
                if mode == "initial_tie":
                    self.assertFalse(self._queued(game, enchantment))
                else:
                    self.target(game, "A", [body.ref])
                    self.assertEqual(1, len(self._queued(game, enchantment)))
                    if mode == "later_tie":
                        self.cast(game, "B", buff, [body.ref])
                        self.resolve_top(game)
                        self.assertEqual(game.engine._numeric_stat(body.object_id, "power"),
                                         game.engine._numeric_stat(leader.object_id, "power"))
                    game = self.replay(game, load=True)
                    self.assertIsNone(self.resolve(game))
                live = game.state.cards[body.object_id]
                self.assertEqual("A" if mode == "uncontested" else "B", live.controller)
                self.assertEqual(mode != "uncontested", live.tapped)
                if mode == "uncontested":
                    self.assertIn("haste", game.engine._combat_keywords(live))
                self.replay(game, load=True)

    def test_actual_primal_empathy_uses_private_draw_or_one_untargeted_controlled_choice(self):
        for branch in ("tie", "counter", "empty"):
            with self.subTest(branch=branch):
                seed = 77133020 + len(branch)
                game = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in "ABCD"},
                    first_player="D", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
                keep_all(game)
                empathy = self.add(game.engine, "Primal Empathy")
                self.assert_closed(game, empathy)
                if branch != "empty":
                    controlled = self.add(game.engine, "Wall of Denial" if branch == "counter" else "Walking Corpse")
                    opponent = self.add(game.engine, "Walking Corpse", seat="B", ref="greatest-power-opponent")
                    self.assert_closed(game, controlled)
                    self.assert_closed(game, opponent)
                self.prepare(game, active="D", actor="D")
                self.checkpoint(game)
                self.finish_turn(game)
                self.until(game, lambda s: s.state.active_player == "A" and s.state.step == "upkeep")
                self.assertEqual(1, len(self._queued(game, empathy)))
                before = self._hand(game)
                decision = self.resolve(game)
                if branch == "tie":
                    self.assertIsNone(decision)
                    self._private_draw(game, before)
                elif branch == "empty":
                    self.assertIsNone(decision)
                    self.assertEqual(before, self._hand(game))
                else:
                    self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
                    self.assertIn("shroud", game.engine._combat_keywords(game.state.cards[controlled.object_id]))
                    invalid = {"action_id": "choose", "objects": [opponent.ref]}
                    self._reject(game, "A", invalid)
                    self._reject(game, "A", {"action_id": "choose", "objects": []})
                    legit = {"action_id": "choose", "objects": [controlled.ref]}
                    self._reject(game, "C", legit)
                    game = self.replay(game, load=True)
                    accepted = game.act("pilot:A", legit)
                    self.assertTrue(accepted.ok, accepted.summary)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(1, game.state.cards[controlled.object_id].counters.get("+1/+1"))
                    self.assertEqual(before, self._hand(game))
                self.replay(game, load=True)

    def test_omitting_current_greatest_power_lowering_restores_each_complete_original_residual(self):
        with patch.object(public_trigger_conditions, "greatest_creature_power_control_condition", return_value=None), patch.object(
            resolution_condition_templates, "greatest_creature_power_control_condition", return_value=None,
        ):
            for name in ORIGINALS:
                result, _ = register_card(self.db, self.registry, name)
                self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_greatest_power_and_untargeted_counter_withdrawals_keep_independent_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declared = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (POWER, COUNTER):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Greatest creature power or chosen counter owner withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declared, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(capability == COUNTER and name != "Primal Empathy",
                                     result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])
