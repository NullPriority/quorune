"""CR 608.2c/h, 701.13a and 712.8a for quantities from actual public exile."""

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
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.public_exile_result_model import PUBLIC_EXILE_RESULT_CAPABILITY, PublicExileFollowup
from scripts.build_test_database import build_fixture_database
from test_bound_effect_programs import record
import test_bound_effect_programs as bound_actions
import test_composition_commander_departure as composition_controls


class PublicExileFollowupCompilerTests(unittest.TestCase):
    def test_public_exile_followup_keeps_target_scope_and_printed_span(self):
        text = "Exile target opponent's graveyard. For each creature card exiled this way, create a 1/1 colorless Spirit creature token."
        ir = compile_oracle_card(record(text, "Instant"), capability_registry=load_default_capability_registry())
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual((0, len(text)), (node.span.start, node.span.end))
        self.assertIn(PUBLIC_EXILE_RESULT_CAPABILITY, node.capability_dependencies)
        self.assertEqual("opponent", node.target_schema["player_relation"])
        effect, = node.effects
        followup = PublicExileFollowup.from_dict(effect["followup"])
        self.assertEqual(("graveyard",), followup.query.zones)
        self.assertEqual(("creature",), followup.query.types_all)
        self.assertIs(followup.query.token, False)

    def test_public_exile_followup_rejects_hidden_or_unbound_count_scopes(self):
        registry = load_default_capability_registry()
        for text in (
            "Exile target creature. Draw a card for each creature card exiled this way.",
            "Exile target player's hand. You gain 1 life for each card exiled this way.",
            "Exile all graveyards. You gain 1 life for each card exiled this way.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(record(text, "Sorcery"), capability_registry=registry)
                self.assertNotEqual("exact", ir.status)
        ir = compile_oracle_card(record(
            "Exile target player's graveyard. You gain 1 life for each card exiled this way.",
        ), capability_registry=registry)
        descriptor = ir.faces[0].nodes[0].effects[0]["followup"]
        for key, value in (
            ("schema_version", True), ("effect", {"op": "life", "delta": 1, "player": "B"}),
            ("query", {**descriptor["query"], "zones": ["hand"]}),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                PublicExileFollowup.from_dict({**descriptor, key: value})

    def test_public_exile_followup_withdrawal_retains_plain_graveyard_exile(self):
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf8"))
        capability = next(row for row in raw["capabilities"] if row["id"] == PUBLIC_EXILE_RESULT_CAPABILITY)
        capability.update(status="blocked", blockers=["Committed public exile quantity withdrawn"])
        registry = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        evidence = build_index(registry_value=raw, declaration_source=declarations,
                               discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
        plain = "Exile target player's graveyard."
        counted = plain + " You gain 1 life for each card exiled this way."
        ir = compile_oracle_card(record(counted), capability_registry=registry)
        self.assertNotEqual("exact", ir.status)
        self.assertTrue(any(PUBLIC_EXILE_RESULT_CAPABILITY in blocker
                            for residual in ir.material_residuals for blocker in residual.blockers))
        self.assertEqual("exact", compile_oracle_card(record(plain), capability_registry=registry).status)

    def test_public_exile_followup_committed_quantity_mutation_is_killed(self):
        ir = compile_oracle_card(record(
            "Exile target player's graveyard. You gain 1 life for each card exiled this way.",
        ), capability_registry=load_default_capability_registry())
        spec = PublicExileFollowup.from_dict(ir.faces[0].nodes[0].effects[0]["followup"])

        def witness():
            self.assertEqual((), spec.from_committed_count(0).effects)
            self.assertEqual(3, spec.from_committed_count(3).effects[0]["delta"])

        witness()
        original = PublicExileFollowup.from_committed_count
        with patch.object(PublicExileFollowup, "from_committed_count",
                          lambda current, count: original(current, int(bool(count)))):
            with self.assertRaises(AssertionError):
                witness()


class PublicExileFollowupNativeTests(unittest.TestCase):
    register_fixture_cards_on_add = True
    session = bound_actions.BoundEffectProgramRuntimeTests.session
    add = bound_actions.BoundEffectProgramRuntimeTests.add
    checkpoint = bound_actions.BoundEffectProgramRuntimeTests.checkpoint
    resolve = bound_actions.BoundEffectProgramRuntimeTests.resolve
    replay = bound_actions.BoundEffectProgramRuntimeTests.replay
    priority = composition_controls.CompositionCommanderDepartureTests.priority
    cast = composition_controls.CompositionCommanderDepartureTests.cast
    assert_closed = composition_controls.CompositionCommanderDepartureTests.assert_closed

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "public-exile-followups.sqlite3"
        fixtures = (
            "bound-effect-program-cards", "creature-power-multiple-cards",
            "static-characteristic-setting-cards", "public-exile-followup-cards",
        )
        build_fixture_database([ROOT / "tests/fixtures" / f"{name}.json" for name in fixtures], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Committed public exile", [
            DeckEntry("Generic Bound Commander", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Generic Bound Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def _tokens(self, session, name):
        return [
            card for card in session.state.cards.values()
            if card.zone == "battlefield" and card.is_token and card.printed_name == name
        ]

    def _choose_player(self, session, player):
        self.assertEqual("semantic.target", self.resolve(session).kind)
        session = self.replay(session, load=True)
        before = authoritative_state_hash(session.state)
        command = {"action_id": "choose", "targets": [player]}
        self.assertFalse(session.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        accepted = session.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        return session

    def test_actual_release_to_memory_counts_only_the_opponents_front_creatures(self):
        session = self.session(712_080_921)
        engine = session.engine
        spell = self.add(engine, "Release to Memory", zone="hand")
        creature = self.add(engine, "Grizzly Bears", seat="B", zone="graveyard", ref="exile-result-creature")
        modal_creature = self.add(engine, "Kazandu Mammoth // Kazandu Valley", seat="B", zone="graveyard")
        modal_spell = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", seat="B", zone="graveyard")
        own = self.add(engine, "Grizzly Bears", zone="graveyard", ref="exile-result-own")
        private = self.add(engine, "Generic Bound Growth", seat="C", zone="hand")
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 3, "W": 1})
        self.checkpoint(session)
        offered = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                       if row.get("card") == spell.ref)
        self.assertIn("B", offered["target_schema"]["legal_refs"])
        self.assertNotIn("A", offered["target_schema"]["legal_refs"])
        before = authoritative_state_hash(session.state)
        self.assertFalse(session.act("pilot:A", {"action_id": offered["id"], "targets": ["A"], "pay": "auto"}).ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, "A", spell, targets=("B",))
        self.assertIsNone(self.resolve(session))
        self.assertEqual(["A", "A"], [card.controller for card in self._tokens(session, "Spirit")])
        for card in (creature, modal_creature, modal_spell):
            self.assertEqual("exile", session.state.cards[card.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[own.object_id].zone)
        self.assertNotIn(private.ref, json.dumps(session.packet("pilot:B", full=True)))
        self.replay(session)

    def test_actual_canoptek_counts_dual_types_once_and_excludes_the_modal_back_land(self):
        session = self.session(712_080_922)
        engine = session.engine
        spell = self.add(engine, "Canoptek Scarab Swarm", zone="hand")
        land_artifact = self.add(engine, "Darksteel Citadel", seat="B", zone="graveyard")
        artifact = self.add(engine, "Ornithopter", seat="B", zone="graveyard")
        modal = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", seat="B", zone="graveyard")
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 4})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        session = self._choose_player(session, "B")
        tokens = self._tokens(session, "Insect")
        self.assertEqual(["A", "A"], [card.controller for card in tokens])
        self.assertTrue(all("flying" in session.engine._combat_keywords(card) for card in tokens))
        for card in (land_artifact, artifact, modal):
            self.assertEqual("exile", session.state.cards[card.object_id].zone)
        self.replay(session)

    def test_actual_necromancers_covenant_moves_only_creature_cards_then_creates_zombies(self):
        session = self.session(712_080_923)
        engine = session.engine
        spell = self.add(engine, "Necromancer's Covenant", zone="hand")
        creature = self.add(engine, "Kazandu Mammoth // Kazandu Valley", seat="B", zone="graveyard")
        other = self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", seat="B", zone="graveyard")
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 3, "W": 1, "B": 2})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        session = self._choose_player(session, "B")
        token, = self._tokens(session, "Zombie")
        self.assertEqual("A", token.controller)
        self.assertIn("lifelink", session.engine._combat_keywords(token))
        self.assertEqual("exile", session.state.cards[creature.object_id].zone)
        self.assertEqual("graveyard", session.state.cards[other.object_id].zone)
        self.replay(session)

    def test_actual_malanthrope_counts_own_graveyard_then_uses_the_source_incarnation(self):
        session = self.session(712_080_924)
        engine = session.engine
        spell = self.add(engine, "Malanthrope", zone="hand")
        self.add(engine, "Grizzly Bears", zone="graveyard", ref="exile-own-bear")
        self.add(engine, "Kazandu Mammoth // Kazandu Valley", zone="graveyard")
        self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", zone="graveyard")
        foreign = self.add(engine, "Grizzly Bears", seat="B", zone="graveyard", ref="exile-other-bear")
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 1, "G": 1, "U": 1})
        self.checkpoint(session)
        self.cast(session, "A", spell)
        session = self._choose_player(session, "A")
        self.assertEqual(2, session.state.cards[spell.object_id].counters.get("+1/+1"))
        self.assertEqual("graveyard", session.state.cards[foreign.object_id].zone)
        self.replay(session)

    def test_actual_titanias_command_binds_exile_life_and_continues_the_second_mode(self):
        session = self.session(712_080_925)
        engine = session.engine
        spell = self.add(engine, "Titania's Command", zone="hand")
        exiled = [
            self.add(engine, "Grizzly Bears", seat="B", zone="graveyard", ref="titania-held-creature"),
            self.add(engine, "Bala Ged Recovery // Bala Ged Sanctuary", seat="B", zone="graveyard"),
        ]
        self.assert_closed(session, spell)
        self.priority(session, "A", {"C": 4, "G": 2})
        self.checkpoint(session)
        before = session.state.players["A"].life
        offered = next(row for row in session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                       if row.get("card") == spell.ref)
        accepted = session.act("pilot:A", {"action_id": offered["id"], "modes": ["mode_1", "mode_3"],
                                         "targets": ["B"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(session))
        self.assertEqual(before + 2, session.state.players["A"].life)
        self.assertEqual(["A", "A"], [card.controller for card in self._tokens(session, "Bear")])
        for card in exiled:
            self.assertEqual("exile", session.state.cards[card.object_id].zone)
        self.replay(session)
