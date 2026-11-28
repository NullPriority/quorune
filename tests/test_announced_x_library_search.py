"""Announced and sealed entry X use the counted hidden-search owner."""

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
import inherited_composition_fixture as actions
from quorune.carddb import CardDatabase
from quorune.card_programs.reviewed_overlay import shadowed_reviewed_counted_search_keys
from quorune.deck import DeckDefinition, DeckEntry
from quorune.library_search_model import FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, counted_search_bound_dependencies
from quorune.oracle_ir import compile_oracle_card
from quorune.query_effect_amount_model import CastXAmountSpec, DECLARED_AMOUNT_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.semantics import SemanticRegistry
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


class AnnouncedXLibrarySearchModelTests(unittest.TestCase):
    text = "Search your library for a green creature card with mana value X or less, put it onto the battlefield, then shuffle."

    def test_declared_x_projects_only_into_the_counted_search_bound(self):
        caps = load_default_capability_registry()
        original = record(self.text, "Sorcery")
        ir = compile_oracle_card(replace(original, mana_cost="{X}{G}"), capability_registry=caps)
        self.assertEqual("exact", ir.status)
        node, = [node for face in ir.faces for node in face.nodes if node.effects]
        effect, = node.effects
        self.assertEqual(self.text, self.text[node.span.start:node.span.end])
        self.assertEqual(CastXAmountSpec().to_dict(), effect["selector"]["mana_value"]["maximum"])
        self.assertEqual({"minimum": 1, "maximum": 1}, effect["count"])
        self.assertEqual(
            {FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DECLARED_AMOUNT_CAPABILITY},
            set(counted_search_bound_dependencies(effect)),
        )
        self.assertNotEqual("exact", compile_oracle_card(original, capability_registry=caps).status)

        for malformed in (
            {**effect, "schema_version": 1},
            {**effect, "count": {"minimum": 0, "maximum": CastXAmountSpec().to_dict()}},
            {**effect, "selector": {"types": ["creature"], "mana_value": {"maximum": CastXAmountSpec(-1).to_dict()}}},
            {**effect, "selector": {"types": ["creature"], "mana_value": {"unknown": CastXAmountSpec().to_dict()}}},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                counted_search_bound_dependencies(malformed)

    def test_loyalty_x_accepts_only_the_closed_artifact_search_result(self):
        text = (
            "−X: Search your library for an artifact card with mana value X or less, "
            "put it onto the battlefield, then shuffle."
        )
        caps = load_default_capability_registry()
        card = record(text, "Legendary Planeswalker — Test")
        for unsupported in (
            text.replace("an artifact", "a creature"),
            text.replace("X or less", "X"),
            text.replace("onto the battlefield", "into your hand"),
            text.replace("onto the battlefield", "onto the battlefield tapped"),
        ):
            with self.subTest(unsupported=unsupported):
                variant = replace(card, oracle_text=unsupported)
                self.assertNotEqual("exact", compile_oracle_card(variant, capability_registry=caps).status)
        ir = compile_oracle_card(card, capability_registry=caps)
        self.assertEqual("exact", ir.status)
        node, = ir.faces[0].nodes
        self.assertEqual(
            {FIXED_COUNTED_LIBRARY_SEARCH_CAPABILITY_ID, DECLARED_AMOUNT_CAPABILITY, "activation.loyalty.announced_x"},
            set(node.capability_dependencies),
        )

    def test_omitting_the_search_bound_projection_is_detected(self):
        with patch("quorune.compiler.declared_effect_amounts.counted_search_mana_value_slot", return_value=False):
            with self.assertRaises(AssertionError):
                self.test_declared_x_projects_only_into_the_counted_search_bound()


class AnnouncedXLibrarySearchRuntimeTests(actions.InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "announced-x-library-search.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/scryfall-exact-lists.json",
            ROOT / "tests/fixtures/source-entry-cast-cards.json",
            ROOT / "tests/fixtures/kicked-entry-triggers.json",
            ROOT / "tests/fixtures/state-based-destination-cards.json",
            ROOT / "tests/fixtures/announced-x-library-search-cards.json",
            ROOT / "tests/fixtures/untargeted-untap-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced X search", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def prepare(self, session, **kwargs):
        session.state.config.semantic_policy = "trusted_only"
        super().prepare(session, **kwargs)

    def cast_x(self, session, spell, x, **prices):
        action = self.action(session, "A", spell)
        return session.act("pilot:A", {"action_id": action["id"], "x": x, "pay": "auto", **prices})

    def search_packet(self, session, expected_maximum):
        self.assertEqual("semantic.search", self.resolve(session).kind)
        packet = session.packet("pilot:A", full=True)["decision"]
        self.assertEqual({"maximum": expected_maximum}, packet["ctx"]["search_spec"]["selector"]["mana_value"])
        return packet

    def shuffles(self, session):
        return session.state.players["A"].stats.get("shuffle_count", 0)

    def test_original_green_sun_x_zero_searches_privately_then_shuffles_twice(self):
        session = self.session(1070301)
        spell = self.add(session.engine, "Green Sun's Zenith", zone="hand")
        dryad = self.add(session.engine, "Dryad Arbor", zone="library", ref="x-search-private-dryad")
        elf = self.add(session.engine, "Llanowar Elves", zone="library", ref="x-search-high-cost-elf")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"G": 1})
        baseline = self.shuffles(session)
        self.checkpoint(session)
        result = self.cast_x(session, spell, 0)
        self.assertTrue(result.ok, result.summary)
        packet = self.search_packet(session, 0)
        self.assertEqual([dryad.ref], packet["legal_actions"][0]["choice_schema"]["legal_refs"])
        for viewer in "BCD":
            self.assertNotIn(dryad.ref, json.dumps(session.packet("pilot:" + viewer, full=True)))
        self.assertEqual("stack", session.state.cards[spell.object_id].zone)
        self.assertEqual(baseline, self.shuffles(session))
        before = authoritative_state_hash(session.state)
        for principal, ref in (("pilot:B", dryad.ref), ("pilot:A", elf.ref)):
            self.assertFalse(session.act(principal, {"action_id": "choose", "search_cards": [ref]}).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [dryad.ref]})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("battlefield", session.state.cards[dryad.object_id].zone)
        self.assertEqual("library", session.state.cards[spell.object_id].zone)
        self.assertEqual(baseline + 2, self.shuffles(session))
        self.replay(session, load=True)

    def test_original_flute_announces_x_and_can_fail_to_find_a_matching_creature(self):
        session = self.session(1070302)
        flute = self.add(session.engine, "Citanul Flute")
        elf = self.add(session.engine, "Llanowar Elves", zone="library")
        self.add(session.engine, "Grizzly Bears", zone="library")
        self.assert_closed(session, flute)
        self.prepare(session, mana={"C": 2})
        baseline = self.shuffles(session)
        self.checkpoint(session)
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == "activate:" + flute.ref + ":ab1")
        result = session.act("pilot:A", {"action_id": action["id"], "x": 1, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertTrue(session.state.cards[flute.object_id].tapped)
        self.assertEqual(1, session.state.players["A"].mana_pool["C"])
        packet = self.search_packet(session, 1)
        schema = packet["legal_actions"][0]["choice_schema"]
        self.assertEqual([elf.ref], schema["legal_refs"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("library", session.state.cards[elf.object_id].zone)
        self.assertEqual(baseline + 1, self.shuffles(session))
        self.replay(session, load=True)

    def test_original_reshape_pays_the_typed_sacrifice_before_search_and_replay(self):
        session = self.session(1070303)
        spell = self.add(session.engine, "Reshape", zone="hand")
        payment = self.add(session.engine, "Generic Bound Mentor", ref="x-search-own-artifact")
        foreign = self.add(session.engine, "Generic Bound Mentor", seat="B", ref="x-search-foreign-artifact")
        result_card = self.add(session.engine, "Sol Ring", zone="library")
        self.add(session.engine, "Generic Bound Mentor", zone="library", ref="x-search-high-artifact")
        self.assert_closed(session, spell)
        generated, = session.engine.semantics.programs_for_oracle(spell.oracle_id)
        legacy, = SemanticRegistry().programs_for_oracle(spell.oracle_id)
        original = self.db.by_oracle_id(spell.oracle_id)
        self.assertEqual({legacy.key}, shadowed_reviewed_counted_search_keys(original, [generated], [legacy]))
        for changed in (
            replace(legacy, provenance={**legacy.provenance, "source_oracle_hash": "0" * 64}),
            replace(legacy, cost_schema={"additional_costs": [{"kind": "sacrifice", "count": 2, "types_any": ["Artifact"]}]}),
            replace(legacy, effects=[{**legacy.effects[0], "selector": {"types": ["Creature"], "mana_value": {"maximum": "$x"}}}]),
        ):
            self.assertEqual(set(), shadowed_reviewed_counted_search_keys(original, [generated], [changed]))
        self.prepare(session, mana={"C": 1, "U": 2})
        baseline = self.shuffles(session)
        self.checkpoint(session)
        before = authoritative_state_hash(session.state)
        self.assertFalse(self.cast_x(session, spell, 1, sacrifice_cards=[foreign.ref]).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = self.cast_x(session, spell, 1, sacrifice_cards=[payment.ref])
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[payment.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[foreign.object_id].zone)
        packet = self.search_packet(session, 1)
        self.assertEqual([result_card.ref], packet["legal_actions"][0]["choice_schema"]["legal_refs"])
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [result_card.ref]})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("battlefield", session.state.cards[result_card.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[spell.object_id].zone)
        self.assertEqual(baseline + 1, self.shuffles(session))
        self.replay(session, load=True)

    def test_original_rocco_searches_with_entry_x_after_the_source_departs(self):
        session = self.session(1070304)
        rocco = self.add(session.engine, "Rocco, Cabaretti Caterer", zone="hand")
        elf = self.add(session.engine, "Llanowar Elves", zone="library")
        self.add(session.engine, "Grizzly Bears", zone="library")
        bolt = self.add(session.engine, "Lightning Bolt", seat="B", zone="hand")
        self.assert_closed(session, rocco)
        self.prepare(session, mana={"C": 1, "R": 1, "G": 1, "W": 1})
        session.state.players["B"].mana_pool["R"] = 1
        baseline = self.shuffles(session)
        self.checkpoint(session)
        result = self.cast_x(session, rocco, 1)
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(session)
        self.assertEqual("triggered_ability", session.state.stack[-1].kind)
        self.cast(session, "B", bolt, [rocco.ref])
        self.resolve_top(session)
        self.assertEqual("graveyard", session.state.cards[rocco.object_id].zone)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.assertEqual(baseline, self.shuffles(session))
        session = self.replay(session, load=True)
        result = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(result.ok, result.summary)
        packet = self.search_packet(session, 1)
        self.assertEqual([elf.ref], packet["legal_actions"][0]["choice_schema"]["legal_refs"])
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [elf.ref]})
        self.assertTrue(result.ok, result.summary)
        self.settle(session)
        self.assertEqual("battlefield", session.state.cards[elf.object_id].zone)
        self.assertEqual(baseline + 1, self.shuffles(session))
        self.replay(session, load=True)

    def tezzeret(self, session, loyalty):
        source = self.add(session.engine, "Tezzeret the Seeker")
        source.counters["loyalty"] = loyalty
        source.annotations["loyalty_initialized"] = True
        self.assert_closed(session, source)
        return source

    def activate_loyalty(self, session, source, slot, **selection):
        action_id = f"activate:{source.ref}:ab{slot}"
        offers = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertIn(action_id, {offer["id"] for offer in offers})
        result = session.act("pilot:A", {"action_id": action_id, **selection})
        self.assertTrue(result.ok, result.summary)

    def test_original_tezzeret_plus_one_keeps_zero_one_and_partial_target_resolution(self):
        for index, (targets, destroy_target) in enumerate(((0, False), (1, False), (1, True), (2, True))):
            with self.subTest(targets=targets, destroy_target=destroy_target):
                session = self.session(1070310 + index)
                source = self.tezzeret(session, 4)
                artifact = self.add(session.engine, "Sol Ring", ref="tezzeret-own-ring")
                foreign = self.add(session.engine, "Memnite", seat="B", ref="tezzeret-foreign-memnite")
                removal = self.add(session.engine, "Disenchant", seat="B", zone="hand")
                artifact.tapped = foreign.tapped = True
                self.prepare(session)
                session.state.players["B"].mana_pool.update({"C": 1, "W": 1})
                self.checkpoint(session)

                action = {"action_id": f"activate:{source.ref}:ab1"}
                before = authoritative_state_hash(session.state)
                invalid = [artifact.ref, artifact.ref]
                self.assertFalse(session.act("pilot:A", {**action, "targets": invalid}).ok)
                self.assertEqual(before, authoritative_state_hash(session.state))

                selected = [artifact.ref, foreign.ref][:targets]
                self.activate_loyalty(session, source, 1, targets=selected)
                self.assertEqual(selected, list(session.state.stack[-1].targets))
                self.assertEqual(5, session.state.cards[source.object_id].counters["loyalty"])
                self.assertTrue(session.state.cards[artifact.object_id].tapped)
                if destroy_target:
                    self.cast(session, "B", removal, [artifact.ref])
                    self.assertIsNone(self.resolve_top(session))
                    self.assertEqual("graveyard", session.state.cards[artifact.object_id].zone)
                self.assertIsNone(self.resolve(session))
                if not destroy_target:
                    self.assertEqual(targets == 0, session.state.cards[artifact.object_id].tapped)
                self.assertEqual(targets < 2, session.state.cards[foreign.object_id].tapped)
                self.assertFalse(session.act("pilot:A", {
                    "action_id": f"activate:{source.ref}:ab2", "x": 0,
                }).ok)
                self.replay(session, load=True)

    def test_original_tezzeret_minus_x_keeps_paid_bound_and_shuffle_on_choice_or_failure(self):
        for amount in (0, 1):
            with self.subTest(x=amount):
                session = self.session(1070320 + amount)
                source = self.tezzeret(session, 1)
                citadel = self.add(session.engine, "Darksteel Citadel", zone="library", ref="tezzeret-hidden-citadel")
                ring = self.add(session.engine, "Sol Ring", zone="library", ref="tezzeret-hidden-ring")
                self.add(session.engine, "Llanowar Elves", zone="library")
                self.prepare(session)
                baseline = self.shuffles(session)
                self.checkpoint(session)
                before = authoritative_state_hash(session.state)
                command = {"action_id": f"activate:{source.ref}:ab2"}
                for malformed in (-1, 2, True):
                    self.assertFalse(session.act("pilot:A", {**command, "x": malformed}).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))

                self.activate_loyalty(session, source, 2, x=amount)
                self.assertEqual(amount, session.state.stack[-1].x_value)
                self.assertEqual("graveyard" if amount else "battlefield", session.state.cards[source.object_id].zone)
                self.assertEqual(baseline, self.shuffles(session))
                packet = self.search_packet(session, amount)
                choice = packet["legal_actions"][0]["choice_schema"]
                expected = {citadel.ref, ring.ref} if amount else {citadel.ref}
                self.assertEqual(expected, set(choice["legal_refs"]))
                self.assertTrue(choice["rules_may_fail_to_find"])
                for viewer in "BCD":
                    projection = json.dumps(session.packet("pilot:" + viewer, full=True))
                    self.assertNotIn(citadel.ref, projection)
                    self.assertNotIn(ring.ref, projection)
                before = authoritative_state_hash(session.state)
                for actor, ref in (("B", citadel.ref), ("A", source.ref if amount else ring.ref)):
                    self.assertFalse(session.act(f"pilot:{actor}", {
                        "action_id": "choose", "search_cards": [ref],
                    }).ok)
                    self.assertEqual(before, authoritative_state_hash(session.state))

                session = self.replay(session, load=True)
                result = session.act("pilot:A", {
                    "action_id": "choose", "search_cards": [ring.ref] if amount else [],
                })
                self.assertTrue(result.ok, result.summary)
                self.settle(session)
                self.assertEqual("battlefield" if amount else "library", session.state.cards[ring.object_id].zone)
                self.assertEqual("library", session.state.cards[citadel.object_id].zone)
                self.assertEqual(baseline + 1, self.shuffles(session))
                self.replay(session, load=True)

    def test_original_tezzeret_minus_five_locks_artifact_set_and_expires_set_base(self):
        session = self.session(1070330)
        source = self.tezzeret(session, 5)
        land = self.add(session.engine, "Darksteel Citadel")
        creature = self.add(session.engine, "Memnite")
        late = self.add(session.engine, "Sol Ring", zone="hand")
        other = self.add(session.engine, "Sol Ring", seat="B", ref="tezzeret-other-ring")
        creature.counters["+1/+1"] = 1
        self.prepare(session, mana={"C": 1})
        self.checkpoint(session)
        self.activate_loyalty(session, source, 3)
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(session))

        def effective(card):
            current = session.state.cards[card.object_id]
            return session.engine._effective_card_data(current)

        def types(card):
            return session.engine._type_parts(effective(card)["type_line"])[0]

        self.assertEqual({"artifact", "land", "creature"}, types(land))
        self.assertEqual({"artifact", "creature"}, types(creature))
        def stats(card):
            return tuple(session.engine._numeric_stat(card.object_id, name) for name in ("power", "toughness"))

        self.assertEqual((5, 5), stats(land))
        self.assertEqual((6, 6), stats(creature))
        self.assertIn("Construct", effective(creature)["type_line"])
        self.assertEqual({"artifact"}, types(other))
        self.cast(session, "A", late)
        self.assertIsNone(self.resolve(session))
        self.assertEqual({"artifact"}, types(late))
        self.replay(session, load=True)

        current_turn = session.state.turn_sequence
        self.until(session, lambda game: game.state.turn_sequence > current_turn)
        self.assertEqual({"artifact", "land"}, types(land))
        self.assertEqual((2, 2), stats(creature))
        self.replay(session, load=True)
