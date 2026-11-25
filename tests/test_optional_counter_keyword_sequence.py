"""Native evidence for the fixed counter and keyword sequence over one optional target."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from optional_result_followup_fixture import OptionalResultFollowupFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import target_set_result_sequences
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.target_characteristic_sets import exact_optional_creature_vehicle_keyword_grant
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
REX = "Agonasaur Rex"
VEHICLE = "Renegade Freighter"
CREATURE = "Walking Corpse"
COUNTERS = "counter.producer.quantified_target_set"
CHARACTERISTICS = "continuous.resolution.fixed_target_characteristic_set"
SEQUENCE = "resolution.target_set.ordered_results"
TARGET = "target.revalidate_resolution"
CYCLING = "activation.cycling.hand"
KEYWORDS = {"trample", "indestructible"}


class OptionalCounterKeywordSequenceTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "additional-continuous-carriers-cards.json": {CREATURE},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-prohibition-cards.json": {"Solemnity"},
            "mixed-permanent-target-cards.json": {REX},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "vehicle-self-attack-cards.json": {VEHICLE},
        }
        subsets = []
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, name
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / name
            piece.write_text(json.dumps(doc), encoding="utf-8")
            subsets.append(piece)
        path = folder / "optional-counter-keyword.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Optional counter keywords", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(REX, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, REX)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = tuple(semantics.programs_for_oracle(card.oracle_id))
        trigger, = [program for program in programs if program.event == "card.cycled.self"]
        return card, trigger, programs

    def test_complete_agonasaur_keeps_the_ordered_optional_union_and_independent_cycling(self):
        card, trigger, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual("hand", trigger.active_zone)
        targets = thaw_value(trigger.target_schema)
        self.assertEqual(1, targets["up_to"])
        self.assertEqual([
            {"types_all": ["creature"], "subtypes_any": [], "supertypes_any": []},
            {"types_all": [], "subtypes_any": ["vehicle"], "supertypes_any": []},
        ], targets["characteristic_forms_any"])
        counter, keywords = thaw_value(trigger.effects)
        self.assertEqual(("place_counters_on_targets", "+1/+1", 2, "$targets"),
                         (counter["op"], counter["counter"], counter["amount"], counter["cards"]))
        self.assertTrue(exact_optional_creature_vehicle_keyword_grant(keywords, targets))
        self.assertLessEqual({SEQUENCE, COUNTERS, CHARACTERISTICS, TARGET, "counter.producer.fixed_event_trigger"},
                             set(trigger.capability_dependencies))
        cycle, = [p for p in programs if CYCLING in p.capability_dependencies]
        self.assertEqual([{"op": "draw", "player": "$controller", "count": 1, "private": True}],
                         thaw_value(cycle.effects))
        self.assertTrue(any(p.event == "continuous" and "combat.damage.assignment.trample" in p.capability_dependencies
                            for p in programs))

    def test_optional_sequence_rejects_wider_targets_and_changed_results(self):
        _, trigger, _ = self._original()
        targets = thaw_value(trigger.target_schema)
        counter, keywords = thaw_value(trigger.effects)
        for alternate in (
            replace(trigger, target_schema={**targets, "up_to": 2}),
            replace(trigger, effects=({**counter, "amount": 3}, keywords)),
            replace(trigger, effects=(counter, {**keywords, "keywords": ["Flying"]})),
            replace(trigger, effects=({**counter, "amount": 2.0}, keywords)),
        ):
            report = bind_semantic_program_runtime(alternate, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report["strict"], report)
        exact = "Put two +1/+1 counters on up to one target creature or Vehicle. It gains trample and indestructible until end of turn."
        variant = target_set_result_sequences._optional_counter_keyword_sequence
        self.assertIsNotNone(variant(exact))
        self.assertIsNone(variant(exact.replace("until end of turn", "until your next turn")))
        self.assertIsNone(variant(exact + " Draw a card."))

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _assert_private_draw(self, game, before):
        added, = self._hand(game) - before
        ref = game.state.cards[added].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet(f"pilot:{seat}", full=True)))

    def _cycle(self, game, rex):
        self.priority(game, "A")
        offer, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"].startswith(f"activate:{rex.ref}:")]
        command = {"action_id": offer["id"], "pay": "manual", "payment": {"C": 2, "G": 1}}
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[rex.object_id].zone)

    def _choose(self, game, chosen, expected, rejected=()):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        target_offer = pending.payload_by_actor["A"]["target_schema"]
        self.assertEqual(set(expected), set(target_offer["legal_refs"]))
        for seat, targets in rejected:
            digest = authoritative_state_hash(game.state)
            wrong = game.act(f"pilot:{seat}", {"action_id": "choose", "targets": targets})
            self.assertFalse(wrong.ok)
            self.assertEqual(digest, authoritative_state_hash(game.state))
        result = game.act("pilot:A", {"action_id": "choose", "targets": list(chosen)})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(("activated_ability", "triggered_ability"),
                         tuple(item.kind for item in game.state.stack[-2:]))

    def _keywords(self, game, card):
        return set(game.engine._combat_keywords(game.state.cards[card.object_id]))

    def test_actual_agonasaur_sets_both_results_on_an_uncrewed_vehicle_before_its_draw(self):
        game = self.session(77159010)
        rex = self.add(game.engine, REX, zone="hand")
        vehicle = self.add(game.engine, VEHICLE)
        pilot = self.add(game.engine, CREATURE)
        invalid = self.add(game.engine, "Sol Ring", seat="B")
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        before = self._hand(game)
        self.assertNotIn("Creature", game.engine._effective_card_data(vehicle)["type_line"])
        self.assertFalse(KEYWORDS & self._keywords(game, vehicle))
        self._cycle(game, rex)
        game = self.replay(game, load=True)
        self._choose(game, [vehicle.ref], [vehicle.ref, pilot.ref],
                     [("A", [invalid.ref]), ("A", [vehicle.ref, pilot.ref]), ("B", [vehicle.ref])])
        self.resolve_top(game)
        live = game.state.cards[vehicle.object_id]
        self.assertEqual(2, live.counters["+1/+1"])
        self.assertNotIn("Creature", game.engine._effective_card_data(live)["type_line"])
        self.assertLessEqual(KEYWORDS, self._keywords(game, live))
        self.assertFalse(KEYWORDS & self._keywords(game, pilot))
        self.assertEqual(set(), self._hand(game) - before)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)

        self.priority(game, "A")
        action, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith(f"activate:{vehicle.ref}:")]
        result = game.act("pilot:A", {"action_id": action["id"], "cost_cards": [pilot.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(6, game.engine._numeric_stat(vehicle.object_id, "power"))
        self.assertLessEqual(KEYWORDS, self._keywords(game, vehicle))
        self.finish_turn(game)
        self.assertFalse(KEYWORDS & self._keywords(game, vehicle))
        self.assertEqual(2, game.state.cards[vehicle.object_id].counters["+1/+1"])
        self.replay(game, load=True)

    def test_actual_agonasaur_grants_both_keywords_when_solemnity_prevents_counters(self):
        game = self.session(77159020)
        rex = self.add(game.engine, REX, zone="hand")
        subject = self.add(game.engine, CREATURE)
        self.add(game.engine, "Solemnity", seat="B")
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        before = self._hand(game)
        self._cycle(game, rex)
        self._choose(game, [subject.ref], [subject.ref])
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[subject.object_id].counters.get("+1/+1", 0))
        self.assertLessEqual(KEYWORDS, self._keywords(game, subject))
        self.assertEqual(set(), self._hand(game) - before)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_agonasaur_allows_selecting_zero_and_still_draws(self):
        game = self.session(77159030)
        rex = self.add(game.engine, REX, zone="hand")
        subject = self.add(game.engine, CREATURE)
        self.prepare(game, mana={"C": 2, "G": 1})
        self.checkpoint(game)
        before = self._hand(game)
        self._cycle(game, rex)
        self._choose(game, [], [subject.ref])
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[subject.object_id].counters.get("+1/+1", 0))
        self.assertFalse(KEYWORDS & self._keywords(game, subject))
        self.assertEqual(set(), self._hand(game) - before)
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before)
        self.replay(game, load=True)

    def test_actual_agonasaur_loses_its_only_selected_target_without_losing_the_draw(self):
        game = self.session(77159040)
        rex = self.add(game.engine, REX, zone="hand")
        subject = self.add(game.engine, CREATURE)
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.prepare(game, mana={"C": 2, "G": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        before = self._hand(game)
        self._cycle(game, rex)
        self._choose(game, [subject.ref], [subject.ref])
        self.cast(game, "B", bounce, [subject.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[subject.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(0, game.state.cards[subject.object_id].counters.get("+1/+1", 0))
        self.assertEqual(set(), self._hand(game) - (before | {subject.object_id}))
        self.assertIsNone(self.resolve(game))
        self._assert_private_draw(game, before | {subject.object_id})
        self.replay(game, load=True)

    def test_omitting_the_exact_optional_sequence_leaves_the_original_material_residual(self):
        with patch.object(target_set_result_sequences, "_optional_counter_keyword_sequence", return_value=None):
            report, _ = register_card(self.db, self.registry, REX)
            self.assertFalse(report["strict_capability_ready"], report)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_ordered_counter_and_characteristic_withdrawals_deny_the_complete_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (SEQUENCE, COUNTERS, CHARACTERISTICS):
            with self.subTest(capability=cap):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Scoped optional counter and keyword authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                report, _ = register_card(self.db, registry, REX)
                self.assertFalse(report["strict_capability_ready"], report)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
