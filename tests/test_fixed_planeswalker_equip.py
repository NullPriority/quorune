from __future__ import annotations

"""Native attachment and target restrictions of fixed-cost Equip abilities."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.attachment_actions import (
    EQUIP_SEMANTIC_KEY,
    PLANESWALKER_EQUIP_CAPABILITY,
    PLANESWALKER_EQUIP_SEMANTIC_KEY,
    fixed_equip_ability_spec,
)
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database


SOURCE = "Luxior, Giada's Gift"
WALKER = "Ajani, Caller of the Pride"


class PlaneswalkerEquipTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def use_database(cls, path, commander, land):
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Equip planeswalker", [DeckEntry(commander, 1, "commander"), DeckEntry(land, 30)], [commander]
        )

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        database = Path(cls.temporary.name) / "planeswalker-equip.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/current-life-effect-amount-cards.json",
            ROOT / "tests/fixtures/command-graveyard-cast-grant-cards.json",
            ROOT / "tests/fixtures/activation-public-state-cards.json",
            ROOT / "tests/fixtures/fixed-planeswalker-equip-cards.json",
            ROOT / "tests/fixtures/fixed-commander-equip-test-cards.json",
        ], database)
        cls.use_database(database, "Isamaru, Hound of Konda", "Generic Bound Plains")

    def compile_source(self, *, registry=None, text=None):
        source = self.db.lookup(SOURCE)
        if text is not None:
            source = replace(source, oracle_text=text)
        return compile_oracle_card(
            source, capability_registry=self.registry if registry is None else registry,
            capability_profile="commander_review",
        )

    def equip_command(self, session, source, target, *, planeswalker=True):
        key = PLANESWALKER_EQUIP_SEMANTIC_KEY if planeswalker else EQUIP_SEMANTIC_KEY
        current = session.state.cards[source.object_id]
        ability, = (a for a in session.engine._activated_abilities(current) if a.builtin_semantic_key == key)
        return {"action_id": f"activate:{source.ref}:{ability.ability_id}", "targets": [target.ref], "pay": "auto"}

    def submit(self, session, seat, command, *, valid=True):
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:" + seat, command)
        self.assertEqual(valid, result.ok, result.summary)
        if not valid:
            self.assertEqual(before, authoritative_state_hash(session.state))

    def assert_stats(self, session, card, expected):
        self.assertEqual(expected, tuple(
            session.engine._numeric_stat(card.object_id, stat) for stat in ("power", "toughness")
        ))

    def type_parts(self, session, card):
        current = session.state.cards[card.object_id]
        data = session.engine._effective_card_data(current)
        return session.engine._type_parts(data["type_line"])

    def test_commander_equip_requires_its_target_query_and_replays_both_equip_abilities(self):
        from quorune.card_programs.adapters import _compile_best_available_card_program_from_ir
        from quorune.card_programs.binding import bind_card_program_runtime, bind_semantic_program_runtime
        from quorune.rules.commander_keyword_grants import COMMANDER_PERMANENT_QUERY_CAPABILITY
        from quorune.semantics import SemanticRegistry

        name = "Command Fitting Test Equipment"
        record = self.db.lookup(name)
        profile = "commander_review"
        ir = compile_oracle_card(record, capability_registry=self.registry, capability_profile=profile)
        program = _compile_best_available_card_program_from_ir(
            self.db, record, ir, semantic_registry=SemanticRegistry(),
            capability_registry=self.registry, capability_profile=profile,
        )
        bound = bind_card_program_runtime(program, capability_registry=self.registry, profile=profile)
        self.assertTrue(bound["strict_capability_ready"], bound["blockers"])
        carrier, = (ability for ability in program.abilities
                    if COMMANDER_PERMANENT_QUERY_CAPABILITY in ability.capability_dependencies)
        stripped = replace(carrier, capability_dependencies=[], capability_closure=None)
        missing = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=profile)
        self.assertIn("capability:undeclared_runtime_dependency:" + COMMANDER_PERMANENT_QUERY_CAPABILITY,
                      missing["blockers"])

        session = self.session(565061000)
        equipment = self.add(session.engine, name, zone="hand")
        commander_id, = session.state.players["A"].zones["command"]
        commander = session.state.cards[commander_id]
        ordinary = self.add(session.engine, "Grizzly Bears")
        self.assert_closed(session, equipment)
        self.prepare(session, mana={"W": 1, "C": 8})
        self.checkpoint(session)
        for card in (commander, equipment):
            self.cast(session, "A", card)
            self.assertIsNone(self.resolve(session))
        current = session.state.cards[equipment.object_id]
        abilities = session.engine._activated_abilities(current)
        restricted, = (ability for ability in abilities if ability.target_schema.get("commander") is True)
        regular, = (ability for ability in abilities if ability.builtin_semantic_key == EQUIP_SEMANTIC_KEY
                    and not ability.target_schema.get("commander"))
        cheap = {"action_id": f"activate:{equipment.ref}:{restricted.ability_id}", "pay": "auto"}
        self.submit(session, "A", {**cheap, "targets": [ordinary.ref]}, valid=False)
        self.submit(session, "A", {**cheap, "targets": [commander.ref]})
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(commander_id, session.state.cards[equipment.object_id].attached_to)
        self.submit(session, "A", {"action_id": f"activate:{equipment.ref}:{regular.ability_id}",
                                  "pay": "auto", "targets": [ordinary.ref]})
        self.assertIsNone(self.resolve(session))
        self.assertEqual(ordinary.object_id, session.state.cards[equipment.object_id].attached_to)
        self.replay(session, load=True)

    def test_actual_luxior_counts_live_counters_and_keeps_loyalty_and_both_equips(self):
        session = self.session(565061001)
        source = self.add(session.engine, SOURCE, zone="hand")
        walker = self.add(session.engine, WALKER, zone="hand")
        bearer = self.add(session.engine, "Grizzly Bears")
        bearer.counters["charge"] = 2
        private = self.add(session.engine, "Disenchant", seat="D", zone="hand", ref="equip-private-discard")
        self.assert_closed(session, source)
        self.assert_closed(session, walker)
        self.prepare(session, mana={"W": 2, "C": 6})
        self.checkpoint(session)
        for card in (source, walker):
            self.cast(session, "A", card)
            self.assertIsNone(self.resolve(session))
        self.assertEqual(({"planeswalker"}, {"ajani"}, {"legendary"}), self.type_parts(session, walker))
        command = self.equip_command(session, source, walker)
        self.assertTrue(any(row["id"] == command["action_id"] for row in self.action_rows(session, "A")))
        self.submit(session, "D", command, valid=False)
        self.submit(session, "A", command)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(walker.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(({"creature"}, set(), {"legendary"}), self.type_parts(session, walker))
        self.assert_stats(session, walker, (4, 4))

        current = session.state.cards[walker.object_id]
        loyalty, = (a for a in session.engine._activated_abilities(current) if a.loyalty_delta == 1)
        action = {"action_id": f"activate:{walker.ref}:{loyalty.ability_id}", "targets": [walker.ref]}
        self.submit(session, "A", action)
        self.assertEqual(5, session.state.cards[walker.object_id].counters["loyalty"])
        self.assert_stats(session, walker, (5, 5))
        self.assertIsNone(self.resolve(session))
        self.assert_stats(session, walker, (7, 7))
        self.submit(session, "A", action, valid=False)

        self.submit(session, "A", self.equip_command(session, source, bearer, planeswalker=False))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(bearer.object_id, session.state.cards[source.object_id].attached_to)
        self.assertEqual(({"planeswalker"}, {"ajani"}, {"legendary"}), self.type_parts(session, walker))
        self.assertEqual(5, session.state.cards[walker.object_id].counters["loyalty"])
        self.assert_stats(session, bearer, (4, 4))
        for seat in "ABC":
            packet = json.dumps(session.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.object_id, packet)
        self.replay(session, load=True)

    def action_rows(self, session, seat):
        return session.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]

    def test_planeswalker_equip_rejects_opponent_or_creature_and_revalidates_a_paid_target(self):
        session = self.session(565061002)
        source = self.add(session.engine, SOURCE)
        own = self.add(session.engine, WALKER, ref="equip-friendly-walker")
        own.counters["loyalty"] = 4
        other = self.add(session.engine, WALKER, seat="B", ref="equip-opposing-walker")
        other.counters["loyalty"] = 4
        creature = self.add(session.engine, "Grizzly Bears")
        removal = self.add(session.engine, "Hero's Downfall", seat="B", zone="hand")
        self.add(session.engine, "Swamp", seat="B", ref="equip-b1")
        self.add(session.engine, "Swamp", seat="B", ref="equip-b2")
        self.add(session.engine, "Swamp", seat="B", ref="equip-b3")
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        self.submit(session, "A", self.equip_command(session, source, other), valid=False)
        self.submit(session, "A", self.equip_command(session, source, creature), valid=False)
        self.submit(session, "A", self.equip_command(session, source, own, planeswalker=False), valid=False)

        paid = self.equip_command(session, source, own)
        self.submit(session, "A", paid)
        self.assertFalse(any(session.state.players["A"].mana_pool.values()))
        self.assertFalse(any(row["id"] == paid["action_id"] for row in self.action_rows(session, "A")))
        self.cast(session, "B", removal, [own.ref])
        self.resolve_top(session)
        session = self.replay(session, load=True)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[own.object_id].zone)
        self.assertIsNone(session.state.cards[source.object_id].attached_to)
        self.assertEqual(4, session.state.cards[other.object_id].counters["loyalty"])
        self.replay(session, load=True)

    def test_source_departure_revokes_the_types_without_erasing_walkers_existing_counters(self):
        session = self.session(565061003)
        source = self.add(session.engine, SOURCE)
        walker = self.add(session.engine, WALKER)
        walker.counters.update({"loyalty": 4, "charge": 2})
        removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
        self.add(session.engine, "Plains", seat="B", ref="equip-source-b1")
        self.add(session.engine, "Plains", seat="B", ref="equip-source-b2")
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        self.submit(session, "A", self.equip_command(session, source, walker))
        self.assertIsNone(self.resolve(session))
        self.assert_stats(session, walker, (6, 6))

        # Pass A's priority once so B can destroy the Equipment normally.
        self.submit(session, "A", {"action_id": "pass"})
        self.cast(session, "B", removal, [source.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertEqual(({"planeswalker"}, {"ajani"}, {"legendary"}), self.type_parts(session, walker))
        self.assertEqual({"loyalty": 4, "charge": 2}, session.state.cards[walker.object_id].counters)
        self.assertFalse(session.state.cards[walker.object_id].attachments)
        self.replay(session, load=True)

    def test_complete_source_rejects_changed_or_open_equip_and_retains_ordinary_equip(self):
        result = self.compile_source()
        self.assertEqual("exact", result.status, result.material_residuals)
        self.assertEqual(4, len(result.faces[0].nodes))
        self.assertIn(PLANESWALKER_EQUIP_CAPABILITY, result.faces[0].nodes[2].capability_dependencies)
        self.assertIn("attachment.equip.fixed_mana", result.faces[0].nodes[3].capability_dependencies)
        for line in ("Equip planeswalker {W/U}", "Equip planeswalker {1} or {2}",
                     "Equip planeswalker {1}. This ability costs {1} less to activate."):
            with self.subTest(line=line):
                self.assertIsNone(fixed_equip_ability_spec(line))
                text = self.db.lookup(SOURCE).oracle_text.replace("Equip planeswalker {1}", line)
                self.assertNotEqual("exact", self.compile_source(text=text).status)

    def test_withdrawn_verified_equip_authority_blocks_the_original(self):
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == PLANESWALKER_EQUIP_CAPABILITY).update(
            status="blocked", blockers=["Equip planeswalker authority withdrawn"]
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        result = self.compile_source(registry=registry)
        self.assertNotEqual("exact", result.status)
        self.assertEqual(["Equip planeswalker {1}"], [row.text for row in result.material_residuals])
        self.assertEqual("exact", self.compile_source(registry=registry, text="Equip {3}").status)

    def test_omitted_attached_counter_scaling_mutant_fails_the_native_stat_witness(self):
        from quorune.rules import live_modifier_quantities
        from quorune.characteristic_fragments import CharacteristicQuantityReduction

        original = live_modifier_quantities.current_modifier_quantity
        mutations = []

        def omit_counter_total(host, source, quantity, **kwargs):
            if quantity.reduction is CharacteristicQuantityReduction.TOTAL_COUNTERS:
                mutations.append(quantity)
                return 0
            return original(host, source, quantity, **kwargs)

        with patch.object(live_modifier_quantities, "current_modifier_quantity", side_effect=omit_counter_total):
            with self.assertRaises(AssertionError):
                self.test_actual_luxior_counts_live_counters_and_keeps_loyalty_and_both_equips()
        self.assertTrue(mutations)
