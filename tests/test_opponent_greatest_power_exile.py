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
from quorune.compiler import resolution_effect_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.opponent_greatest_power_exile import (
    GREATEST_POWER_EXILE_CAPABILITIES, GREATEST_POWER_EXILE_FIELD,
    greatest_power_exile_effect, greatest_power_exile_target_schema, validate_greatest_power_exile,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Fall of the Impostor"
BASTION = "Karn's Bastion"


class OpponentGreatestPowerExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "event-spell-copy-cards.json": {BASTION},
            "opponent-greatest-power-exile-cards.json": {ORIGINAL},
            "power-toughness-switch-cards.json": {"Wall of Denial"},
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
        path = folder / "opponent-maximum-exile.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent greatest-power exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        chapters = {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)
                    if program.event.startswith("saga.chapter.")}
        self.assertEqual({"saga.chapter.1", "saga.chapter.2", "saga.chapter.3"}, set(chapters))
        return card, chapters

    def test_complete_fall_retains_both_counter_chapters_and_one_opponent_only_target(self):
        card, chapters = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        first, second, final = [chapters[f"saga.chapter.{n}"] for n in (1, 2, 3)]
        self.assertEqual(thaw_value(first.effects), thaw_value(second.effects))
        self.assertEqual("place_counters_on_targets", first.effects[0]["op"])
        self.assertEqual(greatest_power_exile_target_schema(), thaw_value(final.target_schema))
        self.assertEqual([greatest_power_exile_effect()], thaw_value(final.effects))
        self.assertLessEqual(set(GREATEST_POWER_EXILE_CAPABILITIES), set(final.capability_dependencies))

    def test_exile_descriptor_and_original_reject_wrong_slots_and_creature_targeting(self):
        card, chapters = self._original()
        final = chapters["saga.chapter.3"]
        effect = greatest_power_exile_effect()
        for changes in ({"schema_version": True}, {"opponent": "$target.1"}, {"opponent": "$controller"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_greatest_power_exile(
                    {**effect, GREATEST_POWER_EXILE_FIELD: {**effect[GREATEST_POWER_EXILE_FIELD], **changes}},
                )
        for fields in ({"player_relation": "any"}, {"count": True}, {"count": 2}):
            mutant = replace(final, target_schema={**greatest_power_exile_target_schema(), **fields})
            result = bind_semantic_program_runtime(mutant, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(result["strict"], result)
        for before, after in (("Exile a creature", "Exile target creature"),
                              ("the greatest power", "the least power"),
                              ("target opponent controls", "each opponent controls")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertTrue(ir.material_residuals, ir)

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _proliferate_saga(self, game, bastion, source):
        self.priority(game, "A")
        card = game.state.cards[bastion.object_id]
        ability = next(ability for ability in game.engine._activated_abilities(card) if not ability.mana_ability)
        action_id = f"activate:{card.ref}:{ability.ability_id}"
        legal = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {action["id"] for action in legal})
        activated = game.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        result = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (result.kind, result.actors))
        selected = game.act("pilot:A", {"action_id": "choose", "objects": [source.ref]})
        self.assertTrue(selected.ok, selected.summary)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)

    def _choose_chapter_opponent(self, game, source, seat="B"):
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        schema = pending.payload_by_actor["A"]["target_schema"]
        self.assertEqual({"B", "C", "D"}, set(schema["legal_refs"]))
        self._reject(game, "A", {"action_id": "choose", "targets": ["A"]})
        self.target(game, "A", [seat])
        self.assertEqual([seat], game.state.stack[-1].targets)
        self.assertEqual(3, game.state.cards[source.object_id].counters["lore"])
        self.assertEqual("battlefield", self._zone(game, source))

    def test_actual_fall_preserves_counter_chapters_and_exiles_a_shrouded_current_tie(self):
        game = self.session(77143001)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        first_bastion = self.add(game.engine, BASTION)
        second_bastion = self.add(game.engine, BASTION, ref="fall-second-proliferate-land")
        countered = self.add(game.engine, "Walking Corpse")
        shrouded = self.add(game.engine, "Wall of Denial", seat="B")
        shrouded.counters["+1/+1"] = 5
        raised = self.add(game.engine, "Walking Corpse", seat="B", ref="fall-rising-b")
        lower = self.add(game.engine, "Walking Corpse", seat="B", ref="fall-lower-b")
        outside = self.add(game.engine, "Ancient Brontodon", seat="C")
        growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
        secret = self.add(game.engine, "Sol Ring", seat="D", zone="hand", ref="fall-private-d")
        self.add(game.engine, "Forest", seat="B")
        for card in (source, first_bastion, second_bastion, countered, shrouded, raised, lower, outside, growth):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 9, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.target(game, "A", [countered.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[countered.object_id].counters["+1/+1"])
        self._proliferate_saga(game, first_bastion, source)
        self.target(game, "A", [countered.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2, game.state.cards[countered.object_id].counters["+1/+1"])
        self._proliferate_saga(game, second_bastion, source)
        self._choose_chapter_opponent(game, source)
        self.cast(game, "B", growth, [raised.ref])
        self.resolve_top(game)
        self.assertEqual(5, game.engine._numeric_stat(raised.object_id, "power"))
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (decision.kind, decision.actors))
        ctx = game.packet("pilot:A", full=True)["decision"]["ctx"]
        offer, = ctx["legal_actions"]
        self.assertEqual(sorted((shrouded.ref, raised.ref)), offer["choice_schema"]["legal_refs"])
        self.assertIn("shroud", game.engine._combat_keywords(game.state.cards[shrouded.object_id]))
        self.assertNotIn(secret.ref, json.dumps(game.packet("pilot:A", full=True)))
        command = {"action_id": "choose", "objects": [shrouded.ref]}
        self._reject(game, "B", command)
        for target in ([], [lower.ref], [outside.ref]):
            self._reject(game, "A", {"action_id": "choose", "objects": target})
        self.assertEqual("battlefield", self._zone(game, source))
        game = self.replay(game, load=True)
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "graveyard"), (self._zone(game, shrouded), self._zone(game, source)))
        self.assertEqual({"battlefield"}, {self._zone(game, card) for card in (countered, raised, lower, outside)})
        self.replay(game, load=True)

    def _final_chapter_session(self, seed, *, opponent_card=None):
        game = self.session(seed)
        source = self.add(game.engine, ORIGINAL)
        source.counters["lore"] = 2
        bastion = self.add(game.engine, BASTION)
        outside = self.add(game.engine, "Ancient Brontodon", seat="C")
        candidate = self.add(game.engine, opponent_card, seat="B") if opponent_card is not None else None
        for card in (source, bastion, outside):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 4})
        self.checkpoint(game)
        self._proliferate_saga(game, bastion, source)
        self._choose_chapter_opponent(game, source)
        return game, source, candidate, outside

    def test_actual_fall_exiles_a_sole_maximum_and_skips_an_empty_opponent(self):
        for present in (True, False):
            with self.subTest(present=present):
                game, source, candidate, outside = self._final_chapter_session(
                    77143010 + present, opponent_card="Walking Corpse" if present else None,
                )
                self.assertIsNone(self.resolve(game))
                if candidate:
                    self.assertEqual("exile", self._zone(game, candidate))
                self.assertEqual(("graveyard", "battlefield"), (self._zone(game, source), self._zone(game, outside)))
                self.replay(game, load=True)

    def test_actual_fall_revalidates_its_departed_target_without_exiling_another_creature(self):
        game, source, _, outside = self._final_chapter_session(77143020, opponent_card="Walking Corpse")
        self.priority(game, "B")
        departed = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(departed.ok, departed.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield"), (self._zone(game, source), self._zone(game, outside)))
        self.replay(game, load=True)

    def test_omitting_maximum_exile_restores_only_the_final_chapter_residual(self):
        with patch.object(resolution_effect_templates, "_opponent_greatest_power_exile_template", return_value=None):
            card = self.db.lookup(ORIGINAL, fuzzy=False)
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual(1, len(ir.material_residuals))
            self.assertEqual(3, ir.material_residuals[0].span.line)
            result, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_verified_exile_and_maximum_withdrawals_keep_fall_partial(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declared = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in GREATEST_POWER_EXILE_CAPABILITIES[:2]:
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped opponent maximum exile authority withdrawal"])
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
