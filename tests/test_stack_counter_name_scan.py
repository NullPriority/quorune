"""Pinned command and replay proof for countering into a different-name cast."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.random_selection import choose_random_integer
from quorune.stack_spell_name_scan_model import NAME_SCAN_CAPABILITY, StackSpellNameScan
from scripts.build_test_database import build_fixture_database


CARD = "Tibalt's Trickery"
ORACLE_ID = "6623cb85-fb1e-4561-8b21-0977c25096c2"


class StackCounterNameScanTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "counter-name-scan.sqlite3"
        build_fixture_database(
            [
                ROOT / "tests/fixtures/bound-effect-program-cards.json",
                ROOT / "tests/fixtures/inherited-rule-composition-cards.json",
                ROOT / "tests/fixtures/nonmodal-face-mana-value-cards.json",
                ROOT / "tests/fixtures/graveyard-cast-stack-copies-cards.json",
                ROOT / "tests/fixtures/stack-counter-name-scan-cards.json",
            ],
            path,
        )
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition(
            "Stack counter name scan",
            [DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 12)],
            ["Isamaru, Hound of Konda"],
        )

    def test_original_compiler_and_withdrawal_keep_a_closed_target_and_result(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        card = self.db.by_oracle_id(ORACLE_ID)

        def compile_with(registry=self.registry, record=card):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        ir = compile_with()
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertIn(NAME_SCAN_CAPABILITY, node.capability_dependencies)
        self.assertEqual("resolve_stack_counter_name_scan", node.effects[0]["op"])
        self.assertEqual(1, node.target_schema["count"])
        for old, new in (("1, 2, or 3", "2, 3, or 4"), ("a different name", "the same name"),
                         ("its controller", "you"), ("on the bottom", "on top")):
            changing = card.oracle_text
            changed = changing.replace(old, new).replace(old.capitalize(), new.capitalize())
            self.assertNotEqual(changing, changed)
            self.assertNotEqual("exact", compile_with(record=replace(card, oracle_text=changed)).status)
        with patch("quorune.compiler.stack_counter_followup._COUNTER_NAME_SCAN", "unused"):
            self.assertNotEqual("exact", compile_with().status)

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        next(row for row in raw["capabilities"] if row["id"] == NAME_SCAN_CAPABILITY).update(
            status="blocked", blockers=["Withdraw the different-name scan producer"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compile_with(registry).status)

    def test_distinct_names_use_all_names_and_named_nonland_requirement(self):
        scan = StackSpellNameScan("resolving", "target", "B", ("Fire", "Ice"))
        self.assertEqual(scan, StackSpellNameScan.from_dict(scan.to_dict()))
        self.assertTrue(scan.qualified(types=("instant",), names=("Gush",)))
        self.assertFalse(scan.qualified(types=("instant",), names=("Flame", "Ice")))
        self.assertFalse(scan.qualified(types=("land", "artifact"), names=("Darksteel Citadel",)))
        self.assertFalse(scan.qualified(types=("instant",), names=()))
        with self.assertRaises(ValueError):
            scan.require_resolution(actor="C", stack_ref="resolving", targets=("target",))
        with self.assertRaises(ValueError):
            StackSpellNameScan.from_dict({**scan.to_dict(), "excluded_names": ["Ice", "Fire"]})

    def counter_fixture(self, *, seed, target_name, library_names):
        session = self.session(seed)
        state = session.state
        engine = session.engine
        source = self.add(engine, CARD, zone="hand", ref="name-scan-trickery")
        target = self.add(engine, target_name, seat="B", zone="hand", ref="name-scan-target")
        self.add(engine, "Mountain", seat="B")
        self.add(engine, "Swamp", seat="B")
        self.add(engine, "Forest", seat="B")
        self.add(engine, "Island", seat="C", ref="name-scan-counter-mana-one")
        self.add(engine, "Island", seat="C", ref="name-scan-counter-mana-two")
        body = self.add(engine, "Grizzly Bears", ref="name-scan-bear")
        counter = self.add(engine, "Counterspell", seat="C", zone="hand", ref="name-scan-reply")
        library = state.players["B"].zones["library"]
        for object_id in tuple(library):
            engine.move_card(object_id, "exile", log=False)
        tracked = [
            self.add(engine, name, seat="B", zone="library", ref=f"name-scan-library-{index}")
            for index, name in enumerate(library_names)
        ]
        library[:] = [card.object_id for card in reversed(tracked)]
        self.assert_closed(session, source)
        self.assert_closed(session, target)
        self.prepare(session, mana={"C": 1, "R": 1})
        self.checkpoint(session)
        self.cast(session, "B", target, ["D" if target_name == "Lightning Bolt" else body.ref])
        target_ref = state.stack[-1].ref
        self.cast(session, "A", source, [target_ref])
        count = choose_random_integer(1, 3, seed=state.config.seed,
                                      instruction_key=state.stack[-1].stack_id, seat="B")
        return session, source, target, counter, tracked, count, target_ref

    def test_affected_player_casts_after_matching_names_and_land_and_reloads_choice(self):
        names = ["Lightning Bolt"] * 4 + ["Forest", "Grizzly Bears", "Generic Bound Plains"]
        session, source, target, _, library, count, _ = self.counter_fixture(
            seed=5761661, target_name="Lightning Bolt", library_names=names,
        )
        before = {key: tuple(session.state.players[key].zones["library"]) for key in "ACD"}
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        self.assertEqual(["graveyard"] * count + ["exile"] * (6 - count) + ["library"],
                         [session.state.cards[card.object_id].zone for card in library])
        self.assertNotIn(library[-1].ref, json.dumps(session.packet("pilot:C", full=True)))
        before_choice = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": "choose", "choice": "decline"}).ok)
        self.assertEqual(before_choice, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        option, = session.packet("pilot:B", full=True)["decision"]["ctx"]["cast_options"]
        result = session.act("pilot:B", {"action_id": "choose", "choice": "cast", "cast": {"cost_option": option["id"]}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("stack", session.state.cards[library[-2].object_id].zone)
        self.assertEqual("B", session.state.stack[-1].controller)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertCountEqual([card.object_id for card in library[count:5]], session.state.players["B"].zones["library"][:5-count])
        self.assertEqual(before, {key: tuple(session.state.players[key].zones["library"]) for key in "ACD"})
        self.replay(session, load=True)

    def test_uncounterable_target_stays_after_the_target_controller_declines(self):
        session, source, target, _, library, count, target_ref = self.counter_fixture(
            seed=5761662, target_name="Abrupt Decay",
            library_names=["Forest"] * 4 + ["Grizzly Bears", "Generic Bound Plains"],
        )
        pending = self.resolve(session)
        self.assertEqual("semantic.choice", pending.kind)
        self.assertEqual(["pilot:B"], session.pending_principals())
        self.assertEqual("stack", session.state.cards[target.object_id].zone)
        result = session.act("pilot:B", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(target_ref, session.state.stack[-1].ref)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertCountEqual([card.object_id for card in library[count:5]], session.state.players["B"].zones["library"][:5-count])
        self.replay(session, load=True)

    def test_exhausted_scan_bottoms_every_exiled_card_without_offering_a_cast(self):
        session, source, target, _, library, count, _ = self.counter_fixture(
            seed=5761663, target_name="Lightning Bolt", library_names=["Lightning Bolt", "Forest"] * 3,
        )
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertCountEqual([card.object_id for card in library[count:]], session.state.players["B"].zones["library"])
        self.replay(session, load=True)

    def test_illegal_target_skips_the_random_mill_and_exile(self):
        session, _, _, counter, library, _, target_ref = self.counter_fixture(
            seed=5761664, target_name="Lightning Bolt", library_names=["Forest", "Grizzly Bears"] * 3,
        )
        self.cast(session, "C", counter, [target_ref])
        self.resolve_top(session)
        self.assertIsNone(self.resolve(session))
        self.assertEqual([card.object_id for card in reversed(library)], session.state.players["B"].zones["library"])
        self.replay(session, load=True)
