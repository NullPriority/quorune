from __future__ import annotations

"""Pinned CR 400.7/603.6a/608.2c-d: one private choice, one entry batch."""

from dataclasses import replace
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
import test_bound_effect_programs as witnesses


class PrivateHandCollectionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = load_default_capability_registry()

    def compile(self, text, type_line="Sorcery"):
        card = replace(witnesses.record(text, type_line), name="Private Collection Witness")
        return compile_oracle_card(card, capability_registry=self.registry,
                                   capability_profile="commander_review")

    def test_private_collection_grammar_preserves_bounds_and_entry_policy(self):
        for text, maximum, tapped in (
            ("Put up to two creature cards from your hand onto the battlefield.", 2, False),
            ("You may put any number of land cards from your hand onto the battlefield tapped.", None, True),
            ("Put any number of permanent cards from your hand onto the battlefield.", None, False),
            ("Put up to two non-Saga permanent cards from your hand onto the battlefield.", 2, False),
        ):
            with self.subTest(text=text):
                ir = self.compile(text)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                node, = ir.faces[0].nodes
                effect, = node.effects
                self.assertEqual("put_cards_from_hand", effect["op"])
                self.assertEqual(maximum, effect["selection"]["maximum"])
                self.assertEqual(tapped, effect["selection"]["tapped"])
                self.assertEqual(text[node.span.start:node.span.end], node.text)

    def test_private_collection_unsupported_neighbors_remain_material(self):
        for text in (
            "Put two creature cards from your hand onto the battlefield.",
            "Put up to X creature cards from your hand onto the battlefield.",
            "Each player puts any number of creature cards from their hand onto the battlefield.",
            "Put any number of creature cards from your graveyard onto the battlefield.",
            "Put any number of creature cards from your hand onto the battlefield face down.",
            "Put up to two creature cards from your hand onto the battlefield attacking.",
            "Put any number of creature cards with total mana value 6 or less from your hand onto the battlefield.",
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", self.compile(text).status)

    def test_collection_codec_and_compiler_omission_fail_closed(self):
        from quorune.private_hand_entry_model import PrivateHandCollectionSpec, private_hand_collection_query
        source = PrivateHandCollectionSpec(private_hand_collection_query("creature"), maximum=2).to_dict()
        for change in ({"schema_version": True}, {"maximum": True}, {"maximum": 3},
                       {"tapped": 1}, {"extra": 0}, {"query": {}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                PrivateHandCollectionSpec.from_dict({**source, **change})
        text = "Put up to two creature cards from your hand onto the battlefield."
        self.assertEqual("exact", self.compile(text).status)
        with patch("quorune.compiler.hand_entry_collection_templates.private_hand_collection_template", return_value=None):
            self.assertNotEqual("exact", self.compile(text).status)

    def test_collection_dependencies_are_required(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.private_hand_entry_model import PRIVATE_HAND_COLLECTION_CAPABILITY
        text = "Put up to two creature cards from your hand onto the battlefield."
        self.assertEqual("exact", self.compile(text).status)
        for dependency in (PRIVATE_HAND_COLLECTION_CAPABILITY, "zone.change.destination_replacement",
                           "attachment.aura.typed_restriction"):
            raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
            row = next(row for row in raw["capabilities"] if row["id"] == dependency)
            row.update(status="blocked", blockers=["private collection dependency withdrawn"])
            registry = CapabilityRegistry(raw)
            # This negative fixture isolates dependency withdrawal from an unrelated unverified-index blocker.
            registry.mark_evidence_verified(self.registry.evidence_fingerprint)
            ir = compile_oracle_card(witnesses.record(text, "Sorcery"), capability_registry=registry,
                                     capability_profile="commander_review")
            self.assertNotEqual("exact", ir.status)

    def test_private_collection_contexts_keep_complete_bodies_and_nonmana_activation(self):
        body = "Put any number of creature cards from your hand onto the battlefield."
        for text, kind in (
            ("{3}, {T}: " + body, "Artifact"),
            ("Remove a divinity counter from this creature: " + body, "Creature — Spirit"),
            ("Exhaust — {10}{R}{G}: " + body + " (Activate each exhaust ability only once.)", "Creature — Pilot"),
            ("When this creature enters, " + body, "Creature — Dinosaur"),
            ("Choose one —\n• " + body + "\n• Draw a card.", "Sorcery"),
            ("Draw a card, then put any number of creature cards from your hand onto the battlefield.", "Sorcery"),
        ):
            with self.subTest(text=text):
                ir = self.compile(text, kind)
                self.assertEqual("exact", ir.status, ir.material_residuals)


class PrivateHandCollectionRuntimeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = witnesses.BoundEffectProgramRuntimeTests.session
    add = witnesses.BoundEffectProgramRuntimeTests.add
    ready = witnesses.BoundEffectProgramRuntimeTests.ready
    checkpoint = witnesses.BoundEffectProgramRuntimeTests.checkpoint
    resolve = witnesses.BoundEffectProgramRuntimeTests.resolve
    replay = witnesses.BoundEffectProgramRuntimeTests.replay

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "private-hand.sqlite3"
        build_fixture_database([ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/private-hand-collection-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Private collection witness", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def cast(self, session, source, mana):
        action = self.ready(session, source, mana)
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        return self.resolve(session)

    def choose(self, session, refs):
        result = session.act("pilot:A", {"action_id": "choose", "cards": refs})
        self.assertTrue(result.ok, result.summary)
        return self.resolve(session)

    def settle(self, session):
        for _ in range(12):
            decision = self.resolve(session)
            if decision is None:
                return
            self.assertEqual("trigger.order", decision.kind)
            refs = [item.ref for batch in session.state.pending_trigger_batches for item in batch.items]
            principal, = session.pending_principals()
            result = session.act(principal, {"action_id": "order", "triggers": refs})
            self.assertTrue(result.ok, result.summary)
        self.fail("Collection consequences did not settle")

    def ghalta_batch(self, seed):
        session = self.session(seed)
        source = self.add(session.engine, "Ghalta, Stampede Tyrant", zone="hand")
        first = self.add(session.engine, "Generic Collection Watcher", zone="hand", ref="first-watcher")
        second = self.add(session.engine, "Generic Collection Watcher", zone="hand", ref="second-watcher")
        private = self.add(session.engine, "Generic Bound Body", seat="B", zone="hand", ref="opponent-private")
        nonmatching = self.add(session.engine, "Generic Bound Growth", zone="hand")
        initial_life = session.state.players["A"].life
        self.assertEqual("semantic.choice", self.cast(session, source, {"G": 8}).kind)
        own = str(session.packet("pilot:A", full=True)["decision"])
        self.assertIn(first.ref, own)
        self.assertNotIn(private.ref, own)
        self.assertNotIn(nonmatching.ref, own)
        self.assertNotIn(first.ref, str(session.packet("pilot:B", full=True)))
        before = authoritative_state_hash(session.state)
        for principal, refs in (("pilot:B", [first.ref]), ("pilot:A", [private.ref]),
                                ("pilot:A", [nonmatching.ref]), ("pilot:A", [first.ref, first.ref])):
            result = session.act(principal, {"action_id": "choose", "cards": refs})
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        session = self.replay(session, load=True)
        self.choose(session, [second.ref, first.ref])
        self.settle(session)
        for card in (first, second):
            self.assertEqual("battlefield", session.state.cards[card.object_id].zone)
            self.assertEqual("A", session.state.cards[card.object_id].controller)
        self.assertEqual(session.state.cards[first.object_id].zone_timestamp,
                         session.state.cards[second.object_id].zone_timestamp)
        self.assertEqual(initial_life + 2, session.state.players["A"].life)
        self.replay(session, load=True)
        return session

    def test_actual_ghalta_private_choice_enters_one_group_and_newcomers_see_each_other(self):
        self.ghalta_batch(291001)

    def test_atomic_entry_omission_mutant_is_killed_by_original_ghalta_actions(self):
        with patch("quorune.semantic_choices.simultaneous_movement.commit_simultaneous_movement", return_value=()):
            with self.assertRaises(AssertionError):
                self.ghalta_batch(291002)

    def test_bounded_selection_rejects_three_and_accepts_zero_without_leaking_hand(self):
        session = self.session(291003)
        source = self.add(session.engine, "Generic Collection Two", zone="hand")
        cards = [self.add(session.engine, "Generic Bound Body", zone="hand", ref=f"candidate-{i}") for i in range(3)]
        self.assertEqual("semantic.choice", self.cast(session, source, {"G": 1}).kind)
        before = authoritative_state_hash(session.state)
        result = session.act("pilot:A", {"action_id": "choose", "cards": [card.ref for card in cards]})
        self.assertFalse(result.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.choose(session, [])
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in cards))
        self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
        self.replay(session, load=True)

    def test_any_number_lands_enter_tapped_and_empty_matching_hand_auto_continues(self):
        for count in (0, 3):
            with self.subTest(count=count):
                session = self.session(291010 + count)
                # Remove the incidental opening lands so the count is the independent premise.
                for oid in tuple(session.state.players["A"].zones["hand"]):
                    session.engine.move_card(oid, "library", log=False)
                source = self.add(session.engine, "Generic Collection Lands", zone="hand")
                cards = [self.add(session.engine, "Generic Bound Plains", zone="hand", ref=f"land-{i}") for i in range(count)]
                decision = self.cast(session, source, {"G": 1})
                if count:
                    self.assertEqual("semantic.choice", decision.kind)
                    self.choose(session, [card.ref for card in cards])
                    self.assertTrue(all(session.state.cards[card.object_id].tapped for card in cards))
                    self.assertEqual(1, len({session.state.cards[card.object_id].zone_timestamp for card in cards}))
                else:
                    self.assertIsNone(decision)
                self.assertEqual("graveyard", session.state.cards[source.object_id].zone)
                self.replay(session, load=True)

    def test_permanent_batch_collects_aura_choices_before_any_member_moves(self):
        session = self.session(291020)
        source = self.add(session.engine, "Generic Collection All", zone="hand")
        body = self.add(session.engine, "Generic Bound Body", seat="B", ref="existing-body")
        entrant = self.add(session.engine, "Generic Bound Body", zone="hand", ref="entering-body")
        auras = [self.add(session.engine, "Generic Collection Aura", zone="hand", ref=f"entering-aura-{i}") for i in range(2)]
        self.assertEqual("semantic.choice", self.cast(session, source, {"G": 1}).kind)
        self.assertEqual("aura.entry", self.choose(session, [entrant.ref, *(aura.ref for aura in auras)]).kind)
        for index in range(2):
            self.assertEqual("hand", session.state.cards[entrant.object_id].zone)
            self.assertTrue(all(session.state.cards[aura.object_id].zone == "hand" for aura in auras))
            packet = str(session.packet("pilot:A", full=True)["decision"])
            self.assertNotIn(entrant.ref, packet)
            session = self.replay(session, load=True)
            result = session.act("pilot:A", {"action_id": "choose", "target": body.ref})
            self.assertTrue(result.ok, result.summary)
            self.resolve(session)
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.assertTrue(all(session.state.cards[aura.object_id].attached_to == body.object_id for aura in auras))
        self.assertEqual(4, session.engine._numeric_stat(body.object_id, "power"))
        self.replay(session, load=True)

    def test_simultaneously_entering_scales_does_not_replace_other_entrants_counters(self):
        for existing, expected in ((False, 1), (True, 2)):
            with self.subTest(existing=existing):
                session = self.session(291030 + existing)
                source = self.add(session.engine, "Generic Collection All", zone="hand")
                scales = self.add(session.engine, "Hardened Scales", zone="battlefield" if existing else "hand")
                entrant = self.add(session.engine, "Generic Collection Counter", zone="hand")
                self.cast(session, source, {"G": 1})
                self.choose(session, [entrant.ref] if existing else [scales.ref, entrant.ref])
                self.assertEqual(expected, session.state.cards[entrant.object_id].counters.get("+1/+1", 0))
                self.replay(session, load=True)

    def test_actual_myojin_cast_from_hand_pays_divinity_counter_for_one_collection(self):
        session = self.session(291040)
        source = self.add(session.engine, "Myojin of Life's Web", zone="hand")
        entrant = self.add(session.engine, "Generic Bound Body", zone="hand")
        self.cast(session, source, {"G": 9})
        self.assertEqual(1, session.state.cards[source.object_id].counters.get("divinity", 0))
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"].startswith("activate:" + source.ref + ":"))
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(0, session.state.cards[source.object_id].counters.get("divinity", 0))
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, [entrant.ref])
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.replay(session, load=True)

    def test_actual_redshift_exhaust_is_nonmana_and_keeps_once_per_incarnation_limit(self):
        session = self.session(291041)
        source = self.add(session.engine, "Redshift, Rocketeer Chief")
        entrant = self.add(session.engine, "Generic Bound Body", zone="hand")
        self.ready(session, source, {"C": 10, "R": 1, "G": 1})
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action = next(row for row in actions if row["id"] == "activate:" + source.ref + ":ab3")
        self.checkpoint(session)
        result = session.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[entrant.object_id].zone)
        self.assertTrue(session.state.stack)
        self.assertEqual("semantic.choice", self.resolve(session).kind)
        self.choose(session, [entrant.ref])
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.replay(session, load=True)
        session.engine.state.players["A"].mana_pool.update({"C": 10, "R": 1, "G": 1})
        session.engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        self.assertFalse(any(row["id"] == action["id"] for row in actions))
        # The availability check uses an unrecorded mana setup; checkpoint before its own save/load.
        self.checkpoint(session)
        self.replay(session, load=True)

    def test_private_entry_group_waits_for_copy_replacement_and_replays_before_commit(self):
        session = self.session(291050)
        source = self.add(session.engine, "Generic Collection All", zone="hand")
        existing = self.add(session.engine, "Generic Bound Body", seat="B", ref="existing-copy-body")
        clone = self.add(session.engine, "Clone", zone="hand")
        entrant = self.add(session.engine, "Generic Collection Counter", zone="hand")
        self.cast(session, source, {"G": 1})
        self.assertEqual("replacement.order", self.choose(session, [clone.ref, entrant.ref]).kind)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in (clone, entrant)))
        session = self.replay(session, load=True)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        self.assertFalse(any(entrant.ref in option["id"] for option in options))
        selected = next(option["id"] for option in options
                        if existing.ref in option["id"] and not option["id"].startswith("decline:"))
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", {"action_id": "choose", "replacement": selected}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "replacement": selected})
        self.assertTrue(result.ok, result.summary)
        self.resolve(session)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "battlefield" for card in (clone, entrant)))
        self.assertEqual(1, session.state.cards[entrant.object_id].counters.get("+1/+1", 0))
        self.assertEqual(session.engine._numeric_stat(existing.object_id, "power"),
                         session.engine._numeric_stat(clone.object_id, "power"))
        self.replay(session, load=True)

    def test_aura_without_existing_enchantable_object_stays_in_hand_while_group_enters(self):
        session = self.session(291051)
        source = self.add(session.engine, "Generic Collection All", zone="hand")
        aura = self.add(session.engine, "Generic Collection Aura", zone="hand")
        entrant = self.add(session.engine, "Generic Bound Body", zone="hand")
        self.cast(session, source, {"G": 1})
        self.assertIsNone(self.choose(session, [aura.ref, entrant.ref]))
        self.assertEqual("hand", session.state.cards[aura.object_id].zone)
        self.assertEqual("battlefield", session.state.cards[entrant.object_id].zone)
        self.replay(session, load=True)


if __name__ == "__main__":
    unittest.main()
