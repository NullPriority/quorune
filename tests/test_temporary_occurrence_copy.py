"""An optional temporary copy reads the exact original creature entry occurrence."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.temporary_occurrence_copy import (
    TEMPORARY_OCCURRENCE_COPY_CAPABILITY as CAPABILITY, temporary_occurrence_copy_condition_matches,
    temporary_occurrence_copy_node_capabilities,
)
from quorune.token_copy_references import COPY_REFERENCE_CONTEXT
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


RENEGADE = "Renegade Doppelganger"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class TemporaryOccurrenceCopyTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "temporary-copy-subject-cards.json": {"Cytoshape"},
            "temporary-creature-copy-set-cards.json": {"Mirrorweave"},
            "bound-effect-program-cards.json": {"Generic Bound Plains", "Unsummon"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Island"},
            "creature-subtype-addition-cards.json": {"Goblin Piker", "Isamaru, Hound of Konda", "Vedalken Orrery"},
            "graveyard-mixed-target-cards.json": {"Spectral Sailor"},
            "fixed-direct-phasing-cards.json": {"Reality Ripple"},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
            "copied-aura-token-entry-cards.json": {"Parallel Lives"},
        }
        parts = [FIXTURES / "temporary-occurrence-copy-cards.json"]
        for name, names in support.items():
            doc = json.loads((FIXTURES / name).read_bytes())
            doc["cards"] = [r for r in doc["cards"] if r["name"] in names]
            assert {r["name"] for r in doc["cards"]} == names, name
            ids = {r["oracle_id"] for r in doc["cards"]}
            doc["rulings"] = [r for r in doc.get("rulings", ()) if r["oracle_id"] in ids]
            part = folder / name
            part.write_text(json.dumps(doc), encoding="utf-8")
            parts.append(part)
        database = folder / "temporary-occurrence-copy.sqlite3"
        build_fixture_database(parts, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary occurrence copy", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_original_binds_its_exact_other_controlled_entry_and_optional_copy(self):
        card = self.db.lookup(RENEGADE, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        report, registry = register_card(self.db, self.registry, RENEGADE)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = [p for p in registry.programs_for_oracle(card.oracle_id) if CAPABILITY in p.capability_dependencies]
        self.assertEqual(("creature.enter", "battlefield"), (program.event, program.active_zone))
        self.assertTrue(temporary_occurrence_copy_condition_matches(program.event_condition))
        self.assertIsNone(program.target_schema)
        effect, = program.effects
        for update in ({"schema_version": True}, {"receiver": "$target.0"}, {"player": "$context.controller"},
                       {"copy_spec": {**effect["copy_spec"], "origin": "target"}}):
            self.assertFalse(temporary_occurrence_copy_node_capabilities(
                effects=({**effect, **update},), target_schema=None, mechanic_ids=program.coverage,
            ))
        malformed = replace(program, event_condition={"field": "controller", "op": "eq", "value": "$source.controller"})
        rejected = bind_semantic_program_runtime(malformed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(rejected["strict"])
        self.assertTrue(any("temporary_occurrence_copy" in blocker for blocker in rejected["blockers"]), rejected)

    def _start(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)
        self.checkpoint(game)

    def _command(self, game, command):
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        result = game.act("pilot:A", command)
        self.assertTrue(result.ok, result.summary)

    def _offer(self, game):
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        action, = [row for row in pending.payload_by_actor["A"]["legal_actions"] if row["id"] == "choose"]
        self.assertEqual(["apply", "decline"], list(action["choice_schema"]["legal_values"]))
        for seat in "BCD":
            self.assertIsNone(game.packet("pilot:" + seat, full=True)["decision"])

    def _name(self, game, card):
        return game.engine._effective_card_data(game.state.cards[card.object_id])["name"]

    def test_actual_own_entry_can_be_declined_and_self_and_foreign_entries_create_no_offer(self):
        game = self.session(70721020)
        source = self.add(game.engine, RENEGADE, zone="hand")
        own = self.add(game.engine, "Goblin Piker", zone="hand")
        foreign = self.add(game.engine, "Spectral Sailor", seat="B", zone="hand")
        private = self.add(game.engine, "Grizzly Bears", seat="C", zone="hand", ref="renegade-private-hand")
        self.add(game.engine, "Island", seat="B")
        self._start(game, (source, foreign), {"C": 2, "U": 1, "R": 1})
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", foreign)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", own)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self._offer(game)
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        game = self.replay(game, load=True)
        self._command(game, {"action_id": "choose", "choice": "decline"})
        self.assertEqual(RENEGADE, self._name(game, source))
        self.assertFalse(any(event.code == "permanent.copy" for event in game.state.events))
        self.replay(game, load=True)

    def test_actual_copy_uses_the_current_entrant_or_its_immediate_departure_values(self):
        for index, response in enumerate((None, "Unsummon", "Reality Ripple")):
            with self.subTest(response=response):
                game = self.session(70721023 + index)
                source = self.add(game.engine, RENEGADE)
                entrant = self.add(game.engine, "Goblin Piker", zone="hand")
                later = self.add(game.engine, "Goblin Piker", zone="hand", ref="renegade-later")
                model = self.add(game.engine, "Grizzly Bears", seat="C")
                shape = self.add(game.engine, "Cytoshape", zone="hand")
                answer = self.add(game.engine, response, seat="B", zone="hand") if response else None
                self.add(game.engine, "Island", seat="B", ref="renegade-answer-first")
                self.add(game.engine, "Island", seat="B", ref="renegade-answer-second")
                self._start(game, (source, shape), {"C": 3, "G": 1, "U": 1, "R": 2})
                self.cast(game, "A", entrant)
                self.resolve_top(game)
                trigger, = game.state.stack
                self.assertEqual("Goblin Piker", trigger.context[COPY_REFERENCE_CONTEXT]["event_object"]["snapshot"]["characteristics"]["name"])
                self.cast(game, "A", shape, [entrant.ref])
                self.assertEqual("semantic.choice", self.resolve(game).kind)
                self._command(game, {"action_id": "choose", "card": model.ref})
                self.assertEqual("Grizzly Bears", self._name(game, entrant))
                if answer is not None:
                    self.cast(game, "B", answer, [entrant.ref])
                    self.resolve_top(game)
                    trigger, = game.state.stack
                    self.assertEqual("Grizzly Bears", trigger.context[COPY_REFERENCE_CONTEXT]["event_object"]["snapshot"]["characteristics"]["name"])
                    current = game.state.cards[entrant.object_id]
                    self.assertTrue(current.zone == "hand" if response == "Unsummon" else current.phased_out)
                game = self.replay(game, load=True)
                self._offer(game)
                game = self.replay(game, load=True)
                self._command(game, {"action_id": "choose", "choice": "apply"})
                self.assertEqual("Grizzly Bears", self._name(game, source))
                self.assertEqual(source.logical_object_id, game.state.cards[source.object_id].logical_object_id)
                self.cast(game, "A", later)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("Grizzly Bears", self._name(game, source))
                self.assertEqual(0, self.trigger_count(game, source))
                self.replay(game, load=True)

    def test_actual_receiver_reentry_cannot_inherit_the_preceding_copy_offer(self):
        game = self.session(70721027)
        source = self.add(game.engine, RENEGADE)
        own = self.add(game.engine, "Goblin Piker", zone="hand")
        orrery = self.add(game.engine, "Vedalken Orrery")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        self._start(game, (source, orrery), {"C": 2, "R": 1, "U": 1})
        original = source.logical_object_id
        self.cast(game, "A", own)
        self.resolve_top(game)
        self.assertEqual(1, self.trigger_count(game, source))
        self.cast(game, "B", bounce, [source.ref])
        self.resolve_top(game)
        self.cast(game, "A", game.state.cards[source.object_id])
        self.resolve_top(game)
        self.assertNotEqual(original, game.state.cards[source.object_id].logical_object_id)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(RENEGADE, self._name(game, source))
        self.assertFalse(any(event.code == "permanent.copy" for event in game.state.events))
        self.replay(game, load=True)

    def test_exact_copy_result_omission_preserves_the_original_trigger_residual(self):
        with patch("quorune.compiler.trigger_result_templates._temporary_occurrence_copy_result", return_value=None):
            report, _ = register_card(self.db, self.registry, RENEGADE)
            self.assertFalse(report["strict_capability_ready"], report)

    def test_genuine_occurrence_copy_withdrawal_preserves_the_direct_copy_set(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Scoped current or departure occurrence copy withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn("status:" + CAPABILITY + ":blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        report, _ = register_card(self.db, registry, RENEGADE)
        self.assertFalse(report["strict_capability_ready"], report)
        control, _ = register_card(self.db, registry, "Mirrorweave")
        self.assertTrue(control["strict_capability_ready"], control["blockers"])


    def test_complete_bramble_declares_the_other_nontoken_entry_and_original_payer(self):
        from quorune.rules.token_copy_recipients import (
            occurrence_controller_copy_effect, validate_occurrence_copy_recipient,
        )
        from quorune.token_copy_recipient_model import (
            OCCURRENCE_COPY_RECIPIENT_CAPABILITY,
            other_nontoken_creature_entry_condition,
        )

        original = self.db.lookup("Bramble Sovereign", fuzzy=False)
        report, semantics = register_card(self.db, self.registry, original.name)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        self.assertEqual(("creature.enter", "battlefield"), (program.event, program.active_zone))
        self.assertIsNone(program.target_schema)
        self.assertEqual(other_nontoken_creature_entry_condition(), program.event_condition)
        payment, = program.effects
        self.assertEqual(("offer_optional_mana_payment", "$controller"), (payment["op"], payment["player"]))
        self.assertEqual({"GENERIC": 1, "G": 1}, {key: value for key, value in payment["cost"].items() if value})
        self.assertEqual([occurrence_controller_copy_effect()], payment["effects"])
        resolved = {**occurrence_controller_copy_effect(), "controller": "A",
                    "_replacement_selections": ["selected-source"], "_runtime_source": {"stack_ref": "S1"}}
        before = dict(resolved)
        validate_occurrence_copy_recipient(resolved, actor="A")
        self.assertEqual(before, resolved)
        self.assertIn(OCCURRENCE_COPY_RECIPIENT_CAPABILITY, program.capability_dependencies)
        for condition in (None, {"field": "token", "op": "eq", "value": False},
                          {"field": "card", "op": "ne", "value": "$source.ref"}):
            with self.subTest(condition=condition):
                rejected = bind_semantic_program_runtime(
                    replace(program, event_condition=condition), capability_registry=self.registry, profile=PROFILE,
                )
                self.assertFalse(rejected["strict"], rejected)
        child, = payment["effects"]
        changed = replace(program, effects=[{**payment, "effects": [{**child, "occurrence_copy_recipient_version": True}]}])
        rejected = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(rejected["strict"], rejected)
        with patch("quorune.compiler.token_copy_templates._occurrence_controller_copy_template", return_value=None):
            omitted, _ = register_card(self.db, self.registry, original.name)
            self.assertFalse(omitted["strict_capability_ready"], omitted)

    def test_actual_bramble_excludes_its_own_entry_and_can_decline_another_entry(self):
        game = self.session(1181201)
        source = self.add(game.engine, "Bramble Sovereign", zone="hand")
        entrant = self.add(game.engine, "Ambush Viper", seat="B", zone="hand")
        self._start(game, (source, entrant), {"G": 3, "C": 3})
        game.state.players["B"].mana_pool.update({"G": 1, "C": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.assertIsNone(self.resolve(game))
        self.assertEqual([], game.state.stack)
        self.cast(game, "B", entrant)
        self.resolve_top(game)
        choice = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (choice.kind, choice.actors))
        self.assertIsNone(game.packet("pilot:B", full=True)["decision"])
        game = self.replay(game, load=True)
        before = authoritative_state_hash(game.state)
        denied = game.act("pilot:B", {"action_id": "choose", "pay": True})
        self.assertFalse(denied.ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        declined = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(any(card.is_token for card in game.state.cards.values()))
        self.assertEqual(2, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def _exchange_bramble_entrant(self, game, totem, entrant):
        self.priority(game, "C")
        actions = game.packet("pilot:C", full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = (row for row in actions if row["id"].startswith(f"activate:{totem.ref}:"))
        result = game.act("pilot:C", {"action_id": action["id"], "targets": [entrant.ref], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)
        self.resolve_top(game)

    def test_actual_bramble_pays_for_current_or_retained_foreign_creature_values(self):
        for case in ("live", "departed", "phased"):
            with self.subTest(case=case):
                game = self.session(1181210 + len(case))
                source = self.add(game.engine, "Bramble Sovereign")
                entrant = self.add(game.engine, "Ambush Viper", seat="B", zone="hand")
                totem = self.add(game.engine, "Avarice Totem", seat="C")
                private = self.add(game.engine, "Island", seat="D", zone="hand", ref="bramble-hidden")
                witnesses = [source, entrant, totem]
                if case == "departed":
                    response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                    witnesses.append(response)
                    for name in ("Doubling Season", "Parallel Lives"):
                        witnesses.append(self.add(game.engine, name, seat="C"))
                elif case == "phased":
                    response = self.add(game.engine, "Reality Ripple", seat="B", zone="hand")
                    witnesses.append(response)
                self._start(game, witnesses, {"G": 2, "C": 2})
                game.state.players["B"].mana_pool.update({"G": 1, "C": 2, "U": 1})
                game.state.players["C"].mana_pool.update({"C": 5})
                self.checkpoint(game)
                self.cast(game, "B", entrant)
                self.resolve_top(game)
                self.assertEqual("triggered_ability", game.state.stack[-1].kind)
                incarnation = game.state.cards[entrant.object_id].logical_object_id
                self._exchange_bramble_entrant(game, totem, entrant)
                current = game.state.cards[entrant.object_id]
                self.assertEqual(("C", "B", incarnation), (current.controller, current.owner, current.logical_object_id))
                if case != "live":
                    self.cast(game, "B", response, [entrant.ref])
                    self.resolve_top(game)
                    current = game.state.cards[entrant.object_id]
                    if case == "departed":
                        self.assertEqual("hand", current.zone)
                        self.assertNotEqual(incarnation, current.logical_object_id)
                    else:
                        self.assertTrue(current.phased_out)
                payment = self.resolve(game)
                self.assertEqual(("semantic.choice", ["A"]), (payment.kind, payment.actors))
                self.assertIsNone(game.packet("pilot:C", full=True)["decision"])
                self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
                game = self.replay(game, load=True)
                result = game.act("pilot:A", {"action_id": "choose", "pay": True})
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(2, sum(game.state.players["A"].mana_pool.values()))
                if case == "departed":
                    order = game.state.pending_decision
                    self.assertEqual(("replacement.order", ["C"]), (order.kind, order.actors))
                    self.assertIsNone(game.packet("pilot:A", full=True)["decision"])
                    self.assertFalse(any(card.is_token for card in game.state.cards.values()))
                    game = self.replay(game, load=True)
                    option = game.packet("pilot:C", full=True)["decision"]["ctx"]["options"][0]["id"]
                    command = {"action_id": "choose", "replacement": option}
                    before = authoritative_state_hash(game.state)
                    self.assertFalse(game.act("pilot:A", command).ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                    accepted = game.act("pilot:C", command)
                    self.assertTrue(accepted.ok, accepted.summary)
                self.assertIsNone(self.resolve(game))
                copies = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(4 if case == "departed" else 1, len(copies))
                for copy in copies:
                    self.assertEqual(("C", "C"), (copy.owner, copy.controller))
                    self.assertEqual("Ambush Viper", game.engine._effective_card_data(copy)["name"])
                    self.assertIn("deathtouch", game.engine._combat_keywords(copy))
                    self.assertFalse(copy.phased_out)
                self.replay(game, load=True)

    def test_verified_copy_recipient_withdrawal_preserves_the_temporary_occurrence_copy(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from quorune.token_copy_recipient_model import OCCURRENCE_COPY_RECIPIENT_CAPABILITY
        from scripts.update_capability_evidence import build_index, discover_tests

        capability = OCCURRENCE_COPY_RECIPIENT_CAPABILITY
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == capability).update(
            status="blocked", blockers=["Scoped occurrence token recipient withdrawal"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((capability,), profile=PROFILE)
        self.assertIn(f"status:{capability}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        rejected, _ = register_card(self.db, registry, "Bramble Sovereign")
        self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, RENEGADE)
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
