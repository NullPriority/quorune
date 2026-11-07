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
from quorune.compiler.splice_nodes import printed_splice_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.casting_additional_costs import COLOR_RETURN_ADDITIONAL_COST_CAPABILITY, COLOR_RETURN_SCHEMA_VERSION
from quorune.rules.splice import SPLICE_CAPABILITY, SpliceSpec
from quorune.rules.tap_additional_cost import COLOR_TAP_ADDITIONAL_COST_CAPABILITY, COLOR_TAP_SCHEMA_VERSION, FixedTapAdditionalCost
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database


ORIGINALS = (
    "Blessed Breath", "Candles' Glow", "Consuming Vortex", "Dampen Thought", "Desperate Ritual",
    "Everdream", "Glacial Ray", "Hideous Laughter", "Hundred-Talon Strike", "Kodama's Might", "Lifted by Clouds",
    "Psychic Puppetry", "Soulless Revival", "Spiritual Visit", "Splicer's Skill",
    "Torrent of Stone", "Veil of Secrecy", "Vital Surge", "Wear Away",
)

COLOR_RETURN = "Splice onto Arcane—Return a blue creature you control to its owner's hand."
COLOR_TAP = "Splice onto Arcane—Tap an untapped white creature you control."


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
            "Splice onto Arcane—Tap two untapped white creatures you control."):
            with self.subTest(invalid=invalid):
                ir = compile_oracle_card(record(f"Draw a card.\n{invalid}", "Instant"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)

    def registry_with_withdrawal(self, capability):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        owner = next(row for row in raw["capabilities"] if row["id"] == capability)
        owner.update(status="blocked", blockers=["withdrawn for the dependency witness"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        return registry

    def test_withdrawn_splice_authority_leaves_the_printed_cost_residual(self):
        registry = self.registry_with_withdrawal(SPLICE_CAPABILITY)
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

    def test_color_prices_use_closed_owner_descriptors(self):
        registry = load_default_capability_registry()
        blue = printed_splice_spec(COLOR_RETURN)
        white = printed_splice_spec(COLOR_TAP)
        self.assertEqual((COLOR_RETURN_SCHEMA_VERSION, ("U",)),
            (blue.zone_change.schema_version, blue.zone_change.predicate.colors_all))
        self.assertEqual((COLOR_TAP_SCHEMA_VERSION, ("W",)), (white.tap.schema_version, white.tap.colors_all))
        for spec in (blue, white):
            self.assertEqual(spec, SpliceSpec.from_dict(spec.to_dict()))
        legacy = FixedTapAdditionalCost(1, ("creature",)).to_descriptor()
        self.assertEqual({"schema_version": 1, "kind": "fixed_tap", "count": 1,
            "permanent_types": ["creature"]}, legacy)
        with self.assertRaises(ValueError):
            FixedTapAdditionalCost.from_descriptor({**legacy, "colors_all": ["W"]})
        for text in (COLOR_RETURN, COLOR_TAP):
            ir = compile_oracle_card(record(f"Draw a card.\n{text}", "Instant — Arcane"), capability_registry=registry)
            self.assertEqual("exact", ir.status, ir.material_residuals)

    def test_color_price_capabilities_fail_closed_independently(self):
        for capability, blocked, retained in (
            (COLOR_RETURN_ADDITIONAL_COST_CAPABILITY, COLOR_RETURN, COLOR_TAP),
            (COLOR_TAP_ADDITIONAL_COST_CAPABILITY, COLOR_TAP, COLOR_RETURN),
        ):
            with self.subTest(capability=capability):
                registry = self.registry_with_withdrawal(capability)
                for expected, text in ((False, blocked), (True, retained)):
                    ir = compile_oracle_card(record(f"Draw a card.\n{text}", "Instant — Arcane"), capability_registry=registry)
                    self.assertEqual(expected, ir.status == "exact", ir.material_residuals)

    def test_color_price_lowering_omission_is_killed(self):
        registry = load_default_capability_registry()
        for helper, text in (("fixed_zone_change_additional_cost_template", COLOR_RETURN),
            ("fixed_tap_additional_cost_template", COLOR_TAP)):
            with self.subTest(helper=helper):
                original = record(f"Draw a card.\n{text}", "Instant — Arcane")
                self.assertEqual("exact", compile_oracle_card(original, capability_registry=registry).status)
                with patch(f"quorune.compiler.splice_nodes.{helper}", return_value=None):
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
    assert_closed = actions.SelfPublicZoneRuntimeTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "splice.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/composed-spell-text-cards.json", ROOT / "tests/fixtures/splice-cards.json",
            ROOT / "tests/fixtures/fixed-nonrepeating-modal-cards.json",
            ROOT / "tests/fixtures/assist-payment-cards.json"], path)
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

    def test_actual_colored_splice_prices_share_tapped_creature_then_return_to_owner(self):
        session = self.session(7024708)
        engine = session.engine
        source = self.add(engine, "Reach Through Mists", zone="hand")
        veil = self.add(engine, "Veil of Secrecy", zone="hand")
        strike = self.add(engine, "Hundred-Talon Strike", zone="hand")
        borrowed = self.add(engine, "Zealous Guardian", seat="B", zone="hand")
        engine.move_card(borrowed.object_id, "battlefield", controller="A", log=False)
        white = self.add(engine, "Generic Bound Body", ref="only-white")
        tapped = self.add(engine, "Generic Bound Body", ref="already-tapped-white")
        tapped.tapped = True
        painted = self.add(engine, "Generic Bound Body", ref="current-blue")
        painted.annotations["copy_overrides"] = {"colors": ["U"]}
        blue = self.add(engine, "Cloud of Faeries", ref="only-blue")
        target = self.add(engine, "Generic Bound Body", seat="B", ref="foreign-white")
        power = engine._numeric_stat(target.object_id, "power")
        self.fund(session, {"U": 1})
        self.start_splice(session, source)
        self.add_splice(session, veil)
        self.add_splice(session, strike)
        choice = self.splice_actions(session)["cast"]["cost_options"][0]["choice_schema"]
        returned = next(key for key, value in choice.items() if value.get("payment") == "return")
        self.assertEqual({borrowed.ref, white.ref}, set(choice["tap_cost_cards"]["legal_refs"]))
        self.assertEqual({borrowed.ref, blue.ref, painted.ref}, set(choice[returned]["legal_refs"]))
        spectator = session.packet("pilot:D", full=True)
        self.assertIsNone(spectator["decision"])
        self.assertNotIn(veil.ref, json.dumps(spectator))
        self.assertNotIn(strike.ref, json.dumps(spectator))
        session = self.replay(session, load=True)
        command = {"action_id": "cast", "targets": [target.ref, target.ref], "pay": "auto"}
        for tap_ref, return_ref in ((tapped.ref, borrowed.ref), (painted.ref, borrowed.ref),
            (target.ref, borrowed.ref), (borrowed.ref, white.ref)):
            self.reject(session, **command, tap_cost_cards=[tap_ref], **{returned: [return_ref]})
        before = len(session.state.events)
        hand = len(session.state.players["A"].zones["hand"])
        result = session.act("pilot:A", {**command, "tap_cost_cards": [borrowed.ref], returned: [borrowed.ref]})
        self.assertTrue(result.ok, result.summary)
        current = session.state.cards[borrowed.object_id]
        self.assertEqual(("B", "hand"), (current.owner, current.zone))
        self.assertIn(borrowed.object_id, session.state.players["B"].zones["hand"])
        price_events = [event.code for event in session.state.events[before:]
            if borrowed.object_id in event.changed_objects and event.code.startswith("cost.")]
        self.assertEqual(["cost.tap", "cost.return"], price_events)
        self.assertEqual(["hand", "hand"], [session.state.cards[card.object_id].zone for card in (veil, strike)])
        self.assertIsNone(self.resolve(session))
        current_target = session.state.cards[target.object_id]
        keywords = {word.casefold() for word in session.engine._effective_card_data(current_target)["keywords"]}
        self.assertTrue({"shroud", "first strike"}.issubset(keywords))
        self.assertEqual(power + 1, session.engine._numeric_stat(target.object_id, "power"))
        self.assertEqual(hand, len(session.state.players["A"].zones["hand"]))
        self.replay(session, load=True)


    def test_actual_assist_reveals_only_the_selected_splice_before_contribution(self):
        session = self.session(7024714)
        spell = self.add(session.engine, "Bring Down", zone="hand")
        spliced = self.add(session.engine, "Splicer's Skill", zone="hand")
        private = self.add(session.engine, "Dampen Thought", zone="hand", ref="private-splice-control")
        target = self.add(session.engine, "Generic Bound Body", seat="C")
        target.counters["+1/+1"] = 2
        for card in (spell, spliced):
            self.assert_closed(session, card)
        session.state.players["B"].mana_pool["C"] = 5
        self.fund(session, {"W": 2, "C": 1})
        self.start_splice(session, spell)
        self.add_splice(session, spliced)
        before = json.dumps(session.packet("pilot:B", full=True))
        self.assertNotIn(spliced.ref, before)
        self.assertNotIn(private.ref, before)
        cast = self.splice_actions(session)["cast"]
        option, = cast["cost_options"]
        self.assertEqual(["B"], option["choice_schema"]["assist_helper"]["legal_values"])
        self.assertEqual((6, 2), (option["requirements"]["GENERIC"], option["requirements"]["W"]))
        command = {"action_id": cast["id"], "targets": [target.ref], "pay": "auto", "assist_helper": "B"}
        self.reject(session, "D", **command)
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual("cast.assist", session.state.pending_decision.kind)
        self.assertEqual(["B"], session.state.pending_decision.actors)
        expected = [{"id": spliced.ref, "name": spliced.printed_name}]
        for actor in ("B", "D"):
            packet = session.packet(f"pilot:{actor}", full=True)
            self.assertEqual(expected, packet["state"]["assist_cast"]["spliced_cards"])
            self.assertNotIn(private.ref, json.dumps(packet))
        self.assertEqual(expected, session.packet("pilot:B", full=True)["decision"]["ctx"]["spliced_cards"])
        self.assertIsNone(session.packet("pilot:D", full=True)["decision"])
        self.assertFalse(session.state.stack)
        session = self.replay(session, load=True)
        self.assertEqual(expected, session.packet("pilot:B", full=True)["decision"]["ctx"]["spliced_cards"])

        for actor in ("B", "A"):
            accepted = session.act(f"pilot:{actor}", {"action_id": "assist:finish-mana"})
            self.assertTrue(accepted.ok, accepted.summary)
        self.reject(session, "B", action_id="assist:contribute", assist_amount=6)
        accepted = session.act("pilot:B", {"action_id": "assist:contribute", "assist_amount": 5})
        self.assertTrue(accepted.ok, accepted.summary)
        accepted = session.act("pilot:A", {"action_id": "assist:pay"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(0, sum(session.state.players["A"].mana_pool.values()))
        self.assertEqual(0, sum(session.state.players["B"].mana_pool.values()))
        self.assertEqual("hand", session.state.cards[spliced.object_id].zone)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("graveyard", session.state.cards[target.object_id].zone)
        token, = [card for card in session.state.cards.values() if card.is_token and card.zone == "battlefield"]
        self.assertEqual(("A", "Phyrexian Golem Token"), (token.controller, token.printed_name))
        self.replay(session, load=True)
