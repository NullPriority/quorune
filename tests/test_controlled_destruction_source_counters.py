"""Per-player creature slots and one committed destruction reward on the original source."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from test_qualified_zone_event_queries import query_record
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.errors import GameRuleError
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, DEFAULT_CAPABILITY_REGISTRY, load_default_capability_registry
from quorune.rules.controlled_destruction_source_counters import (
    SOURCE_COUNTER_REWARD_CAPABILITY, SOURCE_COUNTER_REWARD_MECHANIC, controlled_destruction_source_counter_effect,
    controlled_destruction_source_counter_program_dependencies, creature_target_role_schema,
)
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.selection.copy_targets import prepare_copy_targets
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


ORIGINAL = "Demonic Junker"
PROFILE = "commander_review"
TRIGGER_TEXT = (
    "When this Vehicle enters, for each player, destroy up to one target creature that player controls. "
    "If a creature you controlled was destroyed this way, put two +1/+1 counters on this Vehicle."
)


class ControlledDestructionSourceCounterTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "controlled-destruction-source-counters.sqlite3"
        fixtures = ROOT / "tests" / "fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "scryfall-exact-lists.json",
            fixtures / "announced-destruction-controller-tokens-cards.json",
            fixtures / "trigger-view-equivalence-cards.json",
            fixtures / "combat-removal-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Grouped source reward", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_grouped_trigger_and_withdrawal_close_exactly(self):
        card = replace(query_record(TRIGGER_TEXT), type_line="Artifact — Vehicle", power="4", toughness="3")
        options = {"capability_registry": self.registry, "capability_profile": PROFILE}
        ir = compile_oracle_card(card, **options)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        trigger, = ir.faces[0].nodes
        self.assertIn(SOURCE_COUNTER_REWARD_MECHANIC, trigger.mechanics)
        self.assertEqual(creature_target_role_schema(), trigger.target_schema)
        self.assertEqual((controlled_destruction_source_counter_effect(),), tuple(trigger.effects))
        for changed in (
            TRIGGER_TEXT.replace("for each player", "for each opponent"),
            TRIGGER_TEXT.replace("you controlled", "an opponent controlled"),
            TRIGGER_TEXT.replace("put two", "put three"),
        ):
            with self.subTest(changed=changed):
                self.assertNotEqual("exact", compile_oracle_card(replace(card, oracle_text=changed), **options).status)

        real = self.db.lookup(ORIGINAL)
        program = compile_best_available_card_program(self.db, real, semantic_registry=SemanticRegistry(), **options)
        result = bind_card_program_runtime(program, capability_registry=self.registry, profile=PROFILE)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        entry, = (row for row in program.abilities if row.event == "permanent.enter.self")
        malformed = replace(entry, target_schema={"groups": [{"id": "slot", "count": 1}]})
        with self.assertRaises(ValueError):
            controlled_destruction_source_counter_program_dependencies(malformed)

        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for dependency in (SOURCE_COUNTER_REWARD_CAPABILITY, "target.set.one_per_player"):
            raw = json.loads(DEFAULT_CAPABILITY_REGISTRY.read_bytes())
            row = next(row for row in raw["capabilities"] if row["id"] == dependency)
            row.update(status="blocked", blockers=["Test-only dependency withdrawal"])
            registry = CapabilityRegistry(raw)
            index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            result = compile_oracle_card(real, capability_registry=registry, capability_profile=PROFILE)
            with self.subTest(dependency=dependency):
                self.assertNotEqual("exact", result.status)
                self.assertTrue(any(dependency in blocker for row in result.material_residuals for blocker in row.blockers))

    def reject(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act(f"pilot:{actor}", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def scene(self, seed, *, owner="A", replacements=False, response=None, own_indestructible=False):
        game = self.session(seed)
        game.state.config.semantic_policy = "trusted_only"
        vehicle = self.add(game.engine, ORIGINAL, zone="hand")
        if not own_indestructible:
            self.add(game.engine, "Sol Ring")
        self.add(game.engine, "Arcane Signet")
        own = self.add(game.engine, "Grizzly Bears", seat=owner, ref="junker-qualifying-victim")
        if owner != "A":
            game.engine.change_control(own.object_id, "A", reason="Fixture separates control and ownership")
        pilot = self.add(game.engine, "Grizzly Bears", ref="junker-crew-pilot")
        enemy = self.add(game.engine, "Grizzly Bears", seat="B", ref="junker-opponent-victim")
        indestructible = self.add(game.engine, "Darksteel Myr", seat="A" if own_indestructible else "C", ref="junker-indestructible")
        self.add(game.engine, "Grizzly Bears", seat="D", ref="junker-other-target")
        if replacements:
            rip = self.add(game.engine, "Rest in Peace", seat="B")
            leyline = self.add(game.engine, "Leyline of the Void", seat="B")
            for card in (rip, leyline):
                self.assert_closed(game, card)
        answer = self.add(game.engine, response, seat="B", zone="hand") if response else None
        if response == "Steal Artifact":
            flash = self.add(game.engine, "Vedalken Orrery", seat="B")
            self.assert_closed(game, flash)
        if answer is not None:
            self.assert_closed(game, answer)
        self.assert_closed(game, vehicle)
        self.prepare(game, mana={"C": 4, "B": 1})
        game.state.players["B"].mana_pool.update({"C": 2, "U": 2, "W": 1})
        self.checkpoint(game)
        return game, vehicle, own, pilot, enemy, indestructible, answer

    def cast_and_issue_targets(self, game, vehicle):
        offer = self.action(game, "A", vehicle)
        self.reject(game, "A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 4}})
        accepted = game.act("pilot:A", {"action_id": offer["id"], "pay": "manual", "payment": {"C": 4, "B": 1}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        decision = game.packet("pilot:A", full=True)["decision"]
        self.assertEqual("semantic.target", decision["kind"])
        groups = decision["ctx"]["target_schema"]["groups"]
        self.assertEqual(["seat_a", "seat_b", "seat_c", "seat_d"], [group["id"] for group in groups])
        self.assertTrue(all(group["min"] == 0 and group["max"] == 1 for group in groups))
        return groups

    def choose_targets(self, game, targets):
        command = {"action_id": "choose", "targets": [
            {"group": f"seat_{seat.lower()}", "ref": card.ref} for seat, card in targets.items()
        ]}
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        item = game.state.stack[-1]
        self.assertEqual("A", item.controller)
        return item

    def counters(self, game, vehicle):
        return game.state.cards[vehicle.object_id].counters.get("+1/+1", 0)

    def test_native_grouped_cast_destruction_and_crew_replay(self):
        game, vehicle, own, pilot, enemy, indestructible, _unused = self.scene(77801, owner="C")
        groups = self.cast_and_issue_targets(game, vehicle)
        self.assertIn(own.ref, groups[0]["legal_refs"])
        self.assertNotIn(own.ref, groups[2]["legal_refs"])
        proposal = {"action_id": "choose", "targets": [
            {"group": "seat_a", "ref": own.ref}, {"group": "seat_a", "ref": pilot.ref}
        ]}
        self.reject(game, "A", proposal)
        self.reject(game, "B", {"action_id": "choose", "targets": []})
        item = self.choose_targets(game, {"A": own, "B": enemy, "C": indestructible})
        schema = game.engine._stack_target_schema(item, game.engine.semantics.get(item.semantic_key))
        copied = dict(actor="A", schema=schema, modes=item.modes, source_ref=vehicle.ref,
            original_targets=item.targets, original_groups=item.context["target_groups"],
            original_snapshots=item.context["target_snapshots"], record_optimization=False)
        with self.assertRaises(GameRuleError):
            prepare_copy_targets(game.engine, **copied, submitted={"seat_a": [groups[3]["legal_refs"][0]],
                "seat_b": [enemy.ref], "seat_c": [indestructible.ref]})
        valid_copy = prepare_copy_targets(game.engine, **copied, submitted={"seat_a": [pilot.ref],
            "seat_b": [enemy.ref], "seat_c": [indestructible.ref]})
        self.assertEqual((pilot.ref, enemy.ref, indestructible.ref), valid_copy.targets)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("C", "graveyard"), (game.state.cards[own.object_id].owner, game.state.cards[own.object_id].zone))
        self.assertEqual("graveyard", game.state.cards[enemy.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[indestructible.object_id].zone)
        self.assertEqual(2, self.counters(game, vehicle))
        self.priority(game, "A")
        crew = next(row["id"] for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
            if row["id"].startswith(f"activate:{vehicle.ref}:"))
        activated = game.act("pilot:A", {"action_id": crew, "pay": "auto", "cost_cards": [pilot.ref]})
        self.assertTrue(activated.ok, activated.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(game.state.cards[pilot.object_id].tapped)
        self.assertEqual((6, 5), tuple(game.engine._numeric_stat(vehicle.object_id, key) for key in ("power", "toughness")))
        self.replay(game, load=True)

    def test_reward_follows_original_source_after_control_change_or_leaves_it_absent(self):
        for index, response in enumerate(("Steal Artifact", "Disenchant", "Planar Incision")):
            with self.subTest(response=response):
                game, vehicle, own, _pilot, _enemy, _dark, answer = self.scene(77810 + index, response=response)
                self.cast_and_issue_targets(game, vehicle)
                original = game.state.cards[vehicle.object_id].logical_object_id
                self.choose_targets(game, {"A": own})
                self.cast(game, "B", answer, [vehicle.ref])
                self.resolve_top(game)
                if response == "Planar Incision":
                    self.assertNotEqual(original, game.state.cards[vehicle.object_id].logical_object_id)
                    self.assertEqual("semantic.target", game.state.pending_decision.kind)
                    self.choose_targets(game, {})
                self.assertIsNone(self.resolve(game))
                self.assertEqual("graveyard", game.state.cards[own.object_id].zone)
                source = game.state.cards[vehicle.object_id]
                if response == "Steal Artifact":
                    self.assertEqual(("B", "battlefield", 2), (source.controller, source.zone, self.counters(game, vehicle)))
                elif response == "Disenchant":
                    self.assertEqual(("graveyard", 0), (source.zone, self.counters(game, vehicle)))
                else:
                    self.assertEqual(("battlefield", 1), (source.zone, self.counters(game, vehicle)))
                self.replay(game, load=True)

    def test_actual_redirect_earns_reward_after_replacement_replay(self):
        game, vehicle, own, *_rows = self.scene(77820, replacements=True)
        self.cast_and_issue_targets(game, vehicle)
        self.choose_targets(game, {"A": own})
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        self.assertEqual(("battlefield", 0), (game.state.cards[own.object_id].zone, self.counters(game, vehicle)))
        game = self.replay(game, load=True)
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertEqual(2, len(options))
        command = {"action_id": "choose", "replacement": options[0]["id"]}
        self.reject(game, "B", command)
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[own.object_id].zone)
        self.assertEqual(2, self.counters(game, vehicle))
        self.replay(game, load=True)

    def test_zero_targets_and_opponent_only_actual_destruction_have_no_reward(self):
        for index, mode in enumerate(("none", "opponent", "own_indestructible")):
            with self.subTest(mode=mode):
                game, vehicle, _own, _pilot, enemy, dark, _answer = self.scene(
                    77830 + index, own_indestructible=mode == "own_indestructible",
                )
                self.cast_and_issue_targets(game, vehicle)
                targets = {} if mode == "none" else {"B": enemy}
                if mode == "own_indestructible":
                    targets["A"] = dark
                self.choose_targets(game, targets)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield" if mode == "none" else "graveyard", game.state.cards[enemy.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[dark.object_id].zone)
                self.assertEqual(0, self.counters(game, vehicle))
                self.replay(game, load=True)

    def test_omission_of_committed_destruction_is_detected(self):
        from quorune import destruction

        original = destruction.destroy_permanent_refs
        def omitted(*args, **kwargs):
            result = original(*args, **kwargs)
            return replace(result, destroyed_object_ids=())

        def witness(seed):
            game, vehicle, own, *_rows = self.scene(seed)
            self.cast_and_issue_targets(game, vehicle)
            self.choose_targets(game, {"A": own})
            self.assertIsNone(self.resolve(game))
            self.assertEqual(2, self.counters(game, vehicle), "Committed destruction reward was omitted")

        witness(77840)
        with patch.object(destruction, "destroy_permanent_refs", side_effect=omitted):
            with self.assertRaisesRegex(AssertionError, "Committed destruction reward was omitted"):
                witness(77841)
