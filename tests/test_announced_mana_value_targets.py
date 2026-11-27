"""Native casting and target validation for mana value limited by announced X."""

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch

from common import ROOT
import test_qualified_hexproof as native
import test_splice as splice_tests
from test_controller_creature_keyword_absence import register_card
from quorune.rules.announced_mana_value_target import (
    ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY, ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


EXILE = "Vault Passage"
CONTROL = "River Claim"
LAND_RETURN_ARCHITECT = "Tameshi, Reality Architect"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class AnnouncedManaValueTargetTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = native.QualifiedHexproofTests.session
    add = native.QualifiedHexproofTests.add
    prepare = native.QualifiedHexproofTests.prepare
    checkpoint = native.QualifiedHexproofTests.checkpoint
    resolve = native.QualifiedHexproofTests.resolve
    replay = native.QualifiedHexproofTests.replay
    binding = native.QualifiedHexproofTests.binding
    priority = native.QualifiedHexproofTests.priority
    cast = native.QualifiedHexproofTests.cast
    start_splice = splice_tests.SpliceRuntimeTests.start_splice
    splice_actions = splice_tests.SpliceRuntimeTests.splice_actions
    add_splice = splice_tests.SpliceRuntimeTests.add_splice

    @classmethod
    def setUpClass(cls):
        cls.temp = TemporaryDirectory()
        path = Path(cls.temp.name) / "announced-target.sqlite3"
        build_fixture_database([FIXTURES / "bound-effect-program-cards.json",
                                FIXTURES / "announced-mana-value-target-cards.json",
                                FIXTURES / "fixed-stack-spell-copy-cards.json",
                                FIXTURES / "splice-cards.json",
                                FIXTURES / "typed-land-entry-cards.json",
                                FIXTURES / "announced-graveyard-copy-cards.json",
                                FIXTURES / "fixed-public-state-characteristics-cards.json",
                                FIXTURES / "damage-linked-graveyard-outcome-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced target witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def cast_offer(self, session, spell, mana):
        self.prepare(session, mana=mana)
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"] == "cast:" + spell.ref)

    def agadeem_native(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, "Agadeem's Awakening // Agadeem, the Undercrypt", zone="hand")
        session.state.config.semantic_policy = "trusted_only"
        binding = self.binding(session, source)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        self.assertEqual("capability_closed", binding["trust_basis"])
        return session, source

    def test_distinct_graveyard_mana_values_bind_each_target_and_replay(self):
        session, spell = self.agadeem_native(601_320)
        engine = session.engine
        zero = self.add(engine, "Surcharge Spark", zone="graveyard", ref="agadeem-zero")
        one = self.add(engine, "Generic Bound Body", zone="graveyard", ref="agadeem-one")
        two = self.add(engine, "Surcharge Guard", zone="graveyard", ref="agadeem-two")
        three = self.add(engine, "River Sentry", zone="graveyard", ref="agadeem-three")
        duplicate = self.add(engine, "Grizzly Bears", zone="graveyard", ref="agadeem-other-two")
        variable = self.add(engine, "Trial Variable Walker", zone="graveyard", ref="agadeem-printed-x")
        high = self.add(engine, "Folded Sentry", zone="graveyard", ref="agadeem-high")
        foreign = self.add(engine, "Generic Bound Body", seat="B", zone="graveyard", ref="agadeem-foreign")
        noncreature = self.add(engine, "Generic Bound Plains", zone="graveyard", ref="agadeem-land")
        response = self.add(engine, "Cremate", seat="B", zone="hand", ref="agadeem-response")
        hidden = self.add(engine, "Generic Bound Body", seat="D", zone="hand", ref="private-agadeem-decoy")
        session.state.players["B"].mana_pool["B"] = 1
        chosen = [zero.ref, one.ref, two.ref, three.ref]
        payment = {"C": 3, "B": 3}
        action = self.cast_offer(session, spell, payment)
        target = action["target_schema"]
        self.assertNotIn(hidden.ref, json.dumps(session.packet("pilot:A", full=True)))
        self.assertTrue(target["groups"][0]["distinct_mana_values"])
        self.assertEqual(4, target["groups"][0]["max"])
        self.assertEqual(set(chosen + [duplicate.ref, variable.ref]), set(target["legal_refs"]))
        self.assertEqual({"minimum": 0, "maximum": 3}, target["x_bounds_by_ref"][variable.ref])
        self.assertEqual({"minimum": 0, "maximum": 3}, target["x_bounds_by_ref"][zero.ref])
        self.assertEqual({"minimum": 3, "maximum": 3}, target["x_bounds_by_ref"][three.ref])

        before = authoritative_state_hash(session.state)
        command = {"action_id": action["id"], "x": 3, "pay": "manual", "payment": payment}
        for targets in ([two.ref, duplicate.ref], [zero.ref, zero.ref], [zero.ref, variable.ref],
                        [foreign.ref], [high.ref], [noncreature.ref]):
            rejected = session.act("pilot:A", {**command, "targets": targets})
            self.assertFalse(rejected.ok, f"Unauthorized target set accepted: {targets}")
            self.assertEqual(before, authoritative_state_hash(session.state))
            self.assertFalse(session.commands)

        accepted = session.act("pilot:A", {**command, "targets": chosen})
        self.assertTrue(accepted.ok, accepted.summary)
        item = session.state.stack[-1]
        self.assertEqual(chosen, item.targets)
        self.assertEqual(3, item.x_value)
        self.assertEqual(4, item.context["target_schema_override"]["up_to"])
        self.assertTrue(item.context["target_schema_override"]["distinct_mana_values"])
        self.cast(session, "B", response, [two.ref], {"B": 1})
        session = self.replay(session, load=True)
        move = session.engine.move_objects_simultaneously_intent
        with patch.object(session.engine, "move_objects_simultaneously_intent", wraps=move) as moved:
            self.assertIsNone(self.resolve(session))
        returned, = [call.args[0] for call in moved.call_args_list if call.args[0].destination == "battlefield"]
        self.assertEqual({zero.ref, one.ref, three.ref}, set(returned.object_refs))
        self.assertEqual("exile", session.state.cards[two.object_id].zone)
        for card in (zero, one, three):
            current = session.state.cards[card.object_id]
            self.assertEqual(("battlefield", "A", False), (current.zone, current.controller, current.tapped))
        self.replay(session, load=True)

    def test_distinct_graveyard_mana_value_zero_and_all_illegal_replay(self):
        for choice in ("empty", "legal_zero", "removed_zero"):
            with self.subTest(choice=choice):
                session, spell = self.agadeem_native(601_321)
                zero = self.add(session.engine, "Surcharge Spark", zone="graveyard", ref="agadeem-zero")
                answer = self.add(session.engine, "Cremate", seat="B", zone="hand", ref="agadeem-response")
                session.state.players["B"].mana_pool["B"] = 1
                action = self.cast_offer(session, spell, {"B": 3})
                self.assertEqual([zero.ref], action["target_schema"]["legal_refs"])
                targets = [] if choice == "empty" else [zero.ref]
                result = session.act("pilot:A", {"action_id": action["id"], "x": 0, "targets": targets, "pay": "auto"})
                self.assertTrue(result.ok, result.summary)
                stack = session.state.stack[-1].ref
                if choice == "removed_zero":
                    self.cast(session, "B", answer, [zero.ref], {"B": 1})
                self.assertIsNone(self.resolve(session))
                expected = {"empty": "graveyard", "legal_zero": "battlefield", "removed_zero": "exile"}
                self.assertEqual(expected[choice], session.state.cards[zero.object_id].zone)
                self.assertEqual(choice == "removed_zero", any(
                    event.code == "stack.counter" and event.details.get("stack") == stack
                    for event in session.state.events
                ))
                self.replay(session)

    def test_distinct_graveyard_native_reanimation_exceeds_fixed_bound_with_one_batch(self):
        from quorune.replacement_effects import ReplacementClass, ReplacementEffect, SetField
        from quorune.semantic_runtime import zone_replacements

        session, spell = self.agadeem_native(601_325)
        names = ("Surcharge Spark", "Generic Bound Body", "Grizzly Bears", "River Sentry",
                 "Hill Giant", "Fusion Elemental", "Nyxborn Colossus")
        targets = [self.add(session.engine, name, zone="graveyard", ref=f"native-distinct-{index}")
                   for index, name in enumerate(names)]
        refs = [target.ref for target in targets]
        action = self.cast_offer(session, spell, {"C": 6, "B": 3})
        self.assertEqual(7, action["target_schema"]["groups"][0]["max"])
        self.assertEqual(set(refs), set(action["target_schema"]["legal_refs"]))
        returned = session.act("pilot:A", {"action_id": action["id"], "x": 6, "targets": refs, "pay": "auto"})
        self.assertTrue(returned.ok, returned.summary)

        redirect = ReplacementEffect(
            effect_id="test:distinct-reanimation-redirect", source_id="test:exile-instead",
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={
                "object_ref": {"eq": refs[-1]}, "origin": {"eq": "graveyard"},
                "destination": {"eq": "battlefield"},
            },
            operations=(SetField("destination", "exile"),),
        )
        collect = zone_replacements.collect_zone_change_replacement_effects

        def with_redirect(host, **kwargs):
            return (*collect(host, **kwargs), redirect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", with_redirect):
            move = session.engine.move_objects_simultaneously_intent
            with patch.object(session.engine, "move_objects_simultaneously_intent", wraps=move) as moved:
                self.assertIsNone(self.resolve(session))
            batch, = [call.args[0] for call in moved.call_args_list if call.args[0].destination == "battlefield"]
            self.assertEqual(tuple(refs), batch.object_refs)
            self.assertEqual("exile", session.state.cards[targets[-1].object_id].zone)
            self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield" for card in targets[:-1]))
            self.replay(session)

    def test_distinct_graveyard_capability_withdrawal_preserves_existing_single_targets(self):
        from quorune.rules.announced_mana_value_target import DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY

        source = self.db.lookup("Agadeem's Awakening // Agadeem, the Undercrypt")
        full = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", full.status, full.material_residuals)
        face = full.faces[0]
        front, = [node for node in face.nodes if node.event == "resolve"]
        self.assertIn(DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY, front.capability_dependencies)
        self.assertEqual(front.text, face.oracle_text[front.span.start:front.span.end])
        registry_value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in registry_value["capabilities"] if row["id"] == DISTINCT_GRAVEYARD_MANA_VALUE_CAPABILITY).update(
            status="blocked", blockers=["Distinct-value authority withdrawn"]
        )
        registry = CapabilityRegistry(registry_value)
        evidence = build_index(registry_value=registry_value,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        closed = compile_oracle_card(source, capability_registry=registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", closed.status)
        self.assertEqual("exact", compile_oracle_card(
            self.db.lookup(EXILE), capability_registry=registry, capability_profile=PROFILE,
        ).status)

    def test_distinct_graveyard_target_uniqueness_mutation_is_killed(self):
        with patch("quorune.selection.target_validation._validate_distinct_mana_values", return_value=None):
            with self.assertRaisesRegex(AssertionError, "Unauthorized target set accepted"):
                self.test_distinct_graveyard_mana_values_bind_each_target_and_replay()

    def test_agadeem_default_back_face_pays_life_and_produces_black_in_replay(self):
        for pay_life in (False, True):
            with self.subTest(pay_life=pay_life):
                session = self.session(601_322)
                source = self.add(session.engine, "Agadeem's Awakening // Agadeem, the Undercrypt", zone="hand")
                session.state.config.semantic_policy = "trusted_only"
                self.prepare(session, mana={"B": 3})
                self.checkpoint(session)
                life = session.state.players["A"].life
                action_ids = {action["id"] for action in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]}
                self.assertIn(f"cast:{source.ref}", action_ids)
                self.assertIn(f"play-land:{source.ref}", action_ids)
                result = session.act("pilot:A", {"action_id": f"play-land:{source.ref}", "pay_life": pay_life})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual("Agadeem, the Undercrypt", source.active_face)
                self.assertEqual(not pay_life, source.tapped)
                self.assertEqual(life - 3 * pay_life, session.state.players["A"].life)
                if pay_life:
                    mana = session.state.players["A"].mana_pool["B"]
                    result = session.act("pilot:A", {"action_id": f"activate:{source.ref}:ab2", "pay": "auto"})
                    self.assertTrue(result.ok, result.summary)
                    self.assertTrue(source.tapped)
                    self.assertEqual(mana + 1, session.state.players["A"].mana_pool["B"])
                self.replay(session)

    def test_distinct_graveyard_mana_value_binding_rejects_open_shape(self):
        from quorune.rules.announced_mana_value_target import (
            ANNOUNCED_MANA_VALUE_LIMIT, bind_announced_mana_value_limit, distinct_own_graveyard_mana_value_schema,
        )

        declared = distinct_own_graveyard_mana_value_schema()
        for x in (0, 3, 20):
            bound = bind_announced_mana_value_limit(declared, x)
            self.assertEqual((x, x + 1), (bound["mana_value_max"], bound["up_to"]))
            self.assertTrue(bound["distinct_mana_values"])
        from types import SimpleNamespace
        from quorune.selection.target_validation import target_mana_value_matches_snapshot
        from quorune.targets import target_plan

        group, = target_plan(bind_announced_mana_value_limit(declared, 3)).groups
        current = {"zone": "graveyard", "category": "card", "mana_value": 3.0,
                   "object_id": "graveyard-card", "zone_change_counter": 2}
        host = SimpleNamespace(_target_snapshot=lambda _: current)
        self.assertTrue(target_mana_value_matches_snapshot(host, group, "test", current))
        self.assertFalse(target_mana_value_matches_snapshot(host, group, "test", {**current, "mana_value": 2}))
        self.assertFalse(target_mana_value_matches_snapshot(host, group, "test", {**current, "object_id": None}))
        for bad in (None, True, -1, 3.0):
            with self.assertRaises(ValueError):
                bind_announced_mana_value_limit(declared, bad)
        for malformed in (
            {**declared, "owner_relation": "any"},
            {**declared, "count": 1},
            {**declared, "mana_value_max": 3},
            {**declared, ANNOUNCED_MANA_VALUE_LIMIT: {**declared[ANNOUNCED_MANA_VALUE_LIMIT], "schema_version": 4.0}},
        ):
            with self.assertRaises(ValueError):
                bind_announced_mana_value_limit(malformed, 3)

    def test_mana_value_quote_and_private_credit_enforce_the_same_announced_x(self):
        session = self.session(601_291)
        spell = self.add(session.engine, EXILE, zone="hand")
        resource = self.add(session.engine, "Vault Ledger", zone="hand", ref="private-vault-ledger")
        free = self.add(session.engine, "Pebble Vault", seat="B", ref="free-vault")
        mid = self.add(session.engine, "Copper Vault", seat="B", ref="copper-vault")
        high = self.add(session.engine, "Steel Vault", seat="B", ref="steel-vault")
        action = self.cast_offer(session, spell, {"W": 1})
        option = action["cost_options"][0]
        self.assertEqual(2, option["choice_schema"]["x"]["maximum"])
        self.assertEqual({free.ref: {"minimum": 0, "maximum": 2},
                          mid.ref: {"minimum": 2, "maximum": 2}},
                         option["target_schema"]["x_bounds_by_ref"])
        self.assertNotIn(high.ref, option["target_schema"]["legal_refs"])
        for seat in "BCD":
            self.assertNotIn(resource.ref, json.dumps(session.packet("pilot:" + seat, full=True)))
        command = {"action_id": action["id"], "targets": [mid.ref], "x": 1,
                   "exile_cards": [resource.ref], "pay": "manual", "payment": {"W": 1}}
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {**command, "x": 2})
        self.assertTrue(accepted.ok, accepted.summary)
        item = session.state.stack[-1]
        self.assertEqual(2, item.x_value)
        self.assertEqual(2, item.context["target_schema_override"]["mana_value_max"])
        self.assertEqual("exile", session.state.cards[resource.object_id].zone)

        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("exile", session.state.cards[mid.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[high.object_id].zone)
        self.replay(session, load=True)

    def test_target_tax_quotes_each_x_range_and_allows_a_zero_value_creature(self):
        session = self.session(601_292)
        spell = self.add(session.engine, CONTROL, zone="hand")
        zero = self.add(session.engine, "Surcharge Spark", seat="B", ref="taxed-zero")
        high = self.add(session.engine, "Surcharge Guard", seat="C", ref="taxed-two")
        ordinary = self.add(session.engine, "River Sentry", seat="D", ref="untaxed-three")
        action = self.cast_offer(session, spell, {"U": 1, "C": 3})
        target = action["target_schema"]
        self.assertEqual({zero.ref: {"minimum": 0, "maximum": 1},
                          ordinary.ref: {"minimum": 3, "maximum": 3}}, target["x_bounds_by_ref"])
        self.assertNotIn(high.ref, target["legal_refs"])
        before = authoritative_state_hash(session.state)
        denied = session.act("pilot:A", {"action_id": action["id"], "targets": [high.ref], "x": 2, "pay": "auto"})
        self.assertFalse(denied.ok, denied.summary)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", {"action_id": action["id"], "targets": [zero.ref], "x": 0,
                                            "pay": "manual", "payment": {"U": 1, "C": 2}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, session.state.stack[-1].context["target_schema_override"]["mana_value_max"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[zero.object_id].controller)
        self.assertEqual("C", session.state.cards[high.object_id].controller)
        self.replay(session, load=True)


    def test_copied_mana_value_limit_retains_announced_x_when_retargeted(self):
        session = self.session(601_293)
        source = self.add(session.engine, CONTROL, zone="hand")
        echo = self.add(session.engine, "Reverberate", zone="hand")
        first = self.add(session.engine, "Surcharge Spark", seat="B", ref="copy-first")
        second = self.add(session.engine, "Surcharge Guard", seat="C", ref="copy-second")
        high = self.add(session.engine, "River Sentry", seat="D", ref="copy-over-limit")
        action = self.cast_offer(session, source, {"U": 1, "C": 4, "R": 2})
        sent = session.act("pilot:A", {"action_id": action["id"], "targets": [first.ref], "x": 2,
                                        "pay": "manual", "payment": {"U": 1, "C": 4}})
        self.assertTrue(sent.ok, sent.summary)
        original = session.state.stack[-1]
        self.cast(session, "A", echo, [original.ref], {"R": 2})
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "targets": [high.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        chosen = session.act("pilot:A", {"action_id": "choose", "targets": [second.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        clone, = (item for item in session.state.stack if item.kind == "spell_copy")
        self.assertEqual(2, clone.x_value)
        self.assertEqual(2, clone.context["target_schema_override"]["mana_value_max"])
        self.assertEqual([second.ref], clone.targets)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[first.object_id].controller)
        self.assertEqual("A", session.state.cards[second.object_id].controller)
        self.assertEqual("D", session.state.cards[high.object_id].controller)
        self.replay(session, load=True)

    def test_live_mana_value_is_rechecked_when_the_same_permanent_turns_face_up(self):
        session = self.session(601_296)
        source = self.add(session.engine, CONTROL, zone="hand")
        folded = self.add(session.engine, "Folded Sentry", seat="B", zone="hand")
        self.prepare(session, actor="B", active="B", mana={"C": 4})
        session.state.players["A"].mana_pool["U"] = 1
        self.checkpoint(session)
        cast = session.act("pilot:B", {"action_id": "cast-morph:" + folded.ref, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(session))
        self.priority(session, "A")
        folded = session.state.cards[folded.object_id]
        identity = folded.logical_object_id
        self.assertTrue(folded.face_down)
        self.assertEqual(0, session.engine._target_snapshot(folded.ref)["mana_value"])
        response = {"action_id": "cast:" + source.ref, "targets": [folded.ref], "x": 0, "pay": "auto"}
        accepted = session.act("pilot:A", response)
        self.assertTrue(accepted.ok, accepted.summary)
        self.priority(session, "B")
        revealed = session.act("pilot:B", {"action_id": "turn-face-up:" + folded.ref, "pay": "auto"})
        self.assertTrue(revealed.ok, revealed.summary)
        current = session.state.cards[folded.object_id]
        self.assertEqual(identity, current.logical_object_id)
        self.assertEqual(5, session.engine._target_snapshot(current.ref)["mana_value"])
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("B", session.state.cards[folded.object_id].controller)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_splice_quote_retains_the_bound_group_and_includes_its_mana_price(self):
        session = self.session(601_297)
        source = self.add(session.engine, CONTROL, zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        zero = self.add(session.engine, "Surcharge Spark", seat="B", ref="splice-taxed")
        low = self.add(session.engine, "Llanowar Elves", seat="B", ref="splice-low")
        high = self.add(session.engine, "River Sentry", seat="C", ref="splice-high")
        self.prepare(session, mana={"U": 2, "C": 4})
        self.checkpoint(session)
        self.start_splice(session, source)
        self.add_splice(session, dream)
        action = self.splice_actions(session)["cast"]
        option, = action["cost_options"]
        self.assertEqual(2, option["choice_schema"]["x"]["maximum"])
        target_schema = option["target_schema"]
        self.assertEqual({"minimum": 0, "maximum": 0}, target_schema["x_bounds_by_ref"][zero.ref])
        self.assertEqual({"minimum": 1, "maximum": 2}, target_schema["x_bounds_by_ref"][low.ref])
        self.assertNotIn(high.ref, target_schema["legal_refs"])
        self.assertEqual("target_0", target_schema["groups"][0]["id"])
        hand_before = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": "cast", "targets": [low.ref], "x": 2, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertIsNone(self.resolve(session))
        self.assertEqual("A", session.state.cards[low.object_id].controller)
        self.assertEqual(hand_before, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)

    def test_mana_value_limit_requires_printed_x_and_its_exact_target_authority(self):
        source = self.db.lookup(CONTROL)
        for name in (EXILE, CONTROL):
            binding, _ = register_card(self.db, self.registry, name)
            self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        cases = (replace(source, mana_cost="{U}"),
                 replace(source, oracle_text=source.oracle_text.replace("X or less", "less than X")),
                 replace(source, oracle_text=source.oracle_text + " Then draw a card."))
        for record in cases:
            result = compile_oracle_card(record, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"] if row["id"] == ANNOUNCED_MANA_VALUE_TARGET_CAPABILITY)
        capability.update(status="blocked", blockers=["Announced target authority withdrawn."])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations, discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        result = compile_oracle_card(source, capability_registry=registry, capability_profile=PROFILE)
        self.assertNotEqual("exact", result.status)
        with patch("quorune.compiler.announced_mana_value_target.announced_mana_value_target_template", return_value=None):
            result = compile_oracle_card(source, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", result.status)


    def legal_activation(self, game, source):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))

    def unchanged_rejection(self, game, actor, command):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:" + actor, command)
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def test_land_return_activation_binds_zero_and_overpaid_x_and_draws_only_once(self):
        game = self.session(60210704)
        source = self.add(game.engine, LAND_RETURN_ARCHITECT)
        borrowed = self.add(game.engine, "Plains", seat="B", ref="return-borrowed-land")
        borrowed.controller = "A"
        game.state.players["B"].zones["battlefield"].remove(borrowed.object_id)
        game.state.players["A"].zones["battlefield"].append(borrowed.object_id)
        owned = self.add(game.engine, "Plains", ref="return-owned-land")
        foreign = self.add(game.engine, "Plains", seat="B", ref="return-uncontrolled-land")
        borrowed.tapped = owned.tapped = foreign.tapped = True
        zero = self.add(game.engine, "Ornithopter", zone="graveyard")
        low = self.add(game.engine, "Sol Ring", zone="graveyard", ref="return-owned-artifact")
        enchantment = self.add(game.engine, "Seal of Cleansing", zone="graveyard")
        opponent = self.add(game.engine, "Sol Ring", seat="C", zone="graveyard", ref="return-foreign-artifact")
        other = self.add(game.engine, "Grizzly Bears", zone="graveyard")
        self.prepare(game, mana={"W": 2, "C": 3})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)

        offer = self.legal_activation(game, source)
        bounds = offer["target_schema"]["x_bounds_by_ref"]
        self.assertEqual({zero.ref: {"minimum": 0, "maximum": 4},
                          low.ref: {"minimum": 1, "maximum": 4},
                          enchantment.ref: {"minimum": 2, "maximum": 4}}, bounds)
        self.assertNotIn(opponent.ref, bounds)
        self.assertNotIn(other.ref, bounds)
        command = {"action_id": offer["id"], "targets": [zero.ref], "x": 0,
                   "cost_cards": [borrowed.ref], "pay": "auto"}
        self.unchanged_rejection(game, "B", command)
        self.unchanged_rejection(game, "A", {**command, "cost_cards": [foreign.ref]})
        self.unchanged_rejection(game, "A", {**command, "targets": [enchantment.ref]})
        self.unchanged_rejection(game, "A", {**command, "x": 5})
        top = game.state.players["A"].zones["library"][-1]
        before_hand = set(game.state.players["A"].zones["hand"])
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)
        self.assertIn(borrowed.object_id, game.state.players["B"].zones["hand"])
        activated, draw = game.state.stack
        self.assertEqual(0, activated.x_value)
        self.assertEqual(0, activated.context["target_schema_override"]["mana_value_max"])
        self.assertEqual(("triggered_ability", source.object_id), (draw.kind, draw.source_object_id))
        self.unchanged_rejection(game, "A", command)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({top}, set(game.state.players["A"].zones["hand"]) - before_hand)
        self.assertEqual("battlefield", game.state.cards[zero.object_id].zone)
        private_ref = game.state.cards[top].ref
        for viewer in ("pilot:B", "pilot:C", "pilot:D", "spectator"):
            self.assertNotIn(private_ref, json.dumps(game.packet(viewer, full=True)))

        second = self.legal_activation(game, source)
        self.assertEqual({"minimum": 2, "maximum": 3}, second["target_schema"]["x_bounds_by_ref"][enchantment.ref])
        result = game.act("pilot:A", {"action_id": second["id"], "targets": [enchantment.ref],
                                     "cost_cards": [owned.ref], "x": 3, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        activated, = game.state.stack
        self.assertEqual(3, activated.x_value)
        self.assertIsNone(self.resolve(game))
        self.assertEqual({"A"}, {game.state.cards[card.object_id].controller for card in (zero, enchantment)})
        self.assertEqual("battlefield", game.state.cards[enchantment.object_id].zone)
        self.assertEqual({top, owned.object_id}, set(game.state.players["A"].zones["hand"]) - before_hand)
        self.replay(game, load=True)

    def test_hand_return_trigger_ignores_other_zones_and_keeps_the_simultaneous_departure_source(self):
        game = self.session(60210705)
        source = self.add(game.engine, LAND_RETURN_ARCHITECT)
        targets = [self.add(game.engine, "Sol Ring", ref=f"mass-return-artifact-{i}") for i in range(2)]
        creature = self.add(game.engine, "Grizzly Bears")
        regrowth = self.add(game.engine, "Regrowth", seat="B", zone="hand")
        from_graveyard = self.add(game.engine, "Sol Ring", seat="B", zone="graveyard", ref="return-from-graveyard")
        normal = self.add(game.engine, "Cyclonic Rift", seat="B", zone="hand", ref="return-only-creature")
        overload = self.add(game.engine, "Cyclonic Rift", seat="B", zone="hand", ref="return-multiple-permanents")
        self.prepare(game, actor="B", active="B", mana={"C": 8, "U": 2, "G": 1})
        game.state.config.semantic_policy = "trusted_only"
        self.checkpoint(game)
        library = tuple(game.state.players["A"].zones["library"])

        for spell, target in ((regrowth, from_graveyard), (normal, creature)):
            self.priority(game, "B")
            result = game.act("pilot:B", {"action_id": "cast:" + spell.ref, "targets": [target.ref], "pay": "auto"})
            self.assertTrue(result.ok, result.summary)
            self.assertIsNone(self.resolve(game))
            self.assertEqual("hand", game.state.cards[target.object_id].zone)
            self.assertEqual(library, tuple(game.state.players["A"].zones["library"]))

        self.priority(game, "B")
        result = game.act("pilot:B", {"action_id": "cast:" + overload.ref, "cost_option": "overload",
                                     "targets": [], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "hand" for card in (source, *targets)))
        self.assertEqual(library[:-1], tuple(game.state.players["A"].zones["library"]))
        private_ref = game.state.cards[library[-1]].ref
        for seat in "BCD":
            self.assertNotIn(private_ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_land_return_activation_and_departure_limit_require_their_closed_authorities(self):
        card = self.db.lookup(LAND_RETURN_ARCHITECT, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, LAND_RETURN_ARCHITECT)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        activated, = (program for program in programs if program.event == "activate"
                      and ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY in program.capability_dependencies)
        trigger, = (program for program in programs if program.event == "permanent.leave")
        self.assertEqual(2, trigger.trigger_limit["schema_version"])
        self.assertEqual(1, activated.cost_schema["announced_x_symbols"])
        cap = ACTIVATION_GRAVEYARD_MANA_VALUE_CAPABILITY
        dependencies = tuple(value for value in activated.capability_dependencies if value != cap)
        altered = replace(activated, capability_dependencies=dependencies,
                          capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(altered, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(binding["strict"])
        self.assertIn("capability:undeclared_runtime_dependency:" + cap, binding["blockers"])
        dependencies = tuple(value for value in trigger.capability_dependencies
                             if value != "trigger.usage.departure_incarnation")
        with self.assertRaisesRegex(ValueError, "Trigger limits require their usage capability dependency"):
            replace(trigger, capability_dependencies=dependencies,
                    capability_closure=self.registry.closure(dependencies, profile=PROFILE).to_dict())
        for before, after in (("land you control", "creature you control"), ("your graveyard", "a graveyard"),
                              ("X or less", "equal to X"), ("Activate only as a sorcery.", ""),
                              ("noncreature permanents", "noncreature cards")):
            changed = replace(card, oracle_text=card.oracle_text.replace(before, after))
            ir = compile_oracle_card(changed, capability_registry=self.registry, capability_profile=PROFILE)
            self.assertNotEqual("exact", ir.status)
