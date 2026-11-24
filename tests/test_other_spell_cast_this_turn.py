"""Native evidence for excluding the exact referenced spell from turn history."""

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
from quorune.compiler import public_trigger_conditions, resolution_condition_templates
from quorune.deck import DeckDefinition, DeckEntry
from quorune.event_spell_copy_model import EVENT_SPELL_COPY_CONTEXT, EVENT_SPELL_COPY_CAPABILITY
from quorune.oracle_ir import compile_oracle_card
from quorune.other_spell_history_model import (
    OTHER_SPELL_CAST_MECHANIC, is_other_spell_draw, is_other_spell_self_cast_condition, validate_other_spell_draw,
)
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
SLICK = "Slick Sequence"
SAGE = "Sage of the Skies"
NAMES = (SLICK, SAGE)
HISTORY = "condition.history.fixed_public_query"
DRAW = "zone.draw.library_to_hand"
LITHOFORM = "Lithoform Engine"


class OtherSpellCastThisTurnTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring", "Counterspell", "Shock"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "fixed-qualified-stack-copy-cards.json": {LITHOFORM},
            "other-spell-cast-this-turn-cards.json": set(NAMES),
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        subsets = []
        for file, names in support.items():
            doc = json.loads((FIXTURES / file).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert names == {row["name"] for row in doc["cards"]}, file
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / file
            part.write_text(json.dumps(doc), encoding="utf-8")
            subsets.append(part)
        path = folder / "other-spell-cast-this-turn.sqlite3"
        build_fixture_database(subsets, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Other spell cast this turn", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _original(self, name, event):
        card = self.db.lookup(name, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        program, = [row for row in semantics.programs_for_oracle(card.oracle_id) if row.event == event]
        return card, program

    def test_complete_originals_keep_the_damage_and_the_self_cast_permanent_copy(self):
        slick, spell = self._original(SLICK, "resolve")
        sage, trigger = self._original(SAGE, "spell.cast")
        for card in (slick, sage):
            ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertEqual("exact", ir.status, ir.material_residuals)
        prefix, wrapper = thaw_value(spell.effects)
        self.assertEqual({"op": "damage", "source": "$source", "target": "$target.0", "amount": 2}, prefix)
        self.assertTrue(is_other_spell_draw(wrapper))
        self.assertIsNone(wrapper["cast_fact"])
        self.assertEqual(1, validate_other_spell_draw(wrapper)[0]["count"])
        self.assertLessEqual({HISTORY, DRAW, "target.revalidate_resolution"}, set(spell.capability_dependencies))
        self.assertEqual("stack", trigger.active_zone)
        self.assertTrue(is_other_spell_self_cast_condition(trigger.event_condition))
        self.assertIn(OTHER_SPELL_CAST_MECHANIC, trigger.coverage)
        effect, = trigger.effects
        self.assertEqual("copy_stack_item", effect["op"])
        self.assertIs(effect["retain_targets_only"], True)
        self.assertLessEqual({HISTORY, EVENT_SPELL_COPY_CAPABILITY}, set(trigger.capability_dependencies))

    def test_exact_other_spell_descriptors_reject_prefilled_facts_and_retargetable_triggers(self):
        _, spell = self._original(SLICK, "resolve")
        _, trigger = self._original(SAGE, "spell.cast")
        _, wrapper = thaw_value(spell.effects)
        for fields in ({"cast_fact": True}, {"schema_version": 22.0},
                       {"condition": {"kind": "spell_cast_this_turn", "schema_version": 1}}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                validate_other_spell_draw({**wrapper, **fields})
        effect, = thaw_value(trigger.effects)
        forged = replace(trigger, effects=({**effect, "retain_targets_only": False},))
        report = bind_semantic_program_runtime(forged, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(report["strict"], report)

    def _hand(self, game):
        return set(game.state.players["A"].zones["hand"])

    def _assert_draw(self, game, before, count):
        added = self._hand(game) - before
        self.assertEqual(count, len(added))
        for object_id in added:
            ref = game.state.cards[object_id].ref
            self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
            for other in "BCD":
                self.assertNotIn(ref, json.dumps(game.packet("pilot:" + other, full=True)))

    def _casts(self, game, seat="A"):
        return [event for event in game.state.turn_history.events if event.kind == "spell_cast" and event.actor == seat]

    def _bodies(self, game):
        return [card for card in game.state.cards.values() if card.zone == "battlefield"
                and card.controller == "A" and card.printed_name == SAGE]

    def test_actual_slick_counts_a_countered_prior_cast_and_keeps_ordinary_target_checks(self):
        for index, illegal in enumerate((False, True)):
            with self.subTest(illegal=illegal):
                game = self.session(77155010 + index)
                slick = self.add(game.engine, SLICK, zone="hand")
                ring = self.add(game.engine, "Sol Ring", zone="hand")
                counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
                bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                body = self.add(game.engine, "Walking Corpse", seat="B")
                self.prepare(game, mana={"C": 1, "U": 1, "R": 1})
                game.state.players["B"].mana_pool["U"] = 3
                self.checkpoint(game)

                self.priority(game, "A")
                ring_action = self.action(game, "A", ring)
                payment = {"action_id": ring_action["id"], "pay": "manual", "payment": {"C": 1}}
                outcome = game.act("pilot:A", payment)
                self.assertTrue(outcome.ok, outcome.summary)
                self.cast(game, "B", counter, [game.state.stack[-1].ref])
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[ring.object_id].zone)
                self.assertEqual(1, len(self._casts(game)))
                self.cast(game, "A", slick, [body.ref if illegal else "B"])
                before = self._hand(game)
                if illegal:
                    self.cast(game, "B", bounce, [body.ref])
                    self.resolve_top(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(40 if illegal else 38, game.state.players["B"].life)
                self.assertEqual(2, len(self._casts(game)))
                self._assert_draw(game, before, 0 if illegal else 1)
                self.replay(game, load=True)

    def test_actual_slick_copy_counts_the_pending_original_and_the_original_excludes_itself(self):
        game = self.session(77155020)
        slick = self.add(game.engine, SLICK, zone="hand")
        copier = self.add(game.engine, LITHOFORM)
        self.prepare(game, mana={"C": 3, "U": 1, "R": 1})
        self.checkpoint(game)
        self.cast(game, "A", slick, ["B"])
        original = game.state.stack[-1]
        original_id = game.state.cards[original.card_object_id].logical_object_id
        self.priority(game, "A")
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in offers if row["id"].startswith("activate:" + copier.ref + ":")
                    and original.ref in json.dumps(row.get("target_schema"))]
        command = {"action_id": offered["id"], "targets": [original.ref], "pay": "manual", "payment": {"C": 3}}
        before_wrong = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:B", command).ok)
        self.assertEqual(before_wrong, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        result = game.act("pilot:A", {"action_id": "choose", "targets": ["C"]})
        self.assertTrue(result.ok, result.summary)
        copy = game.state.stack[-1]
        self.assertEqual("spell_copy", copy.kind)
        self.assertNotEqual(original_id, game.state.cards[copy.card_object_id].logical_object_id)
        self.assertEqual([original_id], [row.object_incarnation for row in self._casts(game)])
        before = self._hand(game)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self._assert_draw(game, before, 1)
        self.assertEqual(38, game.state.players["C"].life)
        self.assertEqual(40, game.state.players["B"].life)
        after_copy = self._hand(game)
        self.assertIsNone(self.resolve(game))
        self._assert_draw(game, after_copy, 0)
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual([original_id], [row.object_incarnation for row in self._casts(game)])
        self.replay(game, load=True)

    def test_actual_sage_first_cast_does_not_trigger_for_other_seats_or_later_casts(self):
        game = self.session(77155030)
        sage = self.add(game.engine, SAGE, zone="hand")
        earlier = self.add(game.engine, "Shock", seat="B", zone="hand", ref="other-seat-shock")
        later = self.add(game.engine, "Shock", zone="hand", ref="later-sage-shock")
        self.prepare(game, mana={"W": 5, "R": 1})
        game.state.players["B"].mana_pool["R"] = 1
        self.checkpoint(game)
        self.cast(game, "B", earlier, ["D"])
        self.assertIsNone(self.resolve(game))
        self.assertEqual((0, 1), (len(self._casts(game)), len(self._casts(game, "B"))))
        self.cast(game, "A", sage)
        self.assertEqual(["spell"], [row.kind for row in game.state.stack])
        self.cast(game, "A", later, ["D"])
        self.assertEqual(["spell", "spell"], [row.kind for row in game.state.stack])
        self.assertIsNone(self.resolve(game))
        body, = self._bodies(game)
        self.assertTrue(body.is_card_object)
        self.assertLessEqual({"flying", "lifelink"}, set(game.engine._combat_keywords(body)))
        self.assertEqual(2, len(self._casts(game)))
        self.replay(game, load=True)

    def test_actual_sage_copies_its_departed_spell_after_an_earlier_cast_and_replays_its_token(self):
        game = self.session(77155031)
        sage = self.add(game.engine, SAGE, zone="hand")
        ring = self.add(game.engine, "Sol Ring", zone="hand")
        counter = self.add(game.engine, "Counterspell", seat="B", zone="hand")
        self.prepare(game, mana={"C": 1, "W": 5})
        game.state.players["B"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast(game, "A", ring)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", sage)
        original, trigger = game.state.stack
        self.assertEqual(("spell", "triggered_ability"), (original.kind, trigger.kind))
        self.assertEqual(sage.object_id, trigger.source_object_id)
        self.assertIn(EVENT_SPELL_COPY_CONTEXT, trigger.context)
        self.cast(game, "B", counter, [original.ref])
        self.resolve_top(game)
        self.assertEqual([trigger.ref], [row.ref for row in game.state.stack])
        self.assertEqual("graveyard", game.state.cards[sage.object_id].zone)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(["spell_copy"], [row.kind for row in game.state.stack])
        self.assertIsNone(self.resolve(game))
        token, = self._bodies(game)
        self.assertFalse(token.is_card_object)
        self.assertLessEqual({"flying", "lifelink"}, set(game.engine._combat_keywords(token)))
        self.assertEqual(2, len(self._casts(game)))
        self.replay(game, load=True)

    def test_omitting_the_exact_cast_history_lowerings_restores_both_original_residuals(self):
        with (patch.object(resolution_condition_templates, "other_spell_draw_template", return_value=None),
              patch.object(public_trigger_conditions, "_other_spell_self_cast_binding", return_value=None)):
            for name in NAMES:
                report, _ = register_card(self.db, self.registry, name)
                self.assertFalse(report["strict_capability_ready"], report)
            known, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(known["strict_capability_ready"], known["blockers"])

    def test_genuine_history_draw_and_copy_withdrawals_enforce_the_two_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap, denied in ((HISTORY, set(NAMES)), (DRAW, {SLICK}), (EVENT_SPELL_COPY_CAPABILITY, {SAGE})):
            with self.subTest(capability=cap):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == cap).update(
                    status="blocked", blockers=["Scoped other-spell history authority withdrawal"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in NAMES:
                    report, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in denied, report["strict_capability_ready"], report)
                report, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(report["strict_capability_ready"], report["blockers"])


if __name__ == "__main__":
    unittest.main()
