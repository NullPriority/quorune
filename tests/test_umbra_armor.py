from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from common import ROOT
from quorune.carddb import CardDatabase
from quorune.oracle_ir import compile_oracle_card
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.card_programs import bind_card_program_runtime
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from quorune.deck import DeckDefinition, DeckEntry
import test_bound_effect_programs as witnesses

from quorune.ability_fragments import ability_fragment_from_dict, ability_fragment_to_dict, canonical_ability_fragments
from quorune.model import CardInstance
from quorune.semantic_runtime.ability_fragments import UmbraArmorFragmentHandler
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.umbra_armor_model import UmbraArmorProtection, UmbraArmorSpec, UMBRA_ARMOR_HANDLER
from quorune.umbra_armor import current_umbra_armor_protections
from quorune.destruction_replacement_options import DestructionReplacementSubject, destruction_replacement_options
from quorune.replacement.ordering import advance_replacement_batch
from quorune.replacement.model import ReplacementEffectError
from quorune.destruction_replacement_planning import resolve_destruction_replacements
from quorune.replacement.ordering import ReplacementChoiceRequired
from quorune.destruction import prepare_destructions, commit_destruction_plan, request_for_card, DestructionCause, DestructionError
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry
from quorune.replacement.replay import ReplacementContinuation
from quorune.attachments import attach_objects


