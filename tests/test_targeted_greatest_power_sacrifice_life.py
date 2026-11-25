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
from quorune.compiler import affected_player_sacrifice_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.power_sacrifice_model import GREATEST_POWER_SACRIFICE_FIELD, GreatestPowerSacrificeSpec
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINAL = "Consecrate // Consume"
MAXIMUM = "quantity_expression.public_characteristic_aggregate"
SACRIFICE = "choice.affected_player.fixed_sacrifice"
LIFE = "life.change.effect"


class TargetedGreatestPowerSacrificeLifeTests(InheritedNativeFixture, unittest.TestCase):
    cast_face = OptionalResultFollowupFixture.cast_face

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Forest"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "activation-public-state-cards.json": {"Giant Growth"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "attached-destruction-set-cards.json": {"Rest in Peace"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "counter-replacement-cards.json": {"Ancient Brontodon"},
            "graveyard-exile-replacement-cards.json": {"Anafenza, the Foremost"},
            "power-toughness-switch-cards.json": {"Wall of Denial"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "targeted-greatest-power-sacrifice-cards.json": {ORIGINAL},
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
        path = folder / "targeted-greatest-power-sacrifice.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Targeted greatest power", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self):
        card = self.db.lookup(ORIGINAL, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, ORIGINAL)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        consume, = [program for program in programs if any(GREATEST_POWER_SACRIFICE_FIELD in effect
                                                          for effect in program.effects)]
        return card, consume, programs

    def test_complete_split_original_retains_both_faces_and_exact_targeted_sacrifice_authority(self):
        card, consume, programs = self._original()
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        self.assertEqual(2, len(programs))
        effect, = thaw_value(consume.effects)
        spec = GreatestPowerSacrificeSpec.from_effect(effect)
        self.assertEqual("$target.0", spec.target_player)
        self.assertEqual({"schema_version": 2, "gain_life": True}, effect[GREATEST_POWER_SACRIFICE_FIELD])
        self.assertEqual(spec.target_schema, thaw_value(consume.target_schema))
        self.assertLessEqual({MAXIMUM, SACRIFICE, LIFE, "target.revalidate_resolution"},
                             set(consume.capability_dependencies))
        consecrate, = [program for program in programs if program != consume]
        self.assertEqual("draw", consecrate.effects[-1]["op"])
        self.assertEqual(["graveyard"], thaw_value(consecrate.target_schema)["zones"])

    def test_targeted_power_sacrifice_rejects_foreign_slots_extra_choices_and_unrelated_scope(self):
        card, consume, _ = self._original()
        effect, = thaw_value(consume.effects)
        for changes in ({"target": "$target.1", "players": ["$target.1"]},
                        {"count": True}, {"count": 2}, {"players": "opponents"},
                        {GREATEST_POWER_SACRIFICE_FIELD: {"schema_version": 2, "gain_life": False}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                GreatestPowerSacrificeSpec.from_effect({**effect, **changes})
        target = thaw_value(consume.target_schema)
        for changes in ({"player_relation": "opponent"}, {"zones": ["battlefield"]}, {"count": 2}):
            mutated = replace(consume, target_schema={**target, **changes})
            report = bind_semantic_program_runtime(mutated, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(report["strict"], report)

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def _choose_prompt(self, game, seat):
        pending = game.state.pending_decision
        self.assertEqual(("choice.apnap", [seat]), (pending.kind, pending.actors))
        row = pending.payload_by_actor[seat]
        self.assertEqual(1, row["count"])
        return set(row["options"])

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _prepare_consume(self, game, card, *, target="B"):
        self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "W": 1, "B": 1})
        self.checkpoint(game)
        self.cast_face(game, card, "Consume", [target])

    def test_actual_consume_gains_last_battlefield_power_after_a_tied_affected_choice(self):
        for redirected in (False, True):
            with self.subTest(redirected=redirected):
                game = self.session(77149001 + redirected)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                first = self.add(game.engine, "Walking Corpse", seat="B")
                first.counters["+1/+1"] = 3
                selected = self.add(game.engine, "Walking Corpse", seat="B", ref="consume-live-growth")
                lower = self.add(game.engine, "Walking Corpse", seat="B", ref="consume-lower-candidate")
                foreign = self.add(game.engine, "Ancient Brontodon", seat="C")
                growth = self.add(game.engine, "Giant Growth", seat="B", zone="hand")
                self.add(game.engine, "Forest", seat="B")
                if redirected:
                    replacement = self.add(game.engine, "Rest in Peace", seat="C")
                    competing = self.add(game.engine, "Anafenza, the Foremost", seat="D")
                    self.assert_closed(game, replacement)
                    self.assert_closed(game, competing)
                for card in (first, selected, lower, foreign, growth):
                    self.assert_closed(game, card)
                self._prepare_consume(game, source)
                self.cast(game, "B", growth, [selected.ref])
                self.resolve_top(game)
                self.assertEqual(5, game.engine._numeric_stat(selected.object_id, "power"))
                self.assertEqual("choice.apnap", self.resolve(game).kind)
                self.assertEqual({first.ref, selected.ref}, self._choose_prompt(game, "B"))
                self.assertEqual(40, game.state.players["A"].life)
                command = {"action_id": "choose", "cards": [selected.ref]}
                self._reject(game, "A", command)
                for invalid in ([], [lower.ref], [foreign.ref], [first.ref, selected.ref]):
                    self._reject(game, "B", {"action_id": "choose", "cards": invalid})
                game = self.replay(game, load=True)
                accepted = game.act("pilot:B", command)
                self.assertTrue(accepted.ok, accepted.summary)
                if redirected:
                    game = self._finish_redirected_sacrifice(game, selected)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("exile" if redirected else "graveyard", self._zone(game, selected))
                self.assertEqual({"battlefield"}, {self._zone(game, card) for card in (first, lower, foreign)})
                self.assertEqual((45, 40), (game.state.players["A"].life, game.state.players["B"].life))
                self.replay(game, load=True)

    def _finish_redirected_sacrifice(self, game, selected):
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", ["B"]), (pending.kind, pending.actors))
        self.assertEqual("battlefield", self._zone(game, selected))
        self.assertEqual(40, game.state.players["A"].life)
        options = pending.payload_by_actor["B"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self._reject(game, "A", command)
        game = self.replay(game, load=True)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        return game

    def test_actual_consume_uses_a_negative_maximum_for_a_self_target_without_changing_life(self):
        game = self.session(77149011)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        negative = self.add(game.engine, "Wall of Denial")
        negative.counters["-1/-1"] = 2
        self.assert_closed(game, negative)
        self._prepare_consume(game, source, target="A")
        self.assertEqual(-2, game.engine._numeric_stat(negative.object_id, "power"))
        self.assertEqual("choice.apnap", self.resolve(game).kind)
        self.assertEqual({negative.ref}, self._choose_prompt(game, "A"))
        chosen = game.act("pilot:A", {"action_id": "choose", "cards": [negative.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", self._zone(game, negative))
        self.assertEqual(40, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_actual_consume_skips_an_empty_or_departed_affected_target(self):
        for departed in (False, True):
            with self.subTest(departed=departed):
                game = self.session(77149020 + departed)
                source = self.add(game.engine, ORIGINAL, zone="hand")
                outsider = self.add(game.engine, "Ancient Brontodon", seat="C")
                self.add(game.engine, "Walking Corpse" if departed else "Sol Ring", seat="B")
                self._prepare_consume(game, source)
                if departed:
                    self.priority(game, "B")
                    result = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
                    self.assertTrue(result.ok, result.summary)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40, game.state.players["A"].life)
                self.assertEqual("battlefield", self._zone(game, outsider))
                self.replay(game, load=True)

    def test_actual_consecrate_keeps_its_own_graveyard_target_and_private_draw(self):
        game = self.session(77149030)
        source = self.add(game.engine, ORIGINAL, zone="hand")
        target = self.add(game.engine, "Walking Corpse", seat="B", zone="graveyard")
        self.assert_closed(game, source)
        self.prepare(game, mana={"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast_face(game, source, "Consecrate", [target.ref])
        before = set(game.state.players["A"].zones["hand"])
        self.assertIsNone(self.resolve(game))
        drawn, = set(game.state.players["A"].zones["hand"]) - before
        secret = game.state.cards[drawn].ref
        self.assertEqual("exile", self._zone(game, target))
        self.assertEqual(40, game.state.players["A"].life)
        self.assertIn(secret, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(secret, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_omitting_targeted_maximum_life_lowering_keeps_the_split_original_partial(self):
        with patch.object(affected_player_sacrifice_templates, "_targeted_greatest_power_sacrifice_life_template",
                          return_value=None):
            original, _ = register_card(self.db, self.registry, ORIGINAL)
            self.assertFalse(original["strict_capability_ready"], original)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_maximum_and_sacrifice_withdrawals_deny_the_whole_split_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (MAXIMUM, SACRIFICE, LIFE):
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Scoped targeted sacrifice power authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
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
