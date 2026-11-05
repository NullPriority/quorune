"""Pinned CR 121.2c, 605.1a, 701.20 and 712.8a for public top-card results."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.mana_source_discovery import mana_plan_abilities
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.revealed_library_top import REVEAL_TOP_CAPABILITY, RevealedLibraryTopSpec
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record


_TEXT = (
    "Each player reveals the top card of their library. For each nonland card revealed this way, "
    "you create a 1/1 white Spirit creature token with flying. Then each player draws a card."
)


class RevealedLibraryTopCompilerTests(unittest.TestCase):
    def test_public_top_reveal_has_the_full_source_span_and_ordered_result(self):
        text = "Parley — " + _TEXT
        ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=load_default_capability_registry())
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIsNone(node.target_schema)
        self.assertIn(REVEAL_TOP_CAPABILITY, node.capability_dependencies)
        self.assertIn("combat.block.flying", node.capability_dependencies)
        spec = RevealedLibraryTopSpec.from_effect(node.effects[0])
        token, draw = spec.from_revealed_counts(lands=1, nonlands=2).effects
        self.assertEqual(("create_token", 2), (token["op"], token["quantity"]))
        self.assertEqual({"op": "draw_each_player", "count": 1}, draw)

    def test_public_top_reveal_rejects_unsupported_or_unbound_instructions(self):
        registry = load_default_capability_registry()
        for text in (
            _TEXT.replace("Each player reveals", "Target player reveals", 1),
            _TEXT.replace("you create", "target opponent creates"),
            _TEXT.replace("nonland card", "creature card"),
            _TEXT.replace("Then each player draws a card.", "Then each player mills a card."),
        ):
            with self.subTest(text=text):
                self.assertNotEqual("exact", compile_oracle_card(record(text, "Sorcery"), capability_registry=registry).status)
        effect = compile_oracle_card(record(_TEXT, "Sorcery"), capability_registry=registry).faces[0].nodes[0].effects[0]
        descriptor = effect["followup"]
        instruction = descriptor["instructions"][0]
        for malformed in (
            {**effect, "player": "B"},
            {**effect, "followup": {**descriptor, "schema_version": True}},
            {**effect, "followup": {**descriptor, "instructions": [
                {**instruction, "effect": {**instruction["effect"], "controller": "B"}},
            ]}},
        ):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                RevealedLibraryTopSpec.from_effect(malformed)

    def test_public_top_reveal_withdrawal_retains_ordinary_draw(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == REVEAL_TOP_CAPABILITY)
        capability.update(status="blocked", blockers=["Public top-card results withdrawn"])
        registry = CapabilityRegistry(raw)
        evidence = build_index(
            registry_value=raw,
            declaration_source=read_evidence_document(ROOT / "platform/capability-evidence-declarations.json"),
            discovered_test_ids=set(discover_tests(ROOT).values()),
        )
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        ir = compile_oracle_card(record(_TEXT, "Sorcery"), capability_registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(REVEAL_TOP_CAPABILITY in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
        ordinary = compile_oracle_card(record("Each player draws a card.", "Sorcery"), capability_registry=registry)
        self.assertEqual("exact", ordinary.status)

    def test_mutation_collapsing_the_revealed_nonland_count_is_killed(self):
        registry = load_default_capability_registry()
        effect = compile_oracle_card(record(_TEXT, "Sorcery"), capability_registry=registry).faces[0].nodes[0].effects[0]
        spec = RevealedLibraryTopSpec.from_effect(effect)

        def witness():
            zero = spec.from_revealed_counts(lands=3, nonlands=0).effects
            self.assertEqual(["draw_each_player"], [effect["op"] for effect in zero])
            many = spec.from_revealed_counts(lands=1, nonlands=3).effects
            self.assertEqual(3, many[0]["quantity"])

        witness()
        original = RevealedLibraryTopSpec.from_revealed_counts
        with patch.object(RevealedLibraryTopSpec, "from_revealed_counts",
                          lambda current, *, lands, nonlands: original(current, lands=lands, nonlands=int(bool(nonlands)))):
            with self.assertRaises(AssertionError):
                witness()


class RevealedLibraryTopNativeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "revealed-library-top.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "static-characteristic-setting-cards",
            "revealed-library-top-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Public reveal", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    def _tokens(self, session, name):
        return [card for card in session.state.cards.values()
                if card.is_token and card.zone == "battlefield" and card.printed_name == name]

    def _reveal_events(self, session):
        return [event for event in session.state.events if event.code == "library.top.reveal"]

    def test_actual_rousing_counts_front_faces_and_finishes_apnap_after_dredge(self):
        session = self.session(701_200_101)
        engine = session.engine
        spell = self.add(engine, "Rousing of Souls", zone="hand")
        hidden = self.add(engine, "Generic Bound Growth", seat="C", zone="hand", ref="unrevealed-private-hand")
        self.add(engine, "Generic Bound Growth", seat="C", zone="library", ref="unrevealed-second-card")
        c_top = self.add(engine, "Generic Bound Plains", seat="C", zone="library", ref="known-c-top")
        b_top = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", seat="B", zone="library")
        a_top = self.add(engine, "Generic Bound Body", zone="library")
        loam = self.add(engine, "Life from the Loam", seat="B", zone="graveyard")
        self.assert_closed(session, spell)
        self.prepare(session, mana={"C": 2, "W": 1})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        pending = self.resolve(session)
        self.assertIsNotNone(pending)
        self.assertEqual("draw.replacement", pending.kind)
        self.assertEqual(["B"], list(pending.actors))
        events = self._reveal_events(session)
        self.assertEqual(list("ABCD"), [event.details["player"] for event in events])
        self.assertEqual("Bala Ged Recovery", events[1].details["card"])
        self.assertEqual("hand", session.state.cards[a_top.object_id].zone)
        self.assertEqual("library", session.state.cards[b_top.object_id].zone)
        self.assertEqual("library", session.state.cards[c_top.object_id].zone)
        self.assertEqual(2, len(self._tokens(session, "Spirit")))
        self.assertTrue(all("flying" in session.engine._combat_keywords(token)
                            for token in self._tokens(session, "Spirit")))

        other = session.packet("pilot:A", full=True)
        self.assertIsNone(other["decision"])
        self.assertNotIn(hidden.ref, json.dumps(other))
        self.assertNotIn(hidden.printed_name, json.dumps(other))
        self.assertNotIn("unrevealed-second-card", json.dumps(other))
        self.assertNotIn("hand", other["state"]["players"]["B"])
        self.assertIn("B revealed Bala Ged Recovery.", json.dumps(session.packet("spectator", full=True)))

        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        choose = {"action_id": "choose", "choice": loam.ref}
        self.assertFalse(session.act("pilot:C", choose).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        chosen = session.act("pilot:B", choose)
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual("hand", session.state.cards[loam.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[b_top.object_id].zone)
        self.assertEqual("hand", session.state.cards[c_top.object_id].zone)
        self.assertEqual(2, len(self._tokens(session, "Spirit")))
        self.assertEqual(4, len(self._reveal_events(session)))
        self.assertEqual(list("ACD"), [event.actor for event in session.state.events
                                        if event.code == "card.draw" and event.event_id > events[-1].event_id])
        self.replay(session)

    def test_actual_phabine_creates_before_pumping_and_ignores_empty_libraries(self):
        session = self.session(701_200_102)
        engine = session.engine
        phabine = self.add(engine, "Phabine, Boss's Confidant")
        ally = self.add(engine, "Generic Bound Body", ref="phabine-ally")
        enemy = self.add(engine, "Generic Bound Body", seat="B", ref="phabine-enemy")
        self.add(engine, "Generic Bound Body", zone="library", ref="phabine-a-reveal")
        self.add(engine, "Generic Bound Growth", seat="B", zone="library", ref="phabine-b-reveal")
        for object_id in list(session.state.players["D"].zones["library"]):
            engine.move_card(object_id, "hand", log=False)
        self.assert_closed(session, phabine)
        self.prepare(session)
        self.checkpoint(session)
        self.until(session, lambda s: any(item.source_object_id == phabine.object_id for item in s.state.stack))
        self.assertIsNone(self.resolve(session))
        citizen, = self._tokens(session, "Citizen")
        self.assertEqual(3, engine._numeric_stat(citizen.object_id, "power"))
        self.assertIn("haste", engine._combat_keywords(citizen))
        self.assertEqual(4, engine._numeric_stat(ally.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(enemy.object_id, "power"))
        self.assertEqual(list("ABC"), [event.details["player"] for event in self._reveal_events(session)])
        self.assertFalse(session.state.players["D"].in_game)
        self.replay(session)

    def test_actual_woodvine_pumps_only_the_controllers_current_attackers(self):
        session = self.session(701_200_103)
        engine = session.engine
        woodvine = self.add(engine, "Woodvine Elemental")
        attacker = self.add(engine, "Generic Bound Body", ref="woodvine-attacker")
        idle = self.add(engine, "Generic Bound Body", ref="woodvine-idle")
        self.add(engine, "Generic Bound Growth", seat="C", zone="library")
        for card in (woodvine, attacker):
            card.acquired_control_turn_count = -1
        self.assert_closed(session, woodvine)
        self.prepare(session)
        self.checkpoint(session)
        self.attack(session, [(woodvine, "B"), (attacker, "C")])
        self.assertIsNone(self.resolve(session))
        self.assertEqual(5, engine._numeric_stat(woodvine.object_id, "power"))
        self.assertEqual(3, engine._numeric_stat(attacker.object_id, "power"))
        self.assertEqual(2, engine._numeric_stat(idle.object_id, "power"))
        self.replay(session)

    def test_actual_enforcer_gets_counters_before_the_players_draw(self):
        session = self.session(701_200_104)
        engine = session.engine
        enforcer = self.add(engine, "Selvala's Enforcer", zone="hand")
        self.add(engine, "Generic Bound Growth", seat="B", zone="library", ref="enforcer-b-reveal")
        self.add(engine, "Generic Bound Body", seat="C", zone="library", ref="enforcer-c-reveal")
        self.assert_closed(session, enforcer)
        self.prepare(session, mana={"C": 3, "G": 1})
        self.checkpoint(session)
        self.cast(session, "A", enforcer)
        self.assertIsNone(self.resolve(session))
        entered = session.state.cards[enforcer.object_id]
        self.assertEqual("battlefield", entered.zone)
        self.assertEqual(2, entered.counters.get("+1/+1"))
        reveals = self._reveal_events(session)
        self.assertEqual(list("ABCD"), [event.details["player"] for event in reveals])
        counter, = [event for event in session.state.events
                    if event.code == "counter.add" and entered.object_id in event.changed_objects]
        draws = [event.event_id for event in session.state.events
                 if event.code == "card.draw" and event.event_id > reveals[-1].event_id]
        self.assertEqual(4, len(draws))
        self.assertLess(reveals[-1].event_id, counter.event_id)
        self.assertLess(counter.event_id, min(draws))
        self.replay(session)

    def test_actual_selvala_uses_the_stack_and_gains_life_once(self):
        session = self.session(701_200_105)
        engine = session.engine
        selvala = self.add(engine, "Selvala, Explorer Returned")
        selvala.acquired_control_turn_count = -1
        self.add(engine, "Generic Bound Body", zone="library", ref="selvala-a-reveal")
        self.add(engine, "Generic Bound Growth", seat="C", zone="library", ref="selvala-c-reveal")
        self.assert_closed(session, selvala)
        self.prepare(session)
        self.checkpoint(session)
        self.assertEqual((), mana_plan_abilities(engine, "A", selvala))
        action = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith("activate:" + selvala.ref + ":"))
        command = {"action_id": action["id"], "pay": "auto"}
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:B", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(1, len(session.state.stack))
        self.assertTrue(session.state.cards[selvala.object_id].tapped)
        self.assertEqual(0, session.state.players["A"].mana_pool.get("G", 0))
        self.assertEqual([], self._reveal_events(session))
        life = session.state.players["A"].life
        self.assertIsNone(self.resolve(session))
        self.assertEqual(2, session.state.players["A"].mana_pool.get("G", 0))
        self.assertEqual(life + 2, session.state.players["A"].life)
        self.assertEqual([2], [event.details["delta"] for event in session.state.events if event.code == "effect.life"])
        self.replay(session)
