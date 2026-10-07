from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from quorune.carddb import CardDatabase, CardRecord
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.compiler.entry_designation_templates import entry_designation_handler, chosen_characteristics_handler
from quorune.compiler.unlock_frontier import canonical_residual_families
from quorune.deck import DeckLoader
from quorune.entry_designations import EntryDesignationKind, validate_designation
from quorune.model import CardInstance, GameState
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.replacement.immutable import FrozenMap
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.semantic_runtime.chosen_characteristics import ChosenCharacteristicsHandler
from quorune.semantic_runtime.continuous_components import ContinuousEffectSourceContext
from quorune.semantic_runtime.context import SemanticNodeError
from quorune.semantic_runtime.entry_designations import EntryDesignationHandler
from quorune.semantic_runtime.zone_replacement_model import ZoneChangeSubjectSnapshot, ZoneChangeReplacementSnapshot
from quorune.semantics import SemanticRegistry
from quorune.session import CommanderSession
from scripts.build_test_database import _card_payload, build_fixture_database


def composition_record(identity, name, type_line, text, keywords=()):
    return CardRecord(
        oracle_id=f"fixture:entry-composition:{identity}",
        name=name,
        mana_cost="{U}",
        mana_value=1,
        type_line=type_line,
        oracle_text=text,
        power=None,
        toughness=None,
        loyalty=None,
        defense=None,
        colors=("U",),
        color_identity=("U",),
        keywords=keywords,
        produced_mana=(),
        layout="normal",
        released_at="2026-01-01",
        legalities={"commander": "legal"},
        faces=(),
        raw={},
    )


class EntryDesignationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "entry.sqlite3"
        compositions = []
        for identity, name, type_line, text, keywords in (
            ("simple-aura", "Entry Choice Simple Aura", "Enchantment — Aura", "Enchant creature\nAs this Aura enters, choose a color.", ("Enchant",)),
            ("typed-aura", "Entry Choice Typed Aura", "Enchantment — Aura", "Enchant creature or Vehicle\nAs this Aura enters, choose a color.", ("Enchant",)),
            ("targeted-artifact", "Entry Choice Targeted Artifact", "Artifact", "As this artifact enters, choose a color.\n{1}: This artifact deals 1 damage to any target.", ()),
        ):
            record = composition_record(identity, name, type_line, text, keywords)
            compositions.append(_card_payload(record))
        composition_path = Path(cls.temporary.name) / "compositions.json"
        composition_path.write_text(json.dumps({"schema_version": 1, "cards": compositions, "rulings": []}), encoding="utf-8")
        build_fixture_database([ROOT / "tests/fixtures/scryfall-exact-lists.json", ROOT / "tests/fixtures/entry-designation-cards.json", composition_path], path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One", deck_name="Mishra")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina", deck_name="Zimone")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def test_entry_designation_vocabulary_and_descriptor_are_closed(self):
        for kind in EntryDesignationKind:
            for value in kind.values:
                self.assertEqual(value, validate_designation(kind, value))
        for value in (None, True, "C", "blue", "WU"):
            with self.assertRaises(ValueError):
                validate_designation(EntryDesignationKind.COLOR, value)
        for value in ("Goblins", "artifact", "invented-type", "Goblin", ""):
            with self.assertRaises(ValueError):
                validate_designation(EntryDesignationKind.CREATURE_TYPE, value)
        desc = entry_designation_handler("As this artifact enters, choose a color.", source_name="Sol Grail")[1]
        self.assertEqual(EntryDesignationKind.COLOR, EntryDesignationHandler().validate(desc))
        for change in ({"schema_version": True}, {"designation": "card_type"}, {"extra": 1}):
            with self.assertRaises(SemanticNodeError):
                EntryDesignationHandler().validate({**desc, **change})

    def test_entry_designation_reuses_mutually_exclusive_public_replacements(self):
        from quorune.replacement_effects import apply_replacement, replacement_choice
        from quorune.semantic_runtime.zone_replacement_inputs import zone_change_snapshot_event
        subject = ZoneChangeSubjectSnapshot(object_id="object", object_ref="P-object", logical_object_id="object@0", owner="A", controller=None, origin="hand", destination="battlefield", destination_controller="C", entry_face_id="front", object_types=("artifact",), is_card_object=True)
        desc = entry_designation_handler("As this artifact enters, choose a color.", source_name="Sol Grail")[1]
        effects = EntryDesignationHandler().subject_replacement_effects(desc, subject=subject, component_id="entry")
        snapshot = ZoneChangeReplacementSnapshot(revision=0, event_sequence=0, apnap_order=("A", "B", "C", "D"), source_refs=(subject.object_ref,), subjects=(subject,), effects=effects)
        event = zone_change_snapshot_event(snapshot, subject)
        choice = replacement_choice(event, effects)
        self.assertEqual("C", choice.chooser)
        self.assertEqual(5, len(effects))
        red = next(effect for effect in effects if effect.effect_id.endswith(":R"))
        result = apply_replacement(choice, effects, red.effect_id)
        self.assertEqual("R", result.payload["entry_chosen_color"])
        self.assertIsNone(replacement_choice(result, effects))
        self.assertNotIn("entry_chosen_color", event.payload)
        departed = replace(subject, destination="exile")
        self.assertIsNone(replacement_choice(zone_change_snapshot_event(replace(snapshot, subjects=(departed,)), departed), effects))

    def test_chosen_characteristics_bind_public_value_to_existing_query_owner(self):
        desc = chosen_characteristics_handler("Creatures you control of the chosen type get +1/+1.")[1]
        context = ContinuousEffectSourceContext(source_object_id="source", source_ref="P-source", source_controller="A", source_timestamp=4, component_id="anthem", source_designations=FrozenMap({"chosen_creature_type": "elf"}))
        result = ChosenCharacteristicsHandler().lower(desc, context)
        self.assertEqual(1, len(result))
        self.assertIn("elf", result[0].applies.subtypes_all)
        self.assertEqual("A", result[0].applies.controller)
        color_desc = chosen_characteristics_handler("Creatures of the chosen color get +1/+1.")[1]
        color_effect = ChosenCharacteristicsHandler().lower(color_desc, replace(context, source_designations=FrozenMap({"chosen_color": "R"})))[0]
        self.assertEqual(("R",), color_effect.applies.colors_all)
        self.assertIsNone(color_effect.applies.controller)
        self.assertEqual((), ChosenCharacteristicsHandler().lower(desc, replace(context, source_designations=FrozenMap())))
        with self.assertRaises(SemanticNodeError):
            ChosenCharacteristicsHandler().lower(desc, replace(context, source_designations=FrozenMap({"chosen_creature_type": "made-up"})))
        for text in ("Creatures you control of the chosen type get +X/+X.", "Creatures of the chosen type have an arbitrary ability.", "Creatures of the chosen type get +1/+1 for each land you control."):
            self.assertIsNone(chosen_characteristics_handler(text))
        for change in ({"schema_version": True}, {"designation": "card_type"}, {"body": {"handler_id": "arbitrary"}}, {"extra": 1}):
            with self.assertRaises(SemanticNodeError):
                ChosenCharacteristicsHandler().validate({**desc, **change})

    def test_original_entry_designation_programs_close_and_keep_source_spans(self):
        registry = load_default_capability_registry()
        for record in (self.db.lookup("Rally the Ranks"), self.db.lookup("Shared Triumph"), self.db.lookup("Engineered Plague"), self.db.lookup("Plague Engineer"), self.db.lookup("Instruments of War")):
            with self.subTest(card=record.name):
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status)
                entry = next(node for node in ir.faces[0].nodes if node.template_id == "intrinsic-entry-designation-v1")
                self.assertEqual(entry.text, record.oracle_text[entry.span.start:entry.span.end])
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                self.assertTrue(bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"])

    def test_original_entry_designation_unsupported_siblings_reject_admission(self):
        registry = load_default_capability_registry()
        for record in (self.db.lookup("Gauntlet of Power"), self.db.lookup("Sol Grail"), self.db.lookup("Diamond Knight")):
            with self.subTest(card=record.name):
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertTrue(any(node.exact and node.template_id == "intrinsic-entry-designation-v1" for face in ir.faces for node in face.nodes))
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertFalse(binding["strict_capability_ready"])
                self.assertFalse(binding["compatible_ready"])

    def test_entry_designation_dependency_and_compiler_mutations_fail_closed(self):
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        for row in raw["capabilities"]:
            if row["id"] == "zone.entry.public_designation":
                row.update(status="blocked", blockers=["mutation removes entry designation owner"])
        blocked = CapabilityRegistry(raw)
        record = self.db.lookup("Rally the Ranks")
        ir = compile_oracle_card(record, capability_registry=blocked, capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        with patch("quorune.compiler.runtime_templates.entry_designation_handler", return_value=None):
            mutant = compile_oracle_card(record, capability_registry=load_default_capability_registry(), capability_profile="commander_review")
        self.assertNotEqual("exact", mutant.status)
        with patch("quorune.compiler.runtime_templates.chosen_characteristics_handler", return_value=None):
            mutant = compile_oracle_card(record, capability_registry=load_default_capability_registry(), capability_profile="commander_review")
        self.assertNotEqual("exact", mutant.status)

    def test_entry_designation_chosen_target_predicate_stays_unavailable(self):
        registry = load_default_capability_registry()
        boundary = composition_record("chosen-target-boundary", "Entry Choice Target Boundary", "Artifact", "As this artifact enters, choose a creature type.\n{1}, {T}: Return target creature card of the chosen type from your graveyard to your hand.")
        ir = compile_oracle_card(boundary, capability_registry=registry, capability_profile="commander_review")
        self.assertTrue(any(node.exact and "zone.entry.public_designation" in node.capability_dependencies for face in ir.faces for node in face.nodes))
        self.assertTrue(any("target_or_choice:target-predicate" in canonical_residual_families(residual) for face in ir.faces for residual in face.residuals))
        program = compile_best_available_card_program(self.db, boundary, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertFalse(binding["strict_capability_ready"])
        self.assertFalse(binding["compatible_ready"])

    def test_entry_composition_programs_bind_all_target_capabilities(self):
        registry = load_default_capability_registry()
        for name, sibling in (("Entry Choice Simple Aura", "attachment.aura.simple_object"), ("Entry Choice Typed Aura", "attachment.aura.typed_restriction"), ("Entry Choice Targeted Artifact", "target.public.player_or_damageable_permanent"), ("Steely Resolve", "target.protection.shroud_permanent")):
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status)
                dependencies = {value for face in ir.faces for node in face.nodes for value in node.capability_dependencies}
                self.assertLessEqual({"zone.entry.public_designation", sibling}, dependencies)
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                self.assertTrue(bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")["strict_capability_ready"])
        copy_ir = compile_oracle_card(self.db.lookup("Cackling Counterpart"), capability_registry=registry, capability_profile="commander_review")
        self.assertIn("target.revalidate_resolution", {value for face in copy_ir.faces for node in face.nodes for value in node.capability_dependencies})

    def test_entry_designations_clear_with_departed_zone_object(self):
        from quorune.zone_object_state import reset_card_after_zone_change
        card = CardInstance(object_id="choice-reset", ref="P-choice-reset", oracle_id="printed-source", printed_name="Designation diagnostic", owner="A", controller="C", zone="battlefield", annotations={"chosen_color": "R", "chosen_creature_type": "elf"})
        reset_card_after_zone_change(card, destination="hand", stack_to_battlefield=False)
        self.assertNotIn("chosen_color", card.annotations)
        self.assertNotIn("chosen_creature_type", card.annotations)
        self.assertEqual("A", card.controller)
        self.assertIsNone(chosen_characteristics_handler("Creatures you control of the chosen type get +1/+1 until end of turn."))

    def test_duplicate_intrinsic_designations_preserve_linking_residuals(self):
        original = self.db.lookup("Rally the Ranks")
        duplicate = replace(original, oracle_text="As this enchantment enters, choose a creature type.\n" + original.oracle_text)
        ir = compile_oracle_card(duplicate, capability_registry=load_default_capability_registry(), capability_profile="commander_review")
        self.assertNotEqual("exact", ir.status)
        entries = [node for face in ir.faces for node in face.nodes if node.template_id == "intrinsic-entry-designation-v1"]
        self.assertEqual(2, len(entries))
        self.assertTrue(all(not node.exact and not node.lowerable and not node.handlers for node in entries))
        self.assertTrue(any(residual.kind == "entry_designation" for face in ir.faces for residual in face.residuals))

    def test_linked_source_type_additions_keep_the_existing_entry_owner(self):
        registry = load_default_capability_registry()
        for name in ("Roaming Throne", "Metallic Mimic", "Adaptive Automaton"):
            with self.subTest(card=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                entry = next(node for face in ir.faces for node in face.nodes if node.template_id == "intrinsic-entry-designation-v1")
                self.assertFalse(entry.exact)
                self.assertFalse(entry.lowerable)
                self.assertFalse(entry.handlers)
                self.assertEqual(entry.text, record.oracle_text[entry.span.start:entry.span.end])
                self.assertTrue(any(residual.kind == "entry_designation" for face in ir.faces for residual in face.residuals))

    def card(self, session, name, seat, zone="hand"):
        engine = session.engine
        record = self.db.lookup(name)
        ref = engine._next_ref("P")
        card = CardInstance(object_id=f"choice-fixture:{ref}", ref=ref, oracle_id=record.oracle_id, printed_name=record.name, owner=seat, controller=seat, zone=zone, zone_timestamp=engine._next_zone_timestamp(), known_to=list(engine.seats) if zone == "battlefield" else [seat], revealed_to=list(engine.seats) if zone == "battlefield" else [])
        engine.state.cards[card.object_id] = card
        engine.state.players[seat].zones[zone].append(card.object_id)
        register_generated_programs(self.db, engine.semantics, (record,), trust_level="trusted", capability_registry=load_default_capability_registry(), capability_profile="commander_review", promote_exact_runtime_handlers=True, promote_exact_trigger_programs=True, promote_exact_effect_programs=True, promote_exact_capability_declarations=True)
        return card

    def test_printed_entry_designation_cast_choice_privacy_persistence_and_replay(self):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=61412001, auto_pass_empty=False)
        keep_all(session)
        engine = session.engine
        spell = self.card(session, "Rally the Ranks", "A")
        elf = self.card(session, "Llanowar Elves", "A", "battlefield")
        other_elf = self.card(session, "Llanowar Elves", "B", "battlefield")
        bear = self.card(session, "Baleful Strix", "A", "battlefield")
        secret = self.card(session, "Shared Triumph", "D")
        engine.state.players["A"].mana_pool.update(C=1, W=1)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"cast:{spell.ref}"
        self.assertTrue(any(row["id"] == action_id for row in offered))
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        cast = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        for _ in range(12):
            if engine.state.pending_decision.kind == "replacement.order":
                break
            pass_current(session)
        decision = session.packet("pilot:A", full=True)["decision"]
        self.assertIn("replacement", decision["kind"])
        self.assertEqual("stack", engine.state.cards[spell.object_id].zone)
        selected = next(option["id"] for option in decision["ctx"]["options"] if option["id"].endswith(":elf"))
        before = authoritative_state_hash(engine.state)
        rejected = session.act("pilot:B", {"action_id": "choose", "choices": {"replacement": selected}})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        self.assertNotIn(secret.printed_name, json.dumps(session.packet("pilot:B", full=True)))
        with tempfile.TemporaryDirectory() as pending_directory:
            session.save(pending_directory)
            session = CommanderSession.load(self.db, pending_directory)
        engine = session.engine
        self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": selected}})
        self.assertTrue(accepted.ok, accepted.summary)
        spell = engine.state.cards[spell.object_id]
        self.assertEqual("battlefield", spell.zone)
        self.assertEqual("elf", spell.annotations["chosen_creature_type"])
        self.assertEqual("2", engine._effective_card_data(engine.state.cards[elf.object_id])["power"])
        self.assertEqual("1", engine._effective_card_data(engine.state.cards[other_elf.object_id])["power"])
        self.assertEqual("1", engine._effective_card_data(engine.state.cards[bear.object_id])["power"])
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_printed_copy_gets_fresh_entry_designation_and_replays(self):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=61412002, auto_pass_empty=False)
        keep_all(session)
        engine = session.engine
        source = self.card(session, "Plague Engineer", "A")
        spell = self.card(session, "Cackling Counterpart", "A")
        elf = self.card(session, "Llanowar Elves", "B", "battlefield")
        bird = self.card(session, "Baleful Strix", "D", "battlefield")
        engine.state.players["A"].mana_pool.update(C=3, B=1, U=4)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        def reach_entry_choice():
            for _ in range(16):
                if session.state.pending_decision.kind == "replacement.order":
                    return session.packet("pilot:A", full=True)["decision"]
                pass_current(session)
            self.fail("Printed spell did not reach its intrinsic entry choice")

        action_id = f"cast:{source.ref}"
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertTrue(any(row["id"] == action_id for row in offered))
        cast = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        decision = reach_entry_choice()
        original_choice = next(row["id"] for row in decision["ctx"]["options"] if row["id"].endswith(":elf"))
        chosen = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": original_choice}})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("elf", engine.state.cards[source.object_id].annotations["chosen_creature_type"])
        self.assertEqual("graveyard", engine.state.cards[elf.object_id].zone)
        self.assertEqual("battlefield", engine.state.cards[bird.object_id].zone)

        action_id = f"cast:{spell.ref}"
        offered = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next((row for row in offered if row["id"] == action_id), None)
        self.assertIsNotNone(action, {"kind": engine.state.pending_decision.kind, "priority": engine.state.priority_player, "mana": engine.state.players["A"].mana_pool, "actions": [row["id"] for row in offered]})
        self.assertIn(source.ref, action["target_schema"]["legal_refs"])
        cast = session.act("pilot:A", {"action_id": action_id, "targets": [source.ref], "pay": "auto"})
        self.assertTrue(cast.ok, cast.summary)
        decision = reach_entry_choice()
        self.assertFalse(any(card.is_token and card.printed_name == "Plague Engineer" for card in engine.state.cards.values()))
        before = authoritative_state_hash(engine.state)
        stale = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": original_choice}})
        self.assertFalse(stale.ok)
        self.assertEqual(before, authoritative_state_hash(engine.state))
        fresh_choice = next(row["id"] for row in decision["ctx"]["options"] if row["id"].endswith(":bird"))
        chosen = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": fresh_choice}})
        self.assertTrue(chosen.ok, chosen.summary)
        tokens = [card for card in engine.state.cards.values() if card.is_token and card.printed_name == "Plague Engineer"]
        self.assertEqual(1, len(tokens))
        self.assertEqual("bird", tokens[0].annotations["chosen_creature_type"])
        self.assertEqual("elf", engine.state.cards[source.object_id].annotations["chosen_creature_type"])
        self.assertEqual("graveyard", engine.state.cards[bird.object_id].zone)
        self.assertNotIn("chosen_creature_type_adds_subtype", tokens[0].annotations)
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])

    def test_entry_choices_compose_with_aura_targets_and_printed_shroud(self):
        # CR 115.1b/303.4a lock Aura targets before the CR 614.12a entry
        # choice. CR 702.18a excludes the chosen type for every controller.
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=61412003, auto_pass_empty=False)
        keep_all(session)
        engine = session.engine
        simple = self.card(session, "Entry Choice Simple Aura", "A")
        typed = self.card(session, "Entry Choice Typed Aura", "A")
        artifact = self.card(session, "Entry Choice Targeted Artifact", "A")
        shroud = self.card(session, "Steely Resolve", "A")
        own_elf = self.card(session, "Llanowar Elves", "A", "battlefield")
        opposing_elf = self.card(session, "Llanowar Elves", "B", "battlefield")
        bird = self.card(session, "Baleful Strix", "D", "battlefield")
        engine.state.players["A"].mana_pool.update(C=4, U=3, G=1)
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine._grant_priority("A")
        engine.pump()
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()

        def actions():
            return session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]

        def enter(card, value, target=None):
            action = next(row for row in actions() if row["id"] == f"cast:{card.ref}")
            command = {"action_id": action["id"], "pay": "manual", "payment": {"U": 1} if target is not None or card is artifact else {"C": 1, "G": 1}}
            if target is not None:
                self.assertIn(target.ref, action["target_schema"]["legal_refs"])
                self.assertNotIn(shroud.ref, action["target_schema"]["legal_refs"])
                command["targets"] = [target.ref]
            accepted = session.act("pilot:A", command)
            self.assertTrue(accepted.ok, accepted.summary)
            for _ in range(16):
                if session.state.pending_decision.kind == "replacement.order":
                    break
                pass_current(session)
            else:
                self.fail("Cast did not reach the intrinsic entry choice")
            self.assertEqual("stack", card.zone)
            self.assertIsNone(card.attached_to)
            decision = session.packet("pilot:A", full=True)["decision"]
            choice = next(row["id"] for row in decision["ctx"]["options"] if row["id"].endswith(":" + value))
            chosen = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": choice}})
            self.assertTrue(chosen.ok, chosen.summary)
            self.assertEqual("battlefield", card.zone)
            if target is not None:
                self.assertEqual(target.object_id, card.attached_to)
                self.assertIn(card.object_id, target.attachments)
                self.assertEqual(value, card.annotations["chosen_color"])

        enter(simple, "R", opposing_elf)
        enter(typed, "W", bird)
        enter(artifact, "B")
        enter(shroud, "elf")
        self.assertEqual("elf", shroud.annotations["chosen_creature_type"])
        self.assertIn("Shroud", engine._effective_card_data(own_elf)["keywords"])
        self.assertIn("Shroud", engine._effective_card_data(opposing_elf)["keywords"])
        self.assertNotIn("Shroud", engine._effective_card_data(bird)["keywords"])
        activation = next(row for row in actions() if row["id"].startswith(f"activate:{artifact.ref}:"))
        legal_refs = activation["target_schema"]["legal_refs"]
        self.assertIn(bird.ref, legal_refs)
        self.assertNotIn(own_elf.ref, legal_refs)
        self.assertNotIn(opposing_elf.ref, legal_refs)
        before = authoritative_state_hash(engine.state)
        for target in (own_elf, opposing_elf):
            rejected = session.act("pilot:A", {"action_id": activation["id"], "targets": [target.ref], "pay": "auto"})
            self.assertFalse(rejected.ok)
            self.assertEqual(before, authoritative_state_hash(engine.state))
        accepted = session.act("pilot:A", {"action_id": activation["id"], "targets": [bird.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        for _ in range(16):
            if not engine.state.stack:
                break
            pass_current(session)
        self.assertEqual("graveyard", engine.state.cards[bird.object_id].zone)
        self.assertEqual("graveyard", engine.state.cards[typed.object_id].zone)
        self.assertEqual("battlefield", engine.state.cards[simple.object_id].zone)
        self.assertEqual(opposing_elf.object_id, engine.state.cards[simple.object_id].attached_to)
        self.assertEqual("B", engine.state.cards[artifact.object_id].annotations["chosen_color"])
        expected = authoritative_state_hash(engine.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            replay = replay_record(directory, self.db, verify=True)
        self.assertTrue(replay["ok"], replay)
        self.assertEqual(expected, replay["final_state_hash"])


if __name__ == "__main__":
    unittest.main()
