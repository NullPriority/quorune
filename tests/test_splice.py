from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import test_fixed_reveal_additional_costs as reveal
import test_self_public_zone_casting as actions
from test_granted_skulk import record
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.splice import SPLICE_CAPABILITY, SpliceSpec
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


ORIGINALS = (
    "Blessed Breath", "Candles' Glow", "Consuming Vortex", "Dampen Thought", "Desperate Ritual",
    "Everdream", "Glacial Ray", "Hideous Laughter", "Kodama's Might", "Lifted by Clouds",
    "Psychic Puppetry", "Soulless Revival", "Spiritual Visit", "Splicer's Skill",
    "Torrent of Stone", "Vital Surge", "Wear Away",
)


class SpliceCompilerTests(unittest.TestCase):
    def test_splice_cost_is_source_spanned_and_rejects_open_prices(self):
        registry = load_default_capability_registry()
        text = "Draw a card.\nSplice onto instant or sorcery {2}{U}"
        ir = compile_oracle_card(record(text, "Instant"), capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node = next(node for node in ir.faces[0].nodes if node.event == "cast.cost")
        self.assertEqual(node.text, text[node.span.start:node.span.end])
        self.assertIn(SPLICE_CAPABILITY, node.capability_dependencies)
        self.assertEqual(SpliceSpec("instant_or_sorcery", {"GENERIC": 2, "U": 1}).mana["U"],
            node.handlers[0]["splice"]["mana"]["U"])
        for invalid in ("Splice onto creature {U}", "Splice onto Arcane {X}",
            "Splice onto Arcane—Tap an untapped white creature you control."):
            with self.subTest(invalid=invalid):
                ir = compile_oracle_card(record(f"Draw a card.\n{invalid}", "Instant"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)

    def test_withdrawn_splice_authority_leaves_the_printed_cost_residual(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owner = next(row for row in raw["capabilities"] if row["id"] == SPLICE_CAPABILITY)
        owner.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        original = record("Draw a card.\nSplice onto Arcane {U}", "Instant — Arcane")
        self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=registry).status)
        self.assertEqual("exact", compile_oracle_card(record("Draw a card.", "Instant"),
            capability_registry=registry).status)

    def test_splice_declaration_omission_is_killed(self):
        registry = load_default_capability_registry()
        original = record("Draw a card.\nSplice onto Arcane {U}", "Instant — Arcane")
        self.assertEqual("exact", compile_oracle_card(original, capability_registry=registry).status)
        with patch("quorune.compiler.splice_nodes.printed_splice_spec", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=registry).status)


class SpliceRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = reveal.FixedRevealCostRuntimeTests.session
    add = reveal.FixedRevealCostRuntimeTests.add
    fund = reveal.FixedRevealCostRuntimeTests.fund
    checkpoint = reveal.FixedRevealCostRuntimeTests.checkpoint
    resolve = reveal.FixedRevealCostRuntimeTests.resolve
    replay = reveal.FixedRevealCostRuntimeTests.replay
    reject = reveal.FixedRevealCostRuntimeTests.reject
    cast = actions.SelfPublicZoneRuntimeTests.cast

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "splice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/composed-spell-text-cards.json", ROOT / "tests/fixtures/splice-cards.json",
            ROOT / "tests/fixtures/fixed-nonrepeating-modal-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Splice witness", [DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30)], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def start_splice(self, session, card, *, modes=()):
        ctx = session.packet("pilot:A", full=True)["decision"]["ctx"]
        offer = next(action for action in ctx["legal"]["actions"] if action["id"] == f"cast:{card.ref}")
        self.assertIn("prepare_splice", offer["choice_schema"])
        result = session.act("pilot:A", {"action_id": offer["id"], "prepare_splice": True,
            **({"modes": list(modes)} if modes else {})})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("selection.splice_cast", session.state.pending_decision.kind)
        self.assertEqual("hand", session.state.cards[card.object_id].zone)

    def splice_actions(self, session):
        return {row["id"]: row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal_actions"]}

    def add_splice(self, session, card):
        action_id = f"add:{card.ref}"
        self.assertIn(action_id, self.splice_actions(session))
        result = session.act("pilot:A", {"action_id": action_id})
        self.assertTrue(result.ok, result.summary)

    def test_original_splice_bodies_and_prices_bind_strictly(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                program = compile_best_available_card_program(self.db, card, semantic_registry=SemanticRegistry(),
                    capability_registry=self.registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=self.registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)

    def test_actual_ordered_splice_uses_host_color_and_reveals_only_selected_cards(self):
        session = self.session(7024704)
        source = self.add(session.engine, "Reach Through Mists", zone="hand")
        ray = self.add(session.engine, "Glacial Ray", zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        unchosen = self.add(session.engine, "Dampen Thought", zone="hand")
        knight = self.add(session.engine, "Silver Knight", seat="B")
        goblin = self.add(session.engine, "Goblin Piledriver", seat="B")
        self.fund(session, {"U": 2, "R": 1, "C": 3})
        self.reject(session, action_id=f"cast:{source.ref}", splice_sources=[ray.ref], pay="auto")
        self.start_splice(session, source)
        cancelled = session.act("pilot:A", {"action_id": "cancel"})
        self.assertTrue(cancelled.ok, cancelled.summary)
        self.assertEqual("priority", session.state.pending_decision.kind)
        self.start_splice(session, source)
        self.add_splice(session, dream)
        for viewer in "BCD":
            packet = session.packet("pilot:" + viewer, full=True)
            self.assertIsNone(packet["decision"])
            for card in (dream, ray, unchosen):
                self.assertNotIn(card.ref, json.dumps(packet))
        stale = session
        session = self.replay(session, load=True)
        self.reject(session, "B", action_id=f"add:{ray.ref}")
        self.reject(session, action_id=f"add:{dream.ref}")
        self.add_splice(session, ray)
        self.assertNotIn(f"add:{unchosen.ref}", self.splice_actions(session))
        options = self.splice_actions(session)["cast"]["cost_options"]
        self.assertEqual((3, 2, 1), tuple(options[0]["requirements"][symbol] for symbol in ("GENERIC", "U", "R")))
        self.reject(session, action_id="cast", targets=[goblin.ref], pay="auto")
        self.reject(session, action_id="cast", targets=[knight.ref], pay="auto", _mana_payment_id="foreign")
        before = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {"action_id": "cast", "targets": [knight.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        spell, = session.state.stack
        self.assertEqual(["draw", "draw", "damage"], [effect["op"] for effect in spell.context["cast_option_effects"]])
        public = json.dumps(session.packet("pilot:D", full=True))
        for card in (dream, ray):
            self.assertIn(card.ref, public)
            self.assertEqual("hand", session.state.cards[card.object_id].zone)
        self.assertNotIn(unchosen.ref, public)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[knight.object_id].zone)
        self.assertEqual(before + 1, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)
        stale.engine.move_card(dream.object_id, "exile", log=False)
        stale.engine.move_card(dream.object_id, "hand", log=False)
        self.reject(stale, action_id=f"add:{ray.ref}")

    def test_actual_splice_sacrifices_two_mountains_and_fizzles_as_one_spell(self):
        session = self.session(7024705)
        source = self.add(session.engine, "Reach Through Mists", zone="hand")
        torrent = self.add(session.engine, "Torrent of Stone", zone="hand")
        target = self.add(session.engine, "Silver Knight", seat="B")
        bounce = self.add(session.engine, "Unsummon", seat="B", zone="hand")
        lands = [self.add(session.engine, "Mountain", ref=f"price-mountain-{index}") for index in range(2)]
        foreign = self.add(session.engine, "Mountain", seat="B", ref="foreign-mountain")
        session.state.players["B"].mana_pool["U"] = 1
        self.fund(session, {"U": 1})
        self.start_splice(session, source)
        self.add_splice(session, torrent)
        choice = self.splice_actions(session)["cast"]["cost_options"][0]["choice_schema"]
        field = next(key for key, spec in choice.items() if spec.get("destination") == "graveyard")
        command = {"action_id": "cast", "targets": [target.ref], "pay": "auto"}
        self.reject(session, **command, **{field: [lands[0].ref, foreign.ref]})
        result = session.act("pilot:A", {**command, field: [land.ref for land in lands]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["graveyard", "graveyard"],
            [session.state.cards[land.object_id].zone for land in lands])
        hand = len(session.state.players["A"].zones["hand"])
        self.cast(session, "B", bounce, [target.ref])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand, len(session.state.players["A"].zones["hand"]))
        self.assertEqual("hand", session.state.cards[torrent.object_id].zone)
        self.assertEqual("hand", session.state.cards[target.object_id].zone)
        self.replay(session, load=True)

    def test_spliced_storm_copy_keeps_its_text_after_the_original_is_countered(self):
        session = self.session(7024706)
        prior = self.add(session.engine, "Reach Through Mists", zone="hand")
        source = self.add(session.engine, "Brain Freeze", zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        counter = self.add(session.engine, "Counterspell", seat="B", zone="hand")
        session.state.players["B"].mana_pool["U"] = 2
        self.fund(session, {"U": 3, "C": 3})
        self.cast(session, "A", prior)
        self.assertIsNone(self.resolve(session))
        self.start_splice(session, source)
        self.add_splice(session, dream)
        result = session.act("pilot:A", {"action_id": "cast", "targets": ["B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        host_spell = next(item for item in session.state.stack if item.card_object_id == source.object_id)
        hand = len(session.state.players["A"].zones["hand"])
        library = len(session.state.players["B"].zones["library"])
        self.cast(session, "B", counter, [host_spell.ref])
        decision = self.resolve(session)
        self.assertIsNotNone(decision)
        self.assertEqual("semantic.storm", decision.kind)
        result = session.act("pilot:A", {"action_id": "choose"})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(hand + 1, len(session.state.players["A"].zones["hand"]))
        self.assertEqual(library - 3, len(session.state.players["B"].zones["library"]))
        self.assertEqual("hand", session.state.cards[dream.object_id].zone)
        self.replay(session, load=True)

    def test_actual_modal_cast_keeps_declared_modes_and_added_targets(self):
        session = self.session(7024707)
        source = self.add(session.engine, "Kolaghan's Command", zone="hand")
        dream = self.add(session.engine, "Everdream", zone="hand")
        artifact = self.add(session.engine, "Generic Bound Mentor", seat="B")
        self.fund(session, {"C": 3, "B": 1, "R": 1, "U": 1})
        self.start_splice(session, source, modes=("mode_3", "mode_4"))
        self.add_splice(session, dream)
        hand = len(session.state.players["A"].zones["hand"])
        life = session.state.players["B"].life
        result = session.act("pilot:A", {"action_id": "cast", "targets": [artifact.ref, "B"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        spell, = session.state.stack
        self.assertEqual(["mode_3", "mode_4"], spell.modes)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[artifact.object_id].zone)
        self.assertEqual(life - 2, session.state.players["B"].life)
        self.assertEqual(hand, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)
