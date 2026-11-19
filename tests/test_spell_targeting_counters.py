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
from quorune.card_programs.runtime_requirements import stack_spell_target_counter_runtime_dependencies
from quorune.carddb import CardDatabase
from quorune.compiler import resolution_effect_templates
from quorune.compiler.counter_templates import spell_targeting_counter_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.selection.target_query import current_spell_target_subjects
from quorune.stack_spell_targeting import (
    CURRENT_TARGET_COUNTER,
    StackSpellTargetPredicate as Predicate,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
COUNTER = "stack.counter.effect"
REVALIDATE = "target.revalidate_resolution"
DRAW = "zone.draw.library_to_hand"
ORIGINALS = {
    "Avoid Fate": Predicate.INSTANT_OR_AURA_CONTROLLED_PERMANENT,
    "Cerulean Drake": Predicate.CONTROLLER,
    "Confound": Predicate.CREATURE,
    "Fugitive Droid": Predicate.CONTROLLED_ARTIFACT_OR_CREATURE,
    "Hindering Light": Predicate.CONTROLLER_OR_CONTROLLED_PERMANENT,
    "Hydromorph Guardian": Predicate.CONTROLLED_CREATURE,
    "Hydromorph Gull": Predicate.CONTROLLED_CREATURE,
    "Intervene": Predicate.CREATURE,
    "Keep Safe": Predicate.CONTROLLED_PERMANENT,
    "Mistfolk": Predicate.SOURCE,
    "Outwit": Predicate.PLAYER,
    "Rebuff the Wicked": Predicate.CONTROLLED_PERMANENT,
    "Ring of Immortals": Predicate.INSTANT_OR_AURA_CONTROLLED_PERMANENT,
    "Vigilant Martyr": Predicate.ENCHANTMENT,
}
CANTRIPS = {"Confound", "Hindering Light", "Keep Safe"}


class SpellTargetingCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-source-counter-cards.json": {"Shock", "Sol Ring", "Twincast"},
            "activation-restriction-material-cards.json": {"Cloudshift"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "any-number-target-cards.json": {"Pacifism"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "combat-defending-player-cards.json": {"Snakeskin Veil"},
            "intrinsic-empty-spell-resolution-cards.json": {"Void Rend"},
            "mandatory-single-stack-target-change-cards.json": {"Shunt"},
            "recipient-control-cards.json": {"Arc Trail"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "spell-targeting-counter-cards.json": set(ORIGINALS),
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        path = folder / "spell-targeting-counters.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current spell targets", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_counter(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        program, = (p for p in semantics.programs_for_oracle(card.oracle_id)
                    if p.effects and p.effects[0].get("op") == "counter_stack_target")
        return card, program

    def test_all_pinned_spell_target_counters_preserve_complete_originals_and_exact_scopes(self):
        for name, predicate in ORIGINALS.items():
            with self.subTest(name=name):
                card, program = self.original_counter(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(predicate.target_schema(), thaw_value(program.target_schema))
                effects = thaw_value(program.effects)
                self.assertEqual(CURRENT_TARGET_COUNTER, effects[0])
                self.assertEqual(1 + (name in CANTRIPS), len(effects))
                required = set(stack_spell_target_counter_runtime_dependencies(program))
                self.assertEqual({COUNTER, REVALIDATE} | ({DRAW} if name in CANTRIPS else set()), required)
                self.assertLessEqual(required, set(program.capability_dependencies))
                if name in CANTRIPS:
                    self.assertEqual({"op": "draw", "player": "$controller", "count": 1, "private": True}, effects[1])

    def test_spell_target_counter_shapes_reject_widened_filters_and_missing_draw_or_target_authority(self):
        _, original = self.original_counter("Keep Safe")
        schema = thaw_value(original.target_schema)
        effects = thaw_value(original.effects)
        for changes in (
            {"target_schema": {**schema, "count": True}},
            {"target_schema": {**schema, "zones": ["stack", "battlefield"]}},
            {"target_schema": {"groups": [schema]}},
            {"target_schema": {**schema, "source_exclusion": False}},
            {"effects": [{**effects[0], "stack": "$target.1"}, effects[1]]},
            {"effects": [effects[1], effects[0]]},
            {"effects": [effects[0], {**effects[1], "private": False}]},
            {"provenance": {**original.provenance, "components": []}},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(original, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)
        for text in (
            "Counter target spell that targets only a creature.",
            "Counter target spell that targeted a creature.",
            "Counter target spell that targets a creature or a player.",
            "Counter target sorcery spell that targets a creature.",
            "Counter target spell that targets a creature card.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(spell_targeting_counter_effect_template(text))

    def _actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def _spell(self, game, card):
        item, = (item for item in game.state.stack if item.card_object_id == card.object_id)
        return item

    def _activation(self, game, card, spell, seat="A"):
        action, = (
            row for row in self._actions(game, seat)
            if row["id"].startswith("activate:" + card.ref + ":")
            and spell in row.get("target_schema", {}).get("legal_refs", ())
        )
        return action

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _hand(self, game, seat):
        return set(game.state.players[seat].zones["hand"])

    def _assert_private_draw(self, game, before):
        drawn, = self._hand(game, "A") - before
        ref = game.state.cards[drawn].ref
        self.assertIn(ref, json.dumps(game.packet("pilot:A", full=True)))
        for seat in "BCD":
            self.assertNotIn(ref, json.dumps(game.packet("pilot:" + seat, full=True)))

    def _countered(self, game, ref):
        return any(event.code == "stack.counter" and event.details["stack"] == ref
                   for event in game.state.events)

    def test_actual_keep_safe_draws_after_an_uncounterable_spell_and_preserves_draw_privacy(self):
        game = self.session(77131001)
        keep = self.add(game.engine, "Keep Safe", zone="hand")
        body = self.add(game.engine, "Walking Corpse")
        uncounterable = self.add(game.engine, "Void Rend", seat="B", zone="hand")
        for card in (keep, body, uncounterable):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"W": 1, "U": 1, "B": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "B", uncounterable, [body.ref])
        target = self._spell(game, uncounterable).ref
        self.cast(game, "A", keep, [target])
        before = self._hand(game, "A")
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        events = game.state.events
        failure = next(i for i, event in enumerate(events)
                       if event.code == "stack.counter.failed" and event.details["stack"] == target)
        self.assertTrue(any(event.code == "card.draw" for event in events[failure + 1:]))
        self._assert_private_draw(game, before)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[body.object_id].zone)
        self.replay(game, load=True)

    def test_actual_confound_still_counts_a_present_creature_after_an_inner_target_gains_hexproof(self):
        game = self.session(77131002)
        confound = self.add(game.engine, "Confound", zone="hand")
        body = self.add(game.engine, "Walking Corpse")
        veil = self.add(game.engine, "Snakeskin Veil", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        for card in (confound, body, veil, shock):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 1})
        game.state.players["A"].mana_pool.update({"C": 1, "U": 1, "G": 1})
        self.checkpoint(game)
        self.cast(game, "B", shock, [body.ref])
        target = self._spell(game, shock).ref
        self.priority(game, "A")
        offered = self.action(game, "A", confound)
        paid = game.act("pilot:A", {"action_id": offered["id"], "targets": [target],
                                    "pay": "manual", "payment": {"C": 1, "U": 1}})
        self.assertTrue(paid.ok, paid.summary)
        self.cast(game, "A", veil, [body.ref])
        before = self._hand(game, "A")
        self.resolve_top(game)
        current = game.state.cards[body.object_id]
        self.assertIn("hexproof", game.engine._combat_keywords(current))
        inner = self._spell(game, shock)
        self.assertEqual([current.ref], inner.targets)
        filtered = replace(inner, targets=[None], context={**inner.context, "targets_revalidated": True})
        self.assertEqual([current], list(current_spell_target_subjects(game.engine, filtered)))
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._countered(game, target))
        self._assert_private_draw(game, before)
        self.assertEqual(0, game.state.cards[body.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_hindering_light_loses_its_only_target_after_a_new_permanent_incarnation_returns(self):
        game = self.session(77131003)
        hindering = self.add(game.engine, "Hindering Light", zone="hand")
        body = self.add(game.engine, "Walking Corpse")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        for card in (hindering, body, blink, shock):
            self.assert_closed(game, card)
        original_incarnation = body.logical_object_id
        self.prepare(game, active="B", actor="B", mana={"R": 1})
        game.state.players["A"].mana_pool.update({"W": 2, "U": 1})
        self.checkpoint(game)
        self.cast(game, "B", shock, [body.ref])
        target = self._spell(game, shock).ref
        self.cast(game, "A", hindering, [target])
        self.cast(game, "A", blink, [body.ref])
        before = self._hand(game, "A")
        self.resolve_top(game)
        latest = game.state.cards[body.object_id]
        self.assertEqual("battlefield", latest.zone)
        self.assertNotEqual(original_incarnation, latest.logical_object_id)
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertTrue(any(item.ref == target for item in game.state.stack))
        self.assertEqual(before, self._hand(game, "A"))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(0, game.state.cards[body.object_id].marked_damage)
        self.replay(game, load=True)

    def test_actual_outwit_and_intervene_separate_original_and_retargeted_copy_in_multiplayer(self):
        game = self.session(77131004)
        outwit = self.add(game.engine, "Outwit", zone="hand")
        intervene = self.add(game.engine, "Intervene", zone="hand")
        trail = self.add(game.engine, "Arc Trail", seat="B", zone="hand")
        twincast = self.add(game.engine, "Twincast", seat="C", zone="hand")
        first = self.add(game.engine, "Walking Corpse")
        second = self.add(game.engine, "Walking Corpse", seat="C", ref="untargeted-copy-subject")
        for card in (outwit, intervene, trail, twincast, first, second):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"C": 1, "R": 1})
        game.state.players["A"].mana_pool["U"] = 2
        game.state.players["C"].mana_pool["U"] = 2
        self.checkpoint(game)
        self.cast(game, "B", trail, ["A", "D"])
        original = self._spell(game, trail).ref
        self.cast(game, "C", twincast, [original])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        self._reject(game, "D", {"action_id": "choose", "targets": [first.ref, second.ref]})
        retargeted = game.act("pilot:C", {"action_id": "choose", "targets": [first.ref, second.ref]})
        self.assertTrue(retargeted.ok, retargeted.summary)
        copy, = (item for item in game.state.stack if item.kind == "spell_copy")
        self.assertEqual([first.ref, second.ref], copy.targets)
        actions = {row["id"]: row for row in self._actions(game, "A")}
        for card, legal, denied in ((outwit, original, copy.ref), (intervene, copy.ref, original)):
            offer = actions["cast:" + card.ref]
            self.assertEqual([legal], offer["target_schema"]["legal_refs"])
            self._reject(game, "A", {"action_id": offer["id"], "targets": [denied], "pay": "auto"})
        self.cast(game, "A", outwit, [original])
        self.cast(game, "A", intervene, [copy.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._countered(game, original))
        self.assertTrue(self._countered(game, copy.ref))
        self.assertEqual([40] * 4, [game.state.players[seat].life for seat in "ABCD"])
        self.replay(game, load=True)

    def test_actual_avoid_fate_accepts_aura_and_rejects_sorcery_with_the_same_controlled_subject(self):
        for aura in (False, True):
            with self.subTest(aura=aura):
                game = self.session(77131005 + aura)
                avoid = self.add(game.engine, "Avoid Fate", zone="hand")
                body = self.add(game.engine, "Walking Corpse")
                spell = self.add(game.engine, "Pacifism" if aura else "Arc Trail", seat="B", zone="hand")
                for card in (avoid, body, spell):
                    self.assert_closed(game, card)
                self.prepare(game, active="B", actor="B", mana={"C": 1, "R": 1, "W": 1})
                game.state.players["A"].mana_pool["G"] = 1
                self.checkpoint(game)
                self.cast(game, "B", spell, [body.ref] if aura else [body.ref, "D"])
                stack_ref = self._spell(game, spell).ref
                action_id = "cast:" + avoid.ref
                if aura:
                    self.priority(game, "A")
                    self.assertEqual([stack_ref], self.action(game, "A", avoid)["target_schema"]["legal_refs"])
                    self.cast(game, "A", avoid, [stack_ref])
                else:
                    self.assertNotIn(action_id, {row["id"] for row in self._actions(game, "A")})
                    self._reject(game, "A", {"action_id": action_id, "targets": [stack_ref], "pay": "auto"})
                self.assertIsNone(self.resolve(game))
                self.assertEqual(aura, self._countered(game, stack_ref))
                self.assertEqual("battlefield" if aura else "graveyard", game.state.cards[body.object_id].zone)
                self.replay(game, load=True)

    def test_actual_drake_requires_its_controller_and_binds_sacrifice_to_the_accepted_target(self):
        game = self.session(77131007)
        drake = self.add(game.engine, "Cerulean Drake")
        wrong = self.add(game.engine, "Shock", seat="B", zone="hand", ref="other-player-shock")
        right = self.add(game.engine, "Shock", seat="B", zone="hand", ref="controller-shock")
        for card in (drake, wrong, right):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 2})
        self.checkpoint(game)
        self.cast(game, "B", wrong, ["C"])
        first = self._spell(game, wrong).ref
        self.assertFalse(any(row["id"].startswith("activate:" + drake.ref + ":")
                             for row in self._actions(game, "A")))
        self.cast(game, "B", right, ["A"])
        second = self._spell(game, right).ref
        action = self._activation(game, drake, second)
        self.assertEqual([second], action["target_schema"]["legal_refs"])
        command = {"action_id": action["id"], "targets": [second], "pay": "auto"}
        self._reject(game, "C", command)
        self._reject(game, "A", {**command, "targets": [first]})
        self.assertEqual("battlefield", game.state.cards[drake.object_id].zone)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("graveyard", game.state.cards[drake.object_id].zone)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._countered(game, second))
        self.assertEqual((40, 38), (game.state.players["A"].life, game.state.players["C"].life))
        self.replay(game, load=True)

    def test_actual_mistfolk_protects_the_exact_source_and_not_another_with_the_same_name(self):
        game = self.session(77131008)
        source = self.add(game.engine, "Mistfolk")
        namesake = self.add(game.engine, "Mistfolk", ref="different-mistfolk")
        first = self.add(game.engine, "Shock", seat="B", zone="hand", ref="namesake-shock")
        second = self.add(game.engine, "Shock", seat="B", zone="hand", ref="mistfolk-source-shock")
        for card in (source, namesake, first, second):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 2})
        game.state.players["A"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "B", first, [namesake.ref])
        first_ref = self._spell(game, first).ref
        self.assertFalse(any(row["id"].startswith("activate:" + source.ref + ":")
                             for row in self._actions(game, "A")))
        self.cast(game, "B", second, [source.ref])
        second_ref = self._spell(game, second).ref
        action = self._activation(game, source, second_ref)
        self.assertEqual([second_ref], action["target_schema"]["legal_refs"])
        accepted = game.act("pilot:A", {"action_id": action["id"], "targets": [second_ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._countered(game, second_ref))
        self.assertFalse(self._countered(game, first_ref))
        self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[namesake.object_id].zone)
        self.replay(game, load=True)

    def test_actual_rebuff_revalidates_the_original_spells_new_subject_after_shunt(self):
        game = self.session(77131009)
        rebuff = self.add(game.engine, "Rebuff the Wicked", zone="hand")
        body = self.add(game.engine, "Walking Corpse")
        shock = self.add(game.engine, "Shock", seat="B", zone="hand")
        shunt = self.add(game.engine, "Shunt", seat="C", zone="hand")
        for card in (rebuff, body, shock, shunt):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 1})
        game.state.players["A"].mana_pool["W"] = 1
        game.state.players["C"].mana_pool.update({"C": 1, "R": 2})
        self.checkpoint(game)
        self.cast(game, "B", shock, [body.ref])
        spell = self._spell(game, shock).ref
        self.cast(game, "A", rebuff, [spell])
        self.cast(game, "C", shunt, [spell])
        decision = self.resolve(game)
        self.assertEqual(("semantic.choice", ["C"]), (decision.kind, decision.actors))
        game = self.replay(game, load=True)
        result = game.act("pilot:C", {"action_id": "choose", "targets": ["D"]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["D"], self._spell(game, shock).targets)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(self._countered(game, spell))
        self.assertEqual(38, game.state.players["D"].life)
        self.assertEqual("battlefield", game.state.cards[body.object_id].zone)
        self.replay(game, load=True)

    def test_actual_outwit_loses_the_departed_player_and_can_counter_a_remaining_players_spell(self):
        game = self.session(77131010)
        outwit = self.add(game.engine, "Outwit", zone="hand")
        departed = self.add(game.engine, "Shock", seat="B", zone="hand", ref="departed-player-target")
        present = self.add(game.engine, "Shock", seat="B", zone="hand", ref="present-player-target")
        for card in (outwit, departed, present):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B", mana={"R": 2})
        game.state.players["A"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "B", departed, ["C"])
        self.cast(game, "B", present, ["D"])
        departed_ref = self._spell(game, departed).ref
        present_ref = self._spell(game, present).ref
        self.priority(game, "C")
        left = game.act("pilot:C", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(left.ok, left.summary)
        self.assertFalse(game.state.players["C"].in_game)
        self.assertIn(departed_ref, {item.ref for item in game.state.stack})
        self.priority(game, "A")
        offered = self.action(game, "A", outwit)
        self.assertEqual([present_ref], offered["target_schema"]["legal_refs"])
        self._reject(game, "A", {"action_id": offered["id"], "targets": [departed_ref], "pay": "auto"})
        self.cast(game, "A", outwit, [present_ref])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(self._countered(game, present_ref))
        self.assertEqual(40, game.state.players["D"].life)
        self.replay(game, load=True)

    def test_spell_targeting_lowering_omission_leaves_originals_incomplete(self):
        compilers = tuple(template for template in resolution_effect_templates._DIRECT_RESOLUTION_EFFECT_COMPILERS
                          if template is not spell_targeting_counter_effect_template)
        with patch.object(resolution_effect_templates, "_DIRECT_RESOLUTION_EFFECT_COMPILERS", compilers):
            for name in ("Keep Safe", "Avoid Fate", "Fugitive Droid", "Mistfolk"):
                with self.subTest(name=name):
                    result, _ = register_card(self.db, self.registry, name)
                    self.assertFalse(result["strict_capability_ready"], result)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_withdrawal_distinguishes_spell_target_revalidation_counter_and_private_draw(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (COUNTER, REVALIDATE, DRAW):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Current spell target counter authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name, expected in (("Avoid Fate", capability == DRAW),
                                       ("Cerulean Drake", capability == DRAW), ("Keep Safe", False),
                                       ("Sol Ring", True)):
                    result, _ = register_card(self.db, registry, name)
                    self.assertEqual(expected, result["strict_capability_ready"], result)
