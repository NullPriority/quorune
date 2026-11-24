from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import resolution_effect_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.object_query import ObjectQueryResult
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_greatest_power_destruction import (
    GREATEST_POWER_DESTRUCTION_FIELD, greatest_power_destruction_effect,
    validate_greatest_power_destruction,
)
from quorune.semantic_choices.context import SemanticChoiceContext
from quorune.semantic_choices.model import SemanticChoiceError
from quorune.semantic_choices.opponent_greatest_power_destruction import (
    GREATEST_POWER_DESTRUCTION_SELECTIONS, prepare_greatest_power_destruction,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Highcliff Felidar"
DESTROY = "permanent.destroy.fixed_set"
MAXIMUM = "quantity_expression.public_characteristic_aggregate"


class OpponentGreatestPowerDestructionTests(InheritedNativeFixture, unittest.TestCase):
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
            "mana-value-destruction-cards.json": {"Darksteel Myr"},
            "opponent-greatest-power-destruction-cards.json": {ORIGINAL},
            "power-toughness-switch-cards.json": {"Wall of Denial"},
            "regeneration-cards.json": {"Cudgel Troll"},
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
        path = folder / "opponent-maximum-destruction.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent maximum destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = [program for program in programs if program.event == "permanent.enter.self"]
        return card, trigger

    def test_complete_felidar_keeps_vigilance_and_one_nontargeted_destruction_result(self):
        card, trigger = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertIsNone(trigger.target_schema)
        self.assertEqual([greatest_power_destruction_effect()], thaw_value(trigger.effects))
        self.assertLessEqual({DESTROY, MAXIMUM, "trigger.event.normalized_zone_change", "trigger.placement.apnap"},
                             set(trigger.capability_dependencies))

    def test_destruction_shape_rejects_foreign_actor_target_binding_and_other_printed_results(self):
        card, trigger = self._original()
        effect = greatest_power_destruction_effect()
        for changes in ({GREATEST_POWER_DESTRUCTION_FIELD: True}, {"player": "$opponent"},
                        {"then": "sacrifice"}, {"minimum": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_greatest_power_destruction({**effect, **changes})
        targeted = replace(trigger, target_schema={"zones": ["battlefield"], "categories": ["permanent"],
                                                   "types_any": ["creature"], "count": 1})
        binding = bind_semantic_program_runtime(targeted, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"], binding)
        for before, after in (("choose a creature", "choose target creature"),
                              ("the greatest power", "the least power"),
                              ("Destroy those creatures.", "Sacrifice those creatures.")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, ir)

    def test_public_maximum_choices_use_signed_power_and_reject_stale_incarnations(self):
        high = ObjectQueryResult("high", "high-ref", "High", "B", "B", "battlefield",
                                 types=("creature",), effective_power=-2, logical_object_id="high:current")
        low = replace(high, object_id="low", ref="low-ref", effective_power=-3, logical_object_id="low:current")
        phased = replace(high, object_id="phased", ref="phased-ref", effective_power=9, phased_out=True,
                         logical_object_id="phased:current")
        def objects(*, zones, controller):
            self.assertEqual(("battlefield",), zones)
            return (high, low, phased) if controller == "B" else ()
        query = SimpleNamespace(active_seats=("A", "B", "C"), objects=objects)
        context = SemanticChoiceContext("A", "stack:maximum", "A", "Maximum choice",
                                        None, None, "maximum-choice", 1, query)
        effect = greatest_power_destruction_effect(actor="A")
        prepared = prepare_greatest_power_destruction(effect, context)
        self.assertEqual(("high-ref",), prepared.request.choice.legal_refs)
        with self.assertRaises(SemanticChoiceError):
            prepare_greatest_power_destruction(
                {**effect, GREATEST_POWER_DESTRUCTION_SELECTIONS: [["B", "high-ref", "high:previous"]]}, context,
            )

    def _queue_entry(self, game, source):
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 5, "W": 2})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertEqual([], game.state.stack[-1].targets)
        self.assertIn("vigilance", game.engine._combat_keywords(game.state.cards[source.object_id]))

    def _offers(self, game):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        offer = next(action for action in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]
                     if action["id"] == "choose")
        return set(offer["choice_schema"]["legal_refs"])

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _choose(self, game, card):
        result = game.act("pilot:A", {"action_id": "choose", "objects": [card.ref]})
        self.assertTrue(result.ok, result.summary)

    def test_actual_felidar_controller_chooses_current_local_maxima_and_destroys_together(self):
        game = self.session(77139001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first = self.add(game.engine, "Wall of Denial", seat="B")
        tied = self.add(game.engine, "Wall of Denial", seat="B", ref="felidar-tied-b")
        first.counters["+1/+1"] = tied.counters["+1/+1"] = 3
        low = self.add(game.engine, "Walking Corpse", seat="B", ref="felidar-low-b")
        raised = self.add(game.engine, "Walking Corpse", seat="C")
        untouched = self.add(game.engine, "Walking Corpse", seat="C", ref="felidar-other-c")
        growth = self.add(game.engine, "Giant Growth", seat="C", zone="hand")
        own = self.add(game.engine, "Ancient Brontodon")
        hidden = self.add(game.engine, "Sol Ring", seat="D", zone="hand", ref="felidar-private-d")
        self.add(game.engine, "Forest", seat="C")
        for card in (first, tied, low, raised, untouched, growth, own):
            self.assert_closed(game, card)
        self._queue_entry(game, source)
        self.cast(game, "C", growth, [raised.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(raised.object_id, "power"))
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self.assertEqual({first.ref, tied.ref, raised.ref}, self._offers(game))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self._reject(game, "C", {"action_id": "choose", "objects": [raised.ref]})
        self._reject(game, "A", {"action_id": "choose", "objects": [low.ref]})
        self._reject(game, "A", {"action_id": "choose", "objects": [raised.ref, tied.ref]})
        self._choose(game, raised)
        self.assertEqual({first.ref, tied.ref}, self._offers(game))
        self.assertEqual(("battlefield", "battlefield"), (self._zone(game, raised), self._zone(game, tied)))
        self._reject(game, "A", {"action_id": "choose", "objects": [raised.ref]})
        game = self.replay(game, load=True)
        self.assertIn("shroud", game.engine._combat_keywords(game.state.cards[tied.object_id]))
        self._choose(game, tied)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard"), (self._zone(game, raised), self._zone(game, tied)))
        self.assertEqual({"battlefield"}, {self._zone(game, card) for card in (own, first, low, untouched, source)})
        self.replay(game, load=True)

    def test_actual_felidar_preserves_regeneration_replacement_choice_and_indestructible(self):
        game = self.session(77139011)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        regenerating = self.add(game.engine, "Cudgel Troll", seat="B")
        indestructible = self.add(game.engine, "Darksteel Myr", seat="C")
        unprotected = self.add(game.engine, "Walking Corpse", seat="D")
        regenerating.counters["shield"] = 1
        self.add(game.engine, "Forest", seat="B")
        for card in (regenerating, indestructible, unprotected):
            self.assert_closed(game, card)
        self._queue_entry(game, source)
        self.priority(game, "B")
        action = next(option for option in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if option["id"].startswith(f"activate:{regenerating.ref}:"))
        activated = game.act("pilot:B", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.resolve_top(game)
        self.assertEqual(1, game.state.cards[regenerating.object_id].regeneration_shields)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        for card in (regenerating, indestructible, unprotected):
            self.assertIn(card.ref, self._offers(game))
            self._choose(game, card)
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual({"battlefield"}, {self._zone(game, card) for card in (regenerating, indestructible, unprotected)})
        options = pending.payload_by_actor["B"]["options"]
        self.assertEqual(2, len(options))
        regeneration = next(option["id"] for option in options
                            if "regenerat" in (option["id"] + option["label"]).casefold())
        command = {"action_id": "choose", "replacement": regeneration}
        self._reject(game, "A", command)
        game = self.replay(game, load=True)
        chosen = game.act("pilot:B", command)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[regenerating.object_id]
        self.assertEqual(("battlefield", True, 0, 1),
                         (live.zone, live.tapped, live.regeneration_shields, live.counters["shield"]))
        self.assertEqual("battlefield", self._zone(game, indestructible))
        self.assertEqual("graveyard", self._zone(game, unprotected))
        self.replay(game, load=True)

    def test_actual_felidar_with_no_opposing_creatures_finishes_without_a_choice(self):
        game = self.session(77139021)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        artifact = self.add(game.engine, "Sol Ring", seat="B")
        own = self.add(game.engine, "Ancient Brontodon")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self._queue_entry(game, source)
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("hand", "battlefield", "battlefield"),
                         tuple(self._zone(game, card) for card in (source, own, artifact)))
        self.replay(game, load=True)

    def test_omitting_the_greatest_power_destruction_lowering_restores_the_original_residual(self):
        with patch.object(resolution_effect_templates, "_opponent_greatest_power_destruction_template", return_value=None):
            result, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_verified_destruction_and_maximum_withdrawal_denies_the_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declared = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (DESTROY, MAXIMUM):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped opponent maximum destruction owner withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declared, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                original, _ = register_card(self.db, registry, ORIGINAL)
                self.assertFalse(original["strict_capability_ready"], original)
                control, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(control["strict_capability_ready"], control["blockers"])


if __name__ == "__main__":
    unittest.main()
