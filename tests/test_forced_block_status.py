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
from quorune.block_transitions import BlockTransitionAssignment, BlockTransitionError, BlockTransitionEvent, BlockTransitionParticipant
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.combat_relationship_state import is_unblocked_attacker_after_declaration
from quorune.compiler import resolution_effect_templates
from quorune.continuous_conditions import FixedPublicStateFact
from quorune.deck import DeckDefinition, DeckEntry
from quorune.delayed_object_model import forced_block_status_effect, forced_block_status_target_schema
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.record_state_provenance import format_state_versions, validate_state_versions
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.targets import TargetGroup
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
CURTAIN = "Curtain of Light"
DAZZLING = "Dazzling Beauty"
RUNNER = "Trap Runner"
ORIGINALS = (CURTAIN, DAZZLING, RUNNER)
BLOCK_STATUS = "combat.declaration.typed_components"
CAST_WINDOW = "casting.restriction.self_fixed_public_condition"
ACTIVATE_WINDOW = "activation.condition.fixed_public_state"


class ForcedBlockStatusTests(InheritedNativeFixture, unittest.TestCase):
    finish_turn = OptionalResultFollowupFixture.finish_turn

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Island", "Unsummon"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "banding-cards.json": {"Ninja of the Deep Hours"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "rampage-cards.json": {"Craw Giant"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "forced-block-status-cards.json": set(ORIGINALS) | {"Devoted Retainer", "Triton Shorestalker"},
        }
        pieces = []
        for filename, names in support.items():
            doc = json.loads((FIXTURES / filename).read_bytes())
            doc["cards"] = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in doc["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in doc["cards"]}
            doc["rulings"] = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(doc), encoding="utf-8")
            pieces.append(piece)
        path = folder / "forced-block-status.sqlite3"
        build_fixture_database(pieces, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Blocked status", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_result(self, name):
        card = self.db.lookup(name, fuzzy=False)
        bound, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        result, = [program for program in programs if program.effects
                  and program.effects[0].get("action") == "become_blocked"]
        return card, result, programs

    def test_forced_block_originals_bind_exact_target_timing_and_independent_draws(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card, result, programs = self.original_result(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual(forced_block_status_target_schema(), thaw_value(result.target_schema))
                self.assertEqual(forced_block_status_effect(), thaw_value(result.effects[0]))
                self.assertLessEqual({BLOCK_STATUS, "target.revalidate_resolution",
                                      "trigger.event.normalized_public_action"}, set(result.capability_dependencies))
                if name == RUNNER:
                    self.assertEqual(("activate", "battlefield"), (result.event, result.active_zone))
                    self.assertIn(ACTIVATE_WINDOW, result.capability_dependencies)
                    self.assertEqual(1, len(result.effects))
                else:
                    restriction, = [program for program in programs if program.event == "cast.restriction"]
                    fact = (FixedPublicStateFact.CURRENT_DECLARE_BLOCKERS_STEP if name == DAZZLING
                            else FixedPublicStateFact.CURRENT_COMBAT_AFTER_BLOCKERS)
                    self.assertEqual(fact.value, restriction.handlers[0]["source_condition"]["fact"])
                    self.assertIn(CAST_WINDOW, restriction.capability_dependencies)
                    self.assertEqual(["resolve_fixed_object_action", "delayed_trigger" if name == DAZZLING else "draw"],
                                     [effect["op"] for effect in result.effects])

    def test_status_only_block_codec_rejects_physical_pairs_and_improper_current_targets(self):
        attacker = BlockTransitionParticipant(
            object_id="attacker", logical_object_id="attacker@2", reference="O1", controller="A")
        args = {"turn_sequence": 2, "priority_epoch": 10, "active_player": "A", "participants": (attacker,)}
        event = BlockTransitionEvent.create(**args, assignments=(), schema_version=2)
        self.assertEqual({"attacker"}, event.blocked_attacker_ids)
        self.assertEqual(event, BlockTransitionEvent.from_dict(event.to_dict()))
        self.assertEqual("unblocked_attacking", TargetGroup.from_mapping(forced_block_status_target_schema()).combat_state)
        with self.assertRaises(BlockTransitionError):
            BlockTransitionEvent.create(**args, assignments=(BlockTransitionAssignment("attacker", "blocker"),),
                                        schema_version=2)
        with self.assertRaises(BlockTransitionError):
            BlockTransitionEvent.create(**args, assignments=())
        _, result, _ = self.original_result(RUNNER)
        original, = thaw_value(result.effects)
        for changes in (
            {"effects": [{**original, "schema_version": 1}]},
            {"effects": [{**original, "object": "$source"}]},
            {"effects": [{**original, "blocker": "$target.1"}]},
            {"target_schema": {**forced_block_status_target_schema(), "combat_state": "attacking"}},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(result, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)

    def _mana_lands(self, game, seat, name, count):
        for index in range(count):
            self.add(game.engine, name, seat=seat, ref=f"forced-block-payment-{seat}-{index}")

    def _actions(self, game, seat):
        self.priority(game, seat)
        return game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + seat, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _declare_then_pass_blockers(self, game, attackers):
        self.attack(game, [(attacker, "B") for attacker in attackers])
        self.until(game, lambda s: s.state.step == "declare_blockers" and s.state.combat.blockers_declared)

    def _status_event(self, game, card):
        raw, = [event.details["transition"] for event in game.state.events
                if event.code == "combat.block_transition" and event.details["transition"]["schema_version"] == 2]
        transition = BlockTransitionEvent.from_dict(raw)
        self.assertEqual((), transition.assignments)
        self.assertEqual({card.object_id}, transition.blocked_attacker_ids)
        self.assertEqual(card.logical_object_id, transition.participants[0].logical_object_id)
        self.assertEqual([], game.state.combat.blockers[card.object_id])

    def test_actual_curtain_blocks_an_unblockable_attacker_and_closes_its_ninjutsu_offer(self):
        game = self.session(77135001)
        attacker = self.add(game.engine, "Triton Shorestalker")
        attacker.acquired_control_turn_count = -1
        ninja = self.add(game.engine, "Ninja of the Deep Hours", zone="hand")
        curtain = self.add(game.engine, CURTAIN, seat="B", zone="hand")
        for card in (attacker, ninja, curtain):
            self.assert_closed(game, card)
        self._mana_lands(game, "A", "Island", 2)
        self._mana_lands(game, "B", "Generic Bound Plains", 2)
        self.prepare(game)
        self.checkpoint(game)
        unavailable = "cast:" + curtain.ref
        self.assertNotIn(unavailable, {row["id"] for row in self._actions(game, "B")})
        self._reject(game, "B", {"action_id": unavailable, "targets": [attacker.ref], "pay": "auto"})
        self._declare_then_pass_blockers(game, [attacker])
        before = [row for row in self._actions(game, "A") if ninja.ref in row["id"]]
        self.assertEqual(1, len(before))
        self.cast(game, "B", curtain, [attacker.ref])
        hand_before = set(game.state.players["B"].zones["hand"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self._status_event(game, game.state.cards[attacker.object_id])
        drawn, = set(game.state.players["B"].zones["hand"]) - hand_before
        private = game.state.cards[drawn].ref
        for seat in "ACD":
            self.assertNotIn(private, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.assertFalse(any(ninja.ref in row["id"] for row in self._actions(game, "A")))
        self.until(game, lambda s: s.state.step == "end_combat")
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def _return_original_attacker_through_ninjutsu(self, *, historical):
        game = self.session(77135010)
        ninja = self.add(game.engine, "Ninja of the Deep Hours")
        substitute = self.add(game.engine, "Triton Shorestalker")
        blocker = self.add(game.engine, "Walking Corpse", seat="B")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self._mana_lands(game, "A", "Island", 2)
        self._mana_lands(game, "B", "Island", 1)
        for card in (ninja, substitute, blocker, bounce):
            self.assert_closed(game, card)
        for card in (ninja, substitute):
            card.acquired_control_turn_count = -1
        if historical:
            game.state.combat.detached_blocker_ids = None
        provenance = format_state_versions(game.state)
        self.assertEqual(not historical, "combat_relationship_version" in provenance)
        validate_state_versions({"format": provenance}, game.state)
        with self.assertRaisesRegex(ValueError, "Combat relationship provenance"):
            validate_state_versions({"format": {**provenance, "combat_relationship_version": True}}, game.state)
        self.prepare(game)
        self.checkpoint(game)
        old_identity = ninja.logical_object_id
        self.attack(game, [(ninja, "B"), (substitute, "B")])
        self.until(game, lambda current: current.state.pending_decision.kind == "combat.blockers")
        block = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: ninja.ref}})
        self.assertTrue(block.ok, block.summary)
        self.cast_for(game, "B", bounce, [ninja.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[ninja.object_id].zone)
        action, = [row for row in self._actions(game, "A") if row["id"].startswith(f"activate:{ninja.ref}:")]
        result = game.act("pilot:A", {"action_id": action["id"], "cost_objects": [substitute.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)
        returned = game.state.cards[ninja.object_id]
        self.assertNotEqual(old_identity, returned.logical_object_id)
        self.assertEqual("B", game.state.combat.attackers[returned.object_id])
        self.assertEqual(ninja.object_id, game.state.cards[blocker.object_id].blocking)
        return game, returned, blocker

    def test_actual_ninjutsu_reentry_starts_unblocked_and_keeps_the_previous_blocker_blocking(self):
        game, ninja, blocker = self._return_original_attacker_through_ninjutsu(historical=False)
        self.assertTrue(is_unblocked_attacker_after_declaration(game.state.combat, ninja))
        self.assertNotIn(ninja.object_id, game.state.combat.blockers)
        self.assertEqual([blocker.object_id], game.state.combat.detached_blocker_ids)
        self.until(game, lambda s: s.state.step == "combat_damage" and s.state.pending_decision.kind == "priority")
        self.assertEqual(38, game.state.players["B"].life)
        self.assertEqual(0, game.state.cards[blocker.object_id].marked_damage)
        self.assertEqual(ninja.object_id, game.state.cards[blocker.object_id].blocking)
        self.replay(game, load=True)

    def test_ninjutsu_reentry_preserves_the_earlier_recorded_damage_and_replay(self):
        game, ninja, blocker = self._return_original_attacker_through_ninjutsu(historical=True)
        self.assertNotIn("detached_blocker_ids", game.state.combat.to_dict())
        self.assertEqual([blocker.object_id], game.state.combat.blockers[ninja.object_id])
        self.until(game, lambda s: s.state.step == "combat_damage" and s.state.pending_decision.kind == "priority")
        self.assertEqual(40, game.state.players["B"].life)
        self.assertEqual("graveyard", game.state.cards[ninja.object_id].zone)
        self.replay(game, load=True)

    def test_actual_trap_runner_checks_completed_blockers_and_fires_bushido_without_a_pair(self):
        game = self.session(77135002)
        attacker = self.add(game.engine, "Devoted Retainer")
        attacker.acquired_control_turn_count = -1
        runner = self.add(game.engine, RUNNER, seat="B")
        runner.acquired_control_turn_count = -1
        reply = self.add(game.engine, CURTAIN, seat="C", zone="hand")
        self._mana_lands(game, "C", "Generic Bound Plains", 2)
        for card in (attacker, runner, reply):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        prefix = "activate:" + runner.ref + ":"
        self.assertFalse(any(row["id"].startswith(prefix) for row in self._actions(game, "B")))
        self._declare_then_pass_blockers(game, [attacker])
        self.cast(game, "C", reply, [attacker.ref])
        hand_after_cast = set(game.state.players["C"].zones["hand"])
        offered, = [row for row in self._actions(game, "B") if row["id"].startswith(prefix)]
        self.assertEqual([attacker.ref], offered["target_schema"]["legal_refs"])
        command = {"action_id": offered["id"], "targets": [attacker.ref], "pay": "auto"}
        self._reject(game, "C", command)
        accepted = game.act("pilot:B", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertTrue(game.state.cards[runner.object_id].tapped)
        original = game.engine._numeric_stat(attacker.object_id, "power")
        self.resolve_top(game)
        self._status_event(game, game.state.cards[attacker.object_id])
        bushido, = (item for item in game.state.stack if item.kind == "triggered_ability")
        self.assertEqual("bushido", bushido.context["block_keyword_trigger"]["kind"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(original + 1, game.engine._numeric_stat(attacker.object_id, "power"))
        self.assertEqual(hand_after_cast, set(game.state.players["C"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[reply.object_id].zone)
        self.assertIsNone(game.state.cards[runner.object_id].blocking)
        self.until(game, lambda s: s.state.step == "end_combat")
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_actual_dazzling_checks_declare_blockers_step_preserves_trample_and_delays_draw(self):
        game = self.session(77135003)
        giant = self.add(game.engine, "Craw Giant")
        second = self.add(game.engine, "Walking Corpse")
        for card in (giant, second):
            card.acquired_control_turn_count = -1
        beauty = self.add(game.engine, DAZZLING, seat="C", zone="hand")
        later = self.add(game.engine, DAZZLING, seat="C", zone="hand", ref="dazzling-later")
        curtain = self.add(game.engine, CURTAIN, seat="C", zone="hand")
        self._mana_lands(game, "C", "Generic Bound Plains", 5)
        for card in (giant, second, beauty, later, curtain):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self._declare_then_pass_blockers(game, [giant, second])
        power = game.engine._numeric_stat(giant.object_id, "power")
        other_power = game.engine._numeric_stat(second.object_id, "power")
        self.cast(game, "C", beauty, [giant.ref])
        self.resolve_top(game)
        self._status_event(game, game.state.cards[giant.object_id])
        rampage, = game.state.stack
        self.assertEqual("rampage", rampage.context["block_keyword_trigger"]["kind"])
        self.assertEqual(1, len(game.state.delayed_triggers))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(power, game.engine._numeric_stat(giant.object_id, "power"))
        self.until(game, lambda s: s.state.step == "combat_damage")
        self.assertEqual(40 - power - other_power, game.state.players["B"].life)
        later_actions = {row["id"]: row for row in self._actions(game, "C")}
        self.assertNotIn("cast:" + later.ref, later_actions)
        self.assertEqual([second.ref], later_actions["cast:" + curtain.ref]["target_schema"]["legal_refs"])
        self._reject(game, "C", {"action_id": "cast:" + later.ref, "targets": [second.ref], "pay": "auto"})
        self.finish_turn(game)
        self.until(game, lambda s: s.state.active_player == "B" and s.state.step == "upkeep")
        trigger, = game.state.stack
        self.assertEqual("C", trigger.controller)
        before_draw = set(game.state.players["C"].zones["hand"])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, len(set(game.state.players["C"].zones["hand"]) - before_draw))
        self.assertFalse(any(trigger.active for trigger in game.state.delayed_triggers))
        self.replay(game, load=True)

    def test_current_return_cost_keeps_an_attacker_blocked_after_its_assigned_blocker_leaves(self):
        game = self.session(77135004)
        attacker = self.add(game.engine, "Walking Corpse")
        attacker.acquired_control_turn_count = -1
        blocker = self.add(game.engine, "Walking Corpse", seat="B", ref="departing-blocker")
        ninja = self.add(game.engine, "Ninja of the Deep Hours", zone="hand")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        self._mana_lands(game, "A", "Island", 3)
        for card in (attacker, blocker, ninja, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.assertEqual(2, game.state.unblocked_attacker_event_version)
        self.checkpoint(game)
        self.attack(game, [(attacker, "B")])
        self.until(game, lambda s: s.state.pending_decision.kind == "combat.blockers"
                   and s.state.pending_decision.actors == ["B"])
        blocked = game.act("pilot:B", {"action_id": "block", "blocks": {blocker.ref: attacker.ref}})
        self.assertTrue(blocked.ok, blocked.summary)
        self.until(game, lambda s: s.state.step == "declare_blockers" and s.state.combat.blockers_declared)
        self.assertEqual([blocker.object_id], game.state.combat.blockers[attacker.object_id])
        self.cast(game, "A", bounce, [blocker.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.combat.blockers[attacker.object_id])
        self.assertFalse(any(ninja.ref in row["id"] for row in self._actions(game, "A")))
        self._reject(game, "A", {"action_id": "activate:" + ninja.ref + ":ab1",
                                  "cost_cards": [attacker.ref], "pay": "auto"})
        self.until(game, lambda s: s.state.step == "end_combat")
        self.assertEqual(40, game.state.players["B"].life)
        self.replay(game, load=True)

    def test_omitting_exact_forced_block_result_keeps_all_originals_partial(self):
        with patch.object(resolution_effect_templates, "_forced_block_status_template", return_value=None):
            for name in ORIGINALS:
                binding, _ = register_card(self.db, self.registry, name)
                self.assertFalse(binding["strict_capability_ready"], binding)
            control, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(control["strict_capability_ready"], control["blockers"])

    def test_genuine_forced_block_and_distinct_windows_require_their_actual_authority(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        before = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        rows = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        witnesses = ((BLOCK_STATUS, set()), (CAST_WINDOW, {RUNNER}), (ACTIVATE_WINDOW, {CURTAIN, DAZZLING}))
        for capability, retained in witnesses:
            with self.subTest(capability=capability):
                document = deepcopy(before)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Forced block or action-window authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=rows, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    binding, _ = register_card(self.db, registry, name)
                    self.assertEqual(name in retained, binding["strict_capability_ready"], binding)
                independent, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(independent["strict_capability_ready"], independent["blockers"])