class UmbraArmorRepresentationTests(unittest.TestCase):
    def test_full_umbra_dependency_gate_and_malformed_destruction_identity_fail_closed(self):
        from quorune.semantic_choices.destruction_intent_identity import validate_destruction_intent_identity
        from quorune.semantic_choices.model import SemanticChoiceError
        identity = {"actor": "A", "reason": "test", "object_ref": "C", "regeneration_prohibited": False}
        self.assertEqual(identity, validate_destruction_intent_identity("destroy_permanent", identity))
        for mutation in ({"extra": 0}, {"regeneration_prohibited": 1}, {"object_ref": None}):
            with self.assertRaises(SemanticChoiceError):
                validate_destruction_intent_identity("destroy_permanent", {**identity, **mutation})
        import json
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "umbra.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/umbra-armor-cards.json"], path)
            with CardDatabase(path) as db:
                value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
                for row in value["capabilities"]:
                    if row["id"] == "permanent.destroy.umbra_armor":
                        row.update(status="blocked", blockers=["closed destruction owner unavailable"])
                ir = compile_oracle_card(db.lookup("Hyena Umbra"), capability_registry=CapabilityRegistry(value))
                self.assertNotEqual("exact", ir.status)
    def test_recursive_aura_cycle_cannot_reapply_the_same_replacement(self):
        first = DestructionReplacementSubject("first", "A1", "first@0", "A", "A")
        second = DestructionReplacementSubject("second", "A2", "second@0", "A", "A")
        protections = (
            UmbraArmorProtection("first", "A1", "first@0", "A", "second", "second@0", "A", 0),
            UmbraArmorProtection("second", "A2", "second@0", "A", "first", "first@0", "A", 0),
        )
        result = resolve_destruction_replacements((first, second), protections, ("first",),
            batch_id="cycle", apnap_order="ABCD", cause="effect")
        self.assertEqual({"first": "destroy", "second": "umbra_armor"}, dict(result.dispositions))
        self.assertEqual(("first", "second"), result.damage_clear_object_ids)
    def test_coupled_destruction_expands_chosen_aura_and_deduplicates_simultaneous_request(self):
        body = DestructionReplacementSubject("body", "C", "body@0", "A", "A")
        aura = DestructionReplacementSubject("aura", "U", "aura@0", "B", "B")
        protection = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@0", "A", 0)
        for requests in (("body",), ("body", "aura")):
            result = resolve_destruction_replacements((body, aura), (protection,), requests,
                batch_id="coupled", apnap_order="ABCD", cause="effect")
            self.assertEqual({"aura": "destroy", "body": "umbra_armor"}, dict(result.dispositions))
            self.assertEqual(("body",), result.damage_clear_object_ids)
            self.assertEqual(2, len(result.batch.events))
        result = resolve_destruction_replacements((body, replace(aura, indestructible=True)), (protection,), ("body",),
            batch_id="indestructible-aura", apnap_order="ABCD", cause="effect")
        self.assertEqual({"aura": "indestructible", "body": "umbra_armor"}, dict(result.dispositions))

    def test_recursive_aura_protection_uses_each_affected_controller_and_preserves_decisions(self):
        body = DestructionReplacementSubject("body", "C", "body@0", "A", "A")
        aura = DestructionReplacementSubject("aura", "U", "aura@0", "B", "B", shield_counters=1, regeneration_shields=1)
        protection = UmbraArmorProtection("aura", "U", "aura@0", "C", "body", "body@0", "A", 0)
        with self.assertRaises(ReplacementChoiceRequired) as raised:
            resolve_destruction_replacements((body, aura), (protection,), ("body",),
                batch_id="recursive", apnap_order="ABCD", cause="effect")
        self.assertEqual("B", raised.exception.pending.choice.chooser)
        self.assertEqual(2, len(raised.exception.pending.choice.options))
        result = resolve_destruction_replacements((body, aura), (protection,), ("body",),
            batch_id="recursive", apnap_order="ABCD", cause="effect", selections=("aura@0:shield",))
        self.assertEqual({"aura": "shield_counter", "body": "umbra_armor"}, dict(result.dispositions))
        self.assertEqual(1, result.consumed_selections)

    def test_competing_protections_use_recipient_controller_and_shared_choice_authority(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A", shield_counters=1, regeneration_shields=1)
        first = UmbraArmorProtection("aura1", "U1", "aura1@0", "B", "body", "body@0", "A", 0)
        second = replace(first, aura_object_id="aura2", aura_ref="U2", aura_logical_object_id="aura2@0", aura_controller="C")
        options = destruction_replacement_options((subject,), (first, second), batch_id="choice", apnap_order="ABCD", cause="effect")
        progress = advance_replacement_batch(options.batch, options.effects)
        self.assertEqual("A", progress.pending.choice.chooser)
        self.assertEqual(4, len(progress.pending.choice.options))
        selected = next(effect.effect_id for effect in options.effects if effect.source_id == "aura2")
        resolved = advance_replacement_batch(options.batch, options.effects, selections=(selected,))
        self.assertIsNone(resolved.pending)
        event = resolved.batch.events[0]
        self.assertEqual("umbra_armor", event.payload["disposition"])
        self.assertTrue(event.payload["clear_damage"])
        self.assertEqual("aura2", event.payload["umbra_aura_object_id"])
        self.assertEqual("destroy", options.batch.events[0].payload["disposition"])
        with self.assertRaises(ReplacementEffectError):
            advance_replacement_batch(options.batch, options.effects, selections=("invented-choice",))

    def test_indestructible_and_forbidden_regeneration_do_not_consume_unrelated_protection(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A", indestructible=True, shield_counters=1, regeneration_shields=1)
        aura = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@0", "A", 0)
        options = destruction_replacement_options((subject,), (aura,), batch_id="indestructible", apnap_order="ABCD", cause="effect")
        resolved = advance_replacement_batch(options.batch, options.effects)
        self.assertIsNone(resolved.pending)
        self.assertEqual("indestructible", resolved.batch.events[0].payload["disposition"])
        self.assertFalse(resolved.batch.events[0].payload["clear_damage"])
        self.assertFalse(resolved.batch.journal)
        options = destruction_replacement_options((replace(subject, indestructible=False),), (aura,), batch_id="forbidden", apnap_order="ABCD", cause="effect", regeneration_prohibited=True)
        self.assertEqual(2, len(advance_replacement_batch(options.batch, options.effects).pending.choice.options))
        options = destruction_replacement_options((replace(subject, indestructible=False),), (aura,), batch_id="lethal", apnap_order="ABCD", cause="state_based_action")
        self.assertEqual(2, len(advance_replacement_batch(options.batch, options.effects).pending.choice.options))

    def test_destruction_choice_snapshot_rejects_invalid_resources_and_stale_recipient(self):
        subject = DestructionReplacementSubject("body", "C", "body@0", "A", "A")
        for mutation in ({"shield_counters": True}, {"regeneration_shields": -1}, {"indestructible": 1}):
            with self.assertRaises(ValueError):
                replace(subject, **mutation)
        aura = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@1", "A", 0)
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject,), (aura,), batch_id="stale", apnap_order="ABCD", cause="effect")
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject, subject), (), batch_id="duplicate", apnap_order="ABCD", cause="effect")
        with self.assertRaises(ValueError):
            destruction_replacement_options((subject,), (), batch_id="wrong", apnap_order="ABCD", cause="state_based_action", regeneration_prohibited=True)

    def test_current_source_fragment_projection_mutant_is_killed(self):
        with patch("quorune.umbra_armor.canonical_ability_fragments", return_value=()):
            with self.assertRaises(AssertionError):
                self.test_current_aura_relationship_keeps_source_and_recipient_ability_loss_distinct()

    def test_recognized_fragment_requires_closed_destruction_gameplay(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "umbra.sqlite3"
            build_fixture_database([ROOT / "tests/fixtures/umbra-armor-cards.json"], path)
            with CardDatabase(path) as db:
                registry = load_default_capability_registry()
                import json
                value = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
                for row in value["capabilities"]:
                    if row["id"] == "permanent.destroy.umbra_armor":
                        row.update(status="blocked", blockers=["full owner unavailable"])
                registry = CapabilityRegistry(value)
                record = db.lookup("Hyena Umbra")
                ir = compile_oracle_card(record, capability_registry=registry)
                node = next(n for n in ir.faces[0].nodes if n.template_id == "umbra-armor-source-fragment-v1")
                self.assertTrue(node.lowerable)
                self.assertFalse(node.exact)
                self.assertTrue(node.residual_ids)
                program = compile_best_available_card_program(db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                self.assertFalse(bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"])

    def test_source_rule_codec_preserves_multiplicity_and_rejects_unknown_fields(self):
        rule = UmbraArmorSpec()
        self.assertEqual(rule, ability_fragment_from_dict(ability_fragment_to_dict(rule)))
        self.assertEqual((rule, rule), canonical_ability_fragments((rule, rule)))
        with self.assertRaises(FrozenInstanceError):
            rule.schema_version = 2
        for value in ({"schema_version": True}, {"schema_version": 2}, {"schema_version": 1, "recipient": "all"}, {}):
            with self.assertRaises(ValueError):
                UmbraArmorSpec.from_dict(value)
        value = UmbraArmorProtection("aura", "U", "aura@0", "B", "body", "body@0", "A", 0)
        with self.assertRaises(ValueError):
            replace(value, recipient_object_id="aura")
        with self.assertRaises(ValueError):
            replace(value, instance=True)

    def test_registered_fragment_handler_has_closed_descriptor_and_no_mutation(self):
        rule = UmbraArmorSpec()
        descriptor = {"handler_id": UMBRA_ARMOR_HANDLER, "schema_version": 1,
                      "event": "characteristics.evaluate", "fragment": ability_fragment_to_dict(rule)}
        handler = UmbraArmorFragmentHandler()
        self.assertEqual((rule,), handler.lower(descriptor, None))
        for mutation in ({"extra": 0}, {"schema_version": 2}, {"event": "zone.change"},
                         {"fragment": ability_fragment_to_dict(UmbraArmorSpec()) | {"extra": 0}}):
            with self.assertRaises((ValueError, SemanticNodeError)):
                handler.validate({**descriptor, **mutation})

    def test_current_aura_relationship_keeps_source_and_recipient_ability_loss_distinct(self):
        recipient = CardInstance(object_id="body", ref="C", oracle_id="body", printed_name="Body",
                                 owner="A", controller="A", zone="battlefield")
        aura = CardInstance(object_id="aura", ref="U", oracle_id="aura", printed_name="Aura",
                            owner="B", controller="B", zone="battlefield", attached_to="body")
        rows = {"aura": {"type_line": "Enchantment — Aura", "ability_fragments": (UmbraArmorSpec(),)},
                "body": {"type_line": "Creature", "ability_fragments": ()}}
        host = SimpleNamespace(state=SimpleNamespace(cards={"body": recipient, "aura": aura}),
                               _effective_card_data=lambda card: rows[card.object_id])
        protections = current_umbra_armor_protections(host)
        self.assertEqual(1, len(protections))
        self.assertEqual(("B", "A"), (protections[0].aura_controller, protections[0].recipient_controller))
        rows["body"]["ability_fragments"] = ()
        self.assertEqual(protections, current_umbra_armor_protections(host))
        rows["aura"]["ability_fragments"] = ()
        self.assertFalse(current_umbra_armor_protections(host))
        rows["aura"]["ability_fragments"] = (UmbraArmorSpec(), UmbraArmorSpec())
        self.assertEqual(2, len(current_umbra_armor_protections(host)))
        for field, value in (("phased_out", True), ("zone", "graveyard"), ("attached_to", None)):
            previous = getattr(aura, field)
            setattr(aura, field, value)
            self.assertFalse(current_umbra_armor_protections(host))
            setattr(aura, field, previous)
        self.assertEqual(protections[0].aura_logical_object_id, aura.logical_object_id)


class UmbraArmorRepresentationReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "umbra.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json", ROOT / "tests/fixtures/umbra-armor-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Umbra representation", [DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    replay = witnesses.BoundEffectProgramRuntimeTests.replay
    def ready(self, session, source, mana):
        program = compile_best_available_card_program(self.db, self.db.lookup(source.printed_name),
            semantic_registry=SemanticRegistry(), capability_registry=self.registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        for ability in program.abilities:
            session.engine.semantics.put(ability)
        return witnesses.BoundEffectProgramRuntimeTests.ready(self, session, source, mana)
    resolve = witnesses.BoundEffectProgramRuntimeTests.resolve

    def test_actual_hyena_cast_attaches_and_protects_without_manual_rule_grant(self):
        session = self.session(295008)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", zone="hand")
        action = self.ready(session, aura, {"W": 1})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [body.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual(body.object_id, aura.attached_to)
        self.assertTrue(current_umbra_armor_protections(engine))
        self.assertFalse(aura.annotations.get("granted_ability_fragments"))
        self.replay(session)
        spell = self.add(engine, "Murder", zone="hand")
        action = self.ready(session, spell, {"B": 3})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [body.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertEqual("battlefield", body.zone)
        self.assertEqual("graveyard", aura.zone)
        self.assertFalse(body.tapped)
        self.replay(session)

    def test_offered_destroy_spell_suspends_for_recipient_choice_and_replays(self):
        session = self.session(295005)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        first = self.add(engine, "Hyena Umbra", seat="B")
        second = self.add(engine, "Spider Umbra", seat="C")
        for aura in (first, second):
            attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        spell = self.add(engine, "Murder", zone="hand")
        action = self.ready(session, spell, {"B": 3})
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "targets": [body.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        pending = self.resolve(session)
        self.assertEqual("replacement.order", pending.kind)
        self.assertEqual("battlefield", body.zone)
        self.assertTrue(all(aura.zone == "battlefield" for aura in (first, second)))
        for seat in "BCD":
            self.assertIsNone(session.packet("pilot:" + seat, full=True)["decision"])
        resumed = self.replay(session, load=True)
        packet = resumed.packet("pilot:A", full=True)["decision"]
        options = packet["ctx"]["options"]
        chosen = next(option["id"] for option in options if option["source"] == second.object_id)
        before = authoritative_state_hash(resumed.state)
        wrong = resumed.act("pilot:B", {"action": "choose", "replacement": chosen})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(resumed.state))
        result = resumed.act("pilot:A", {"action": "choose", "replacement": chosen})
        self.assertTrue(result.ok, result.summary)
        self.resolve(resumed)
        self.assertEqual("battlefield", resumed.state.cards[body.object_id].zone)
        self.assertEqual("graveyard", resumed.state.cards[second.object_id].zone)
        self.assertEqual("battlefield", resumed.state.cards[first.object_id].zone)
        self.replay(resumed)

    def test_lethal_state_based_destruction_choice_preserves_damage_until_selected_and_replays(self):
        session = self.session(295006)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        first = self.add(engine, "Hyena Umbra", seat="B")
        second = self.add(engine, "Spider Umbra", seat="C")
        for aura in (first, second):
            attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        body.marked_damage = 100
        self.assertTrue(engine._stabilize())
        self.assertEqual("replacement.order", engine.state.pending_decision.kind)
        self.assertEqual(100, body.marked_damage)
        self.assertEqual("battlefield", body.zone)
        self.checkpoint(session)
        resumed = self.replay(session, load=True)
        packet = resumed.packet("pilot:A", full=True)["decision"]
        chosen = next(option["id"] for option in packet["ctx"]["options"] if option["source"] == first.object_id)
        result = resumed.act("pilot:A", {"action": "choose", "replacement": chosen})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("battlefield", resumed.state.cards[body.object_id].zone)
        self.assertEqual(0, resumed.state.cards[body.object_id].marked_damage)
        self.assertEqual("graveyard", resumed.state.cards[first.object_id].zone)
        self.assertEqual("battlefield", resumed.state.cards[second.object_id].zone)
        self.replay(resumed)

    def test_canonical_owner_replaces_destruction_and_clears_damage_without_tapping(self):
        session = self.session(295002)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        body.marked_damage = 3
        plan = prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                                    actor="A", reason="typed owner witness", regeneration_prohibited=True)
        self.assertEqual((aura.object_id,), plan.destroyed_object_ids)
        commit_destruction_plan(engine, plan)
        self.assertEqual("battlefield", body.zone)
        self.assertEqual("graveyard", aura.zone)
        self.assertEqual(0, body.marked_damage)
        self.assertFalse(body.tapped)

    def test_canonical_owner_simultaneous_aura_and_recipient_destroy_only_aura_once(self):
        session = self.session(295003)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        plan = prepare_destructions(engine, tuple(request_for_card(card) for card in (body, aura)),
                                    cause=DestructionCause.EFFECT, actor="A", reason="simultaneous witness")
        self.assertEqual((aura.object_id,), plan.destroyed_object_ids)
        commit_destruction_plan(engine, plan)
        self.assertEqual("battlefield", body.zone)
        self.assertEqual("graveyard", aura.zone)

    def test_canonical_owner_stale_attachment_rejects_without_mutation(self):
        session = self.session(295004)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        other = self.add(engine, "Generic Bound Body", ref="other")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        plan = prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                                    actor="A", reason="stale witness")
        attach_objects(engine.state.cards, aura, other, source_timestamp=engine._next_zone_timestamp())
        before = authoritative_state_hash(session.state)
        with self.assertRaises(DestructionError):
            commit_destruction_plan(engine, plan)
        self.assertEqual(before, authoritative_state_hash(session.state))

    def test_stale_aura_ability_and_borrowed_choice_state_reject_without_mutation(self):
        session = self.session(295007)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        plan = prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                                    actor="A", reason="ability stale")
        original = engine._effective_card_data
        def remove_aura_abilities(card, **kwargs):
            data = original(card, **kwargs)
            if (card if isinstance(card, str) else card.object_id) == aura.object_id:
                data = {**data, "ability_fragments": ()}
            return data
        engine._effective_card_data = remove_aura_abilities
        before = authoritative_state_hash(session.state)
        with self.assertRaises(DestructionError):
            commit_destruction_plan(engine, plan)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with self.assertRaises(ValueError):
            replace(plan, replacement_subjects=("untyped",))

    def test_missing_damage_clear_mutant_is_killed(self):
        with patch("quorune.damage_results.clear_permanent_damage", return_value=False):
            with self.assertRaises(AssertionError):
                self.test_canonical_owner_replaces_destruction_and_clears_damage_without_tapping()

    def test_deathtouch_state_based_replacement_keeps_combat_and_does_not_regenerate(self):
        session = self.session(295009)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        body.marked_damage = 1
        body.deathtouch_damage = True
        body.attacking = "C"
        engine.state.combat.attackers[body.object_id] = "C"
        engine.state.combat.attack_target_context[body.object_id] = {"defender": "C", "target_kind": "player"}
        engine._stabilize()
        self.assertEqual("battlefield", body.zone)
        self.assertEqual("graveyard", aura.zone)
        self.assertEqual(0, body.marked_damage)
        self.assertFalse(body.tapped)
        self.assertEqual("C", body.attacking)
        self.assertIn(body.object_id, engine.state.combat.attackers)

    def test_stale_pending_sba_choice_rejects_atomically_and_preserves_capability(self):
        session = self.session(295011)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        for name, seat in (("Hyena Umbra", "B"), ("Spider Umbra", "C")):
            aura = self.add(engine, name, seat=seat)
            attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        body.marked_damage = 100
        self.assertTrue(engine._stabilize())
        packet = session.packet("pilot:A", full=True)["decision"]
        chosen = packet["ctx"]["options"][0]["id"]
        body.marked_damage = 99
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:A", {"action": "choose", "replacement": chosen})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.assertIsNotNone(engine.state.pending_decision)

    def test_competing_regeneration_and_shield_choice_uses_one_resource(self):
        for selected in ("regeneration", "shield"):
            with self.subTest(selected=selected):
                session = self.session(295012)
                engine = session.engine
                body = self.add(engine, "Generic Bound Body")
                body.counters["shield"] = 1
                body.regeneration_shields = 1
                body.marked_damage = 2
                with self.assertRaises(ReplacementChoiceRequired) as raised:
                    prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                                        actor="B", reason="competing resource choice")
                self.assertEqual("A", raised.exception.pending.choice.chooser)
                choice = next(effect.effect_id for effect in raised.exception.effects if effect.effect_id.endswith(":" + selected))
                plan = prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                    actor="B", reason="competing resource choice", replacement_selections=(choice,))
                commit_destruction_plan(engine, plan)
                self.assertEqual("battlefield", body.zone)
                self.assertEqual(0 if selected == "shield" else 1, body.counters.get("shield", 0))
                self.assertEqual(0 if selected == "regeneration" else 1, body.regeneration_shields)
                self.assertEqual(selected == "regeneration", body.tapped)

    def test_indestructible_aura_still_protects_and_zero_toughness_does_not_use_umbra(self):
        session = self.session(295010)
        engine = session.engine
        body = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra")
        attach_objects(engine.state.cards, aura, body, source_timestamp=engine._next_zone_timestamp())
        original = engine._effective_card_data
        def aura_indestructible(card, **kwargs):
            data = original(card, **kwargs)
            if (card if isinstance(card, str) else card.object_id) == aura.object_id:
                data = {**data, "keywords": (*data.get("keywords", ()), "Indestructible")}
            return data
        body.marked_damage = 2
        with patch.object(engine, "_effective_card_data", aura_indestructible):
            plan = prepare_destructions(engine, (request_for_card(body),), cause=DestructionCause.EFFECT,
                                        actor="A", reason="indestructible Aura")
            commit_destruction_plan(engine, plan)
        self.assertEqual("battlefield", body.zone)
        self.assertEqual("battlefield", aura.zone)
        self.assertEqual(0, body.marked_damage)
        def zero_toughness(card, **kwargs):
            data = original(card, **kwargs)
            if (card if isinstance(card, str) else card.object_id) == body.object_id:
                data = {**data, "toughness": "0"}
            return data
        with patch.object(engine, "_effective_card_data", zero_toughness):
            engine._stabilize()
        self.assertEqual("graveyard", body.zone)
        self.assertEqual("graveyard", aura.zone)
        self.assertFalse(any(event.code == "permanent.destroy.umbra" and event.details.get("reason") == "state-based action" for event in engine.state.events))

    def test_source_fragment_checkpoint_and_relationship_replay_are_exact(self):
        session = self.session(295001)
        engine = session.engine
        recipient = self.add(engine, "Generic Bound Body")
        aura = self.add(engine, "Hyena Umbra", seat="B")
        aura.attached_to = recipient.object_id
        aura.annotations["granted_ability_fragments"] = [ability_fragment_to_dict(UmbraArmorSpec())]
        before = current_umbra_armor_protections(engine)
        self.assertTrue(before)
        self.assertTrue(all(p.aura_controller == "B" and p.recipient_controller == "A" for p in before))
        self.checkpoint(session)
        resumed = self.replay(session, load=True)
        self.assertEqual(before, current_umbra_armor_protections(resumed.engine))
        self.assertEqual(aura.annotations["granted_ability_fragments"], resumed.state.cards[aura.object_id].annotations["granted_ability_fragments"])
