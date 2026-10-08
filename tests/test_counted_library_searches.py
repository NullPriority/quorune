from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from scripts.build_test_database import build_fixture_database, compact_ci_fixture_paths
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckLoader
from quorune.library_search_model import FixedCountedLibrarySearchTemplate
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.library_search_capability_shapes import fixed_counted_library_search_node_capabilities
from quorune.semantics import SemanticProgram, SemanticRegistry
from quorune.session import CommanderSession
from quorune.zone_transitions import ZoneTransitionOwner


CAPABILITY = "library.search.fixed_counted"


class CountedLibrarySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "counted-searches.sqlite3"
        build_fixture_database(compact_ci_fixture_paths(root=ROOT), path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def card(self, session, name, zone="hand", owner="A"):
        engine = session.engine
        record = self.db.lookup(name)
        ref = engine._next_ref("P")
        card = CardInstance(
            object_id=f"counted-search:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=owner, controller=owner, zone=zone,
            known_to=list(engine.seats) if zone == "battlefield" else [owner],
            revealed_to=list(engine.seats) if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[owner].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db, engine.semantics, (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def setup_cast(self, name, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed, auto_pass_empty=False)
        keep_all(session)
        spell = self.card(session, name)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update(C=12, W=12, U=12, B=12, R=12, G=12)
        return session, spell

    def offered_cast(self, session, spell):
        engine = session.engine
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"cast:{spell.ref}"
        self.assertTrue(any(action["id"] == action_id for action in actions), actions)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def reach_search(self, session):
        for _ in range(24):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "semantic.search":
                return session.packet("pilot:A", full=True)["decision"]
            if decision is not None and decision.kind == "semantic.choice":
                result = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
                self.assertTrue(result.ok, result.summary)
            else:
                pass_current(session)
        self.fail("Printed search did not become available")

    def assert_replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            result = replay_record(directory, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def test_original_search_programs_are_strictly_capability_bound(self):
        registry = load_default_capability_registry()
        for name in ("Demonic Tutor", "Vampiric Tutor", "Merrow Harbinger", "Treasure Mage", "Trophy Mage", "Squadron Hawk", "Ignite the Beacon", "Entomb"):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)

    def test_counted_search_schema_dependencies_and_mutations_fail_closed(self):
        from quorune.compiler.library_search_templates import fixed_library_search_effect_template
        template = fixed_library_search_effect_template("Search your library for a card, then shuffle and put that card on top.")
        self.assertIsInstance(template, FixedCountedLibrarySearchTemplate)
        effect = template.effect()
        self.assertEqual(template, FixedCountedLibrarySearchTemplate.from_effect(effect))
        for field, value in (("schema_version", True), ("shuffle_after", True), ("shuffle_before_placement", False), ("unknown", True)):
            malformed = copy.deepcopy(effect)
            malformed[field] = value
            self.assertEqual((), fixed_counted_library_search_node_capabilities(effects=(malformed,), target_schema=None, mechanic_ids=template.compiled()[3]))
        registry_data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        row = next(row for row in registry_data["capabilities"] if row["id"] == CAPABILITY)
        row["status"] = "blocked"
        row["blockers"] = ["Deliberately blocked owner"]
        blocked = CapabilityRegistry(registry_data)
        record = self.db.lookup("Demonic Tutor")
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=blocked, capability_profile="commander_review").status)
        with patch("quorune.oracle_ir.fixed_library_search_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=load_default_capability_registry(), capability_profile="commander_review").status)
        for text in ("Search your library for X cards, put them into your hand, then shuffle.", "Search your library for two creature cards, put them onto the battlefield, then shuffle.", "Search your library for a card named Clone or Sol Ring, put it into your hand, then shuffle."):
            self.assertIsNone(fixed_library_search_effect_template(text))

    def test_reviewed_search_precedence_requires_matching_trust_source_and_semantics(self):
        from quorune.card_programs.reviewed_overlay import shadowed_reviewed_program_keys
        record = self.db.lookup("Entomb")
        reviewed = next(
            program for program in SemanticRegistry().programs_for_oracle(record.oracle_id)
            if program.ability_id == "spell:front"
        )
        self.assertNotIn("schema_version", reviewed.effects[0])
        session, _spell = self.setup_cast("Entomb", 70123004)
        generated = session.engine.semantics.get(reviewed.key)
        self.assertEqual(2, generated.effects[0]["schema_version"])
        self.assertEqual(
            {reviewed.key}, shadowed_reviewed_program_keys(record, (generated,), (reviewed,)),
        )
        for field, value in (
            ("trust_level", "proposed"),
            ("target_schema", {"kind": "unsupported"}),
            ("provenance", {**generated.provenance, "source_oracle_hash": "0" * 64}),
            ("effects", [{**generated.effects[0], "reveal": True}]),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(generated)
                setattr(changed, field, value)
                self.assertEqual(
                    set(), shadowed_reviewed_program_keys(record, (changed,), (reviewed,)),
                )
        self.assertNotIn("schema_version", reviewed.effects[0])

    def test_persisted_reviewed_search_keeps_legacy_execution_and_replay(self):
        # Execute the actual preserved reviewed Entomb payload, rather than
        # claiming historical compatibility from decoding alone.
        session, spell = self.setup_cast("Entomb", 70123005)
        chosen = self.card(session, "Duplicant", "library")
        reviewed = next(
            program for program in SemanticRegistry().programs_for_oracle(spell.oracle_id)
            if program.ability_id == "spell:front"
        )
        restored = SemanticProgram.from_dict(reviewed.to_dict())
        self.assertEqual(reviewed.to_dict(), restored.to_dict())
        self.assertNotIn("schema_version", restored.effects[0])
        session.engine.semantics.put(restored)
        self.offered_cast(session, spell)
        self.reach_search(session)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[chosen.object_id].zone)
        self.assertEqual(restored.to_dict(), session.engine.semantics.get(restored.key).to_dict())
        self.assert_replay(session)

    def test_printed_unqualified_search_requires_a_card_and_keeps_private_choices(self):
        # CR 701.23b/d/e distinguish a stated quality from a pure quantity.
        session, spell = self.setup_cast("Demonic Tutor", 70123001)
        chosen = self.card(session, "Duplicant", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(1, schema["minimum"])
        self.assertFalse(schema["rules_may_fail_to_find"])
        self.assertIn(chosen.ref, schema["legal_refs"])
        for seat in ("B", "C", "D"):
            self.assertNotIn(chosen.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        before = authoritative_state_hash(session.state)
        for principal, response in (("pilot:B", {"action_id": "choose", "search_cards": [chosen.ref]}), ("pilot:A", {"action_id": "choose", "search_cards": []})):
            result = session.act(principal, response)
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[chosen.object_id].zone)
        for seat in ("B", "C", "D"):
            self.assertNotIn(chosen.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.assert_replay(session)

    def test_printed_multi_search_commander_replacements_persist_per_card(self):
        # Jeska and Tevesh are printed Partner commanders. CR 903.9b applies
        # independently to each selected commander in the simultaneous move.
        session, spell = self.setup_cast("Ignite the Beacon", 90309001)
        engine = session.engine
        for card in engine.state.cards.values():
            if card.owner == "A" and card.is_commander:
                card.is_commander = False
                card.commander_designation_id = None
        commanders = [self.card(session, name, "library") for name in ("Jeska, Thrice Reborn", "Tevesh Szat, Doom of Fools")]
        for card in commanders:
            card.is_commander = True
            card.commander_designation_id = f"commander:{card.object_id}"
        engine.state.commander_oracle_ids["A"] = [card.oracle_id for card in commanders]
        self.offered_cast(session, spell)
        self.reach_search(session)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in commanders]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertTrue(all(card.zone == "library" for card in commanders))
        before = authoritative_state_hash(session.state)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        decline = next(option["id"] for option in options if option["id"].startswith("decline:"))
        wrong = session.act("pilot:B", {"action_id": "choose", "choices": {"replacement": decline}})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        result = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": decline}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        apply = next(option["id"] for option in options if not option["id"].startswith("decline:"))
        result = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": apply}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["command", "hand"], sorted(session.state.cards[card.object_id].zone for card in commanders))
        self.assert_replay(session)

    def test_printed_numeric_search_filters_x_zero_and_allows_failure_to_find(self):
        # Treasure Mage ruling: X in a library card's mana cost is zero.
        session, spell = self.setup_cast("Treasure Mage", 70123002)
        large = self.card(session, "Duplicant", "library")
        small = self.card(session, "Sol Ring", "library")
        variable = self.card(session, "Stonecoil Serpent", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertIn(large.ref, schema["legal_refs"])
        self.assertNotIn(small.ref, schema["legal_refs"])
        self.assertNotIn(variable.ref, schema["legal_refs"])
        self.assertEqual(0, schema["minimum"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "search_cards": [small.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("library", large.zone)
        self.assert_replay(session)

    def test_printed_top_search_excludes_found_card_from_shuffle_and_preserves_identity(self):
        # CR 701.24b and Vampiric Tutor's ruling: shuffle-and-place is one action.
        session, spell = self.setup_cast("Vampiric Tutor", 70124001)
        chosen = self.card(session, "Duplicant", "library")
        self.offered_cast(session, spell)
        self.reach_search(session)
        identity = chosen.logical_object_id
        life = session.state.players["A"].life
        shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        calls = []
        original = ZoneTransitionOwner.shuffle_library
        def observed(owner, seat, **kwargs):
            calls.append(kwargs.get("excluded_object_ids", ()))
            return original(owner, seat, **kwargs)
        with patch.object(ZoneTransitionOwner, "shuffle_library", observed):
            result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([(chosen.object_id,)], calls)
        self.assertEqual(chosen.object_id, session.state.players["A"].zones["library"][-1])
        self.assertEqual(identity, chosen.logical_object_id)
        self.assertEqual(shuffles + 1, session.state.players["A"].stats["shuffle_count"])
        self.assertEqual(life - 2, session.state.players["A"].life)
        self.assertIn("A", chosen.known_to)
        self.assertNotIn("B", chosen.known_to)
        library = session.state.players["A"].zones["library"]
        self.assertTrue(all(
            not session.state.cards[object_id].known_to
            and not session.state.cards[object_id].revealed_to
            for object_id in library if object_id != chosen.object_id
        ))
        packet = session.packet("pilot:A", full=True)
        self.assertEqual(
            [chosen.ref],
            [row["id"] for row in packet["state"]["players"]["A"]["known_top"]],
        )
        self.assertNotIn(
            "known_top", session.packet("pilot:B", full=True)["state"]["players"]["A"],
        )
        self.assert_replay(session)

    def test_printed_named_multi_search_moves_one_simultaneous_private_batch(self):
        session, spell = self.setup_cast("Squadron Hawk", 70123003)
        hawks = [self.card(session, "Squadron Hawk", "library") for _ in range(4)]
        other = self.card(session, "Skyshroud Sentinel", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(3, schema["maximum"])
        self.assertTrue(all(card.ref in schema["legal_refs"] for card in hawks))
        self.assertNotIn(other.ref, schema["legal_refs"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in hawks]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        selected = hawks[:3]
        calls = []
        original = ZoneTransitionOwner.move_cards_simultaneously
        def observed(owner, changes, **kwargs):
            calls.append(tuple(changes))
            return original(owner, changes, **kwargs)
        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", observed):
            result = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([tuple((card.object_id, "hand") for card in selected)], calls)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in selected))
        self.assertEqual("library", session.state.cards[hawks[3].object_id].zone)
        self.assertTrue(all(set(session.engine.seats) <= set(session.state.cards[card.object_id].known_to) for card in selected))
        self.assert_replay(session)
