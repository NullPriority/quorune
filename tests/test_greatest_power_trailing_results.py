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
from quorune.compiler import resolution_condition_templates
from quorune.compiler.affected_player_discard_templates import AffectedPlayerDiscardSubject, FixedAffectedPlayerDiscardTemplate
from quorune.continuous_conditions import FixedPublicStateFact
from quorune.deck import DeckDefinition, DeckEntry
from quorune.model import GameConfig
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.resolution_conditions import (
    GREATEST_POWER_TARGET_DISCARD_VERSION,
    validate_resolution_condition_instruction,
)
from quorune.rules.capabilities import load_default_capability_registry
from quorune.session import CommanderSession
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
CRUELTY = "Triumph of Cruelty"
FEROCITY = "Triumph of Ferocity"
THICKET = "Thickest in the Thicket"
ORIGINALS = {CRUELTY: None, FEROCITY: 1, THICKET: 2}
POWER = "condition.characteristics.public_power_aggregate"
AGGREGATE = "quantity_expression.public_characteristic_aggregate"
CONDITION = "resolution.effect.public_condition"
DISCARD = "choice.affected_player.fixed_discard"


class GreatestPowerTrailingResultTests(InheritedNativeFixture, unittest.TestCase):
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
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "greatest-power-trailing-result-cards.json": set(ORIGINALS),
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for filename, names in support.items():
            document = json.loads((FIXTURES / filename).read_bytes())
            document["cards"] = [row for row in document["cards"] if row["name"] in names]
            assert {row["name"] for row in document["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in document["cards"]}
            document["rulings"] = [row for row in document.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(document), encoding="utf-8")
            subsets.append(piece)
        path = folder / "trailing-power-results.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Trailing power results", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = [program for program in programs if program.event == "step.begin"]
        return card, trigger, programs

    def test_complete_originals_keep_scheduled_triggers_and_distinct_exact_results(self):
        for name, draw_count in ORIGINALS.items():
            with self.subTest(name=name):
                card, trigger, programs = self._original(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertLessEqual({POWER, AGGREGATE, CONDITION}, set(trigger.capability_dependencies))
                self.assertNotIn(FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE.value,
                                 json.dumps(thaw_value(trigger.event_condition)))
                effect, = thaw_value(trigger.effects)
                condition, children = validate_resolution_condition_instruction(effect)
                self.assertIs(FixedPublicStateFact.CONTROLS_A_GREATEST_POWER_CREATURE, condition.fact)
                if draw_count is None:
                    self.assertEqual(GREATEST_POWER_TARGET_DISCARD_VERSION, effect["schema_version"])
                    discard = FixedAffectedPlayerDiscardTemplate(AffectedPlayerDiscardSubject.TARGET_OPPONENT, 1)
                    self.assertEqual(discard.target_schema, thaw_value(trigger.target_schema))
                    self.assertEqual(discard.effects, children)
                    self.assertLessEqual({DISCARD, "target.revalidate_resolution"}, set(trigger.capability_dependencies))
                else:
                    self.assertIsNone(trigger.target_schema)
                    self.assertEqual(({"op": "draw", "count": draw_count, "private": True, "player": "$controller"},),
                                     children)
                if name == THICKET:
                    entry, = [program for program in programs if program.event == "permanent.enter.self"]
                    self.assertIsNotNone(entry.target_schema)
                    self.assertNotIn(CONDITION, entry.capability_dependencies)

    def test_targeted_trailing_shape_rejects_wrong_slot_actor_quantity_and_unrelated_clauses(self):
        card, trigger, _ = self._original(CRUELTY)
        effect, = thaw_value(trigger.effects)
        template, = effect["effects"]
        for changes in ({"target": "$target.1", "players": ["$target.1"]}, {"actor": "$target.0"},
                        {"count": True}, {"count": 2}, {"hidden": False}, {"players": ["$controller"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_resolution_condition_instruction({**effect, "effects": [{**template, **changes}]})
        for region in ("any", "self"):
            changed = replace(trigger, target_schema={**thaw_value(trigger.target_schema), "player_relation": region})
            admission = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(admission["strict"], admission)
        for before, after in (
            ("target opponent", "target player"),
            ("discards a card", "discards two cards"),
            ("greatest power", "greatest toughness"),
        ):
            candidate = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(candidate, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, ir)

    def _upkeep_session(self, seed, name):
        game = CommanderSession.create(self.db, {seat: deepcopy(self.deck) for seat in "ABCD"},
            first_player="D", seed=seed, config=GameConfig(seed=seed, auto_pass_empty_priority=False))
        keep_all(game)
        source = self.add(game.engine, name)
        self.assert_closed(game, source)
        return game, source

    def _start_next_upkeep(self, game):
        self.prepare(game, active="D", actor="D")
        self.checkpoint(game)
        self.finish_turn(game)
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "upkeep")

    def _hand(self, game, seat):
        return set(game.state.players[seat].zones["hand"])

    def _assert_draw(self, game, before, expected):
        drawn = self._hand(game, "A") - before
        self.assertEqual(expected, len(drawn))
        self.assertLessEqual(before, self._hand(game, "A"))
        for object_id in drawn:
            ref = game.state.cards[object_id].ref
            self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
            for seat in "BCD":
                self.assertNotIn(ref, json.dumps(game.packet("pilot:" + seat, full=True)))

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_actual_ferocity_triggers_on_empty_and_checks_for_a_new_tie_after_response(self):
        for tie in (False, True):
            with self.subTest(tie=tie):
                game, source = self._upkeep_session(77137001 + tie, FEROCITY)
                if tie:
                    own = self.add(game.engine, "Walking Corpse")
                    opponent = self.add(game.engine, "Walking Corpse", seat="C", ref="ferocity-tie-c")
                    higher = self.add(game.engine, "Ancient Brontodon", seat="B")
                    bounce = self.add(game.engine, "Unsummon", zone="hand")
                    self.add(game.engine, "Island")
                    for card in (own, opponent, higher, bounce):
                        self.assert_closed(game, card)
                self._start_next_upkeep(game)
                self.assertEqual(1, self.trigger_count(game, source))
                if tie:
                    self.cast(game, "A", bounce, [higher.ref])
                    self.resolve_top(game)
                    self.assertEqual("hand", game.state.cards[higher.object_id].zone)
                    self.assertEqual(game.engine._numeric_stat(own.object_id, "power"),
                                     game.engine._numeric_stat(opponent.object_id, "power"))
                before = self._hand(game, "A")
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self._assert_draw(game, before, int(tie))
                self.replay(game, load=True)

    def test_actual_thicket_retains_independent_target_counters_and_two_private_draws(self):
        game = self.session(77137010)
        source = self.add(game.engine, THICKET, zone="hand")
        own = self.add(game.engine, "Walking Corpse")
        self.add(game.engine, "Ancient Brontodon", seat="B")
        boost = self.add(game.engine, "Giant Growth", zone="hand")
        self.add(game.engine, "Forest")
        for card in (source, own, boost):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "G": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve(game)
        self.target(game, "A", [own.ref])
        self.cast(game, "A", boost, [own.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(own.object_id, "power"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(5, game.state.cards[own.object_id].counters["+1/+1"])
        self.until(game, lambda current: current.state.active_player == "A" and current.state.step == "end_step")
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(10, game.engine._numeric_stat(own.object_id, "power"))
        before = self._hand(game, "A")
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._assert_draw(game, before, 2)
        self.replay(game, load=True)

    def _prepare_cruelty(self, seed, *, response=False):
        game, source = self._upkeep_session(seed, CRUELTY)
        own = self.add(game.engine, "Walking Corpse")
        tied = self.add(game.engine, "Walking Corpse", seat="C", ref="cruelty-tie-c")
        higher = self.add(game.engine, "Ancient Brontodon", seat="B")
        selected = self.add(game.engine, "Sol Ring", seat="B", zone="hand", ref="cruelty-private-b")
        foreign = self.add(game.engine, "Sol Ring", seat="C", zone="hand", ref="cruelty-private-c")
        bounce = None
        if response:
            bounce = self.add(game.engine, "Unsummon", zone="hand")
            self.add(game.engine, "Island")
            self.assert_closed(game, bounce)
        for card in (own, tied, higher, selected, foreign):
            self.assert_closed(game, card)
        self._start_next_upkeep(game)
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        offer, = pending.payload_by_actor["A"]["legal_actions"]
        self.assertEqual({"B", "C", "D"}, set(offer["target_schema"]["legal_refs"]))
        self._reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", ["B"])
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual(["B"], game.state.stack[-1].targets)
        return game, higher, selected, foreign, bounce

    def test_actual_cruelty_targets_before_the_check_then_only_target_chooses_privately(self):
        game, higher, selected, foreign, bounce = self._prepare_cruelty(77137020, response=True)
        self.cast(game, "A", bounce, [higher.ref])
        self.resolve_top(game)
        before = {seat: self._hand(game, seat) for seat in "ABCD"}
        decision = self.resolve(game)
        self.assertEqual(("choice.apnap", ["B"]), (decision.kind, decision.actors))
        self.assertIn(selected.ref, json.dumps(game.packet("pilot:B", full=True)["decision"]))
        for seat in "ACD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])
            self.assertNotIn(selected.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        command = {"action_id": "choose", "cards": [selected.ref]}
        self._reject(game, "B", {"action_id": "choose", "cards": [foreign.ref]})
        self._reject(game, "A", command)
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[selected.object_id].zone)
        self.assertEqual({**before, "B": before["B"] - {selected.object_id}},
                         {seat: self._hand(game, seat) for seat in "ABCD"})
        self.replay(game, load=True)

    def test_actual_cruelty_false_condition_or_departed_target_leaves_no_discard_choice(self):
        for departure in (False, True):
            with self.subTest(departure=departure):
                game, _, _, foreign, _ = self._prepare_cruelty(77137030 + departure)
                if departure:
                    self.priority(game, "B")
                    status = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(status.ok, status.summary)
                live_seats = "ACD" if departure else "ABCD"
                before = {seat: self._hand(game, seat) for seat in live_seats}
                self.assertIsNone(self.resolve(game))
                self.assertEqual(before, {seat: self._hand(game, seat) for seat in before})
                self.assertEqual("hand", game.state.cards[foreign.object_id].zone)
                self.replay(game, load=True)

    def test_omitting_trailing_power_lowering_keeps_all_originals_partial(self):
        with patch.object(resolution_condition_templates, "greatest_creature_power_control_condition", return_value=None):
            for name in ORIGINALS:
                with self.subTest(name=name):
                    result, _ = register_card(self.db, self.registry, name)
                    self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_verified_authority_withdrawal_distinguishes_shared_condition_and_private_discard(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (POWER, CONDITION, DISCARD):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped trailing-result authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(capability == DISCARD and name != CRUELTY,
                                     result["strict_capability_ready"], result)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
