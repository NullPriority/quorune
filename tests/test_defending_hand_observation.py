"""Pinned CR 509.3c and 701.20e: one blocked attacker and one private look."""

from copy import deepcopy
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
from quorune.rules.hand_inspection import (
    DEFENDING_HAND_OBSERVATION_CAPABILITY as CAPABILITY,
    DEFENDING_HAND_OBSERVATION_VERSION_FIELD,
    defending_hand_observation_effect,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


NAME = "Port Inspector"
PROFILE = "commander_review"
LEGACY = "Generic Hand Look Fixture"
FIXTURES = ROOT / "tests/fixtures"
SOURCE_CARDS = FIXTURES / "defending-hand-observation-cards.json"


class DefendingHandObservationTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base_cards = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base_cards["cards"] = [card for card in base_cards["cards"] if card["name"] == "Generic Bound Plains"]
        base_cards["rulings"] = []
        old_cards = json.loads((FIXTURES / "fixed-hand-inspection-cards.json").read_bytes())
        base_cards["cards"].extend(card for card in old_cards["cards"] if card["name"] == LEGACY)
        base = folder / "base.json"
        base.write_text(json.dumps(base_cards), encoding="utf-8")
        db_path = folder / "defending-hand-observation.sqlite3"
        build_fixture_database([base, SOURCE_CARDS], db_path)
        cls.db = CardDatabase(db_path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Defending hand", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def program(self):
        record = self.db.lookup(NAME, fuzzy=False)
        admission, semantics = register_card(self.db, self.registry, NAME)
        self.assertTrue(admission["strict_capability_ready"], admission["blockers"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        return record, program

    def test_original_and_nearby_unsupported_grammar(self):
        record, program = self.program()
        self.assertEqual(("creature.becomes_blocked", "battlefield"), (program.event, program.active_zone))
        self.assertEqual({"field": "card", "op": "eq", "value": "$source.ref"}, program.event_condition)
        self.assertIsNone(program.target_schema)
        self.assertIn(CAPABILITY, program.capability_dependencies)
        self.assertEqual([{
            "op": "offer_optional_effect", "player": "$controller",
            "effects": [defending_hand_observation_effect()],
        }], list(program.effects))
        for text in (
            "Whenever this creature attacks, you may look at defending player's hand.",
            "Whenever this creature becomes blocked by a creature, you may look at defending player's hand.",
            "Whenever this creature becomes blocked, look at defending player's hand.",
            "Whenever this creature becomes blocked, you may reveal defending player's hand.",
            "Whenever this creature becomes blocked, you may look at defending player's hand. Rewrite history.",
        ):
            with self.subTest(text=text):
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertNotEqual("exact", ir.status)

    def test_shape_cannot_change_player_visibility_origin_or_omit_dependency(self):
        _, program = self.program()
        effect, = program.effects
        child, = effect["effects"]
        for changed in (
            replace(program, event="creature.attacks"),
            replace(program, active_zone="graveyard"),
            replace(program, event_condition=None),
            replace(program, target_schema={"count": 1}),
            replace(program, effects=(child,)),
            replace(program, effects=({**effect, "effects": [{**child, "inspection": "reveal"}]},)),
            replace(program, effects=({**effect, "effects": [{**child, "target": "$controller"}]},)),
            replace(program, effects=({**effect, "effects": [
                {**child, DEFENDING_HAND_OBSERVATION_VERSION_FIELD: True},
            ]},)),
        ):
            with self.subTest(changed=changed.to_dict()):
                self.assertFalse(bind_semantic_program_runtime(
                    changed, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
        deps = [dep for dep in program.capability_dependencies if dep != CAPABILITY]
        changed = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        result = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(result["strict"], result)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, result["blockers"])

    def board(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, NAME)
        other = self.add(game.engine, "Grizzly Bears", ref="other-attacker")
        walker = self.add(game.engine, "Ral, Caller of Storms", seat="B")
        walker.counters["loyalty"] = 5
        blockers = tuple(self.add(game.engine, "Phyrexian Walker", seat=seat, ref=f"blocker-{i}")
                         for i, seat in enumerate("BBC"))
        private = self.add(game.engine, "Counterspell", seat="B", zone="hand", ref="defender-private")
        unrelated = self.add(game.engine, "Shock", seat="C", zone="hand", ref="other-private")
        draw = self.add(game.engine, "Reach Through Mists", seat="B", zone="hand", ref="draw-response")
        bounce = self.add(game.engine, "Unsummon", zone="hand")
        for seat in "AB":
            self.add(game.engine, "Island", seat=seat, ref=f"island-{seat}")
        for creature in (source, other):
            creature.acquired_control_turn_count = -1
        for card in (source, draw, bounce):
            self.assert_closed(game, card)
        self.prepare(game)
        self.checkpoint(game)
        self.attack(game, [(source, walker.ref), (other, "C")])
        self.until(game, lambda session: session.state.pending_decision.kind == "combat.blockers")
        for seat, assignments in (
            ("B", {blockers[0].ref: source.ref, blockers[1].ref: source.ref}),
            ("C", {blockers[2].ref: other.ref}),
        ):
            accepted = game.act(f"pilot:{seat}", {"action_id": "block", "blocks": assignments})
            self.assertTrue(accepted.ok, accepted.summary)
        self.until(game, lambda session: self.trigger_count(session, source) != 0)
        self.assertEqual(1, len(game.state.stack))
        self.assertEqual(1, self.trigger_count(game, source))
        return game, source, private, unrelated, draw, bounce

    def assert_private(self, game, expected, shadow, *, accepted):
        for identity in ("pilot:A", "pilot:C", "pilot:D", "spectator"):
            packet = json.dumps(game.packet(identity, full=True))
            for ref in expected:
                if accepted and identity == "pilot:A":
                    self.assertIn(ref, packet)
                else:
                    self.assertNotIn(ref, packet)
            if identity != "pilot:C":
                self.assertNotIn(shadow, packet)

    def test_actual_blocked_trigger_looks_only_at_current_defender_after_reload(self):
        game, source, private, unrelated, draw, bounce = self.board(509370120)
        initial = set(game.state.players["B"].zones["hand"])
        self.cast_for(game, "A", bounce, [source.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self.cast_for(game, "B", draw)
        self.resolve_top(game)
        hand = tuple(game.state.players["B"].zones["hand"])
        added, = set(hand) - initial
        self.assertNotIn(draw.object_id, hand)
        private_refs = (private.ref, game.state.cards[added].ref)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        self.assert_private(game, private_refs, unrelated.ref, accepted=False)
        command = {"action_id": "choose", "choice": "apply"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:C", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        resumed = self.replay(game, load=True)
        declined = game.act("pilot:A", {"action_id": "choose", "choice": "decline"})
        self.assertTrue(declined.ok, declined.summary)
        self.assert_private(game, private_refs, unrelated.ref, accepted=False)
        self.replay(game)
        accepted = resumed.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual(hand, tuple(resumed.state.players["B"].zones["hand"]))
        self.assert_private(resumed, private_refs, unrelated.ref, accepted=True)
        restored = self.replay(resumed, load=True)
        self.assert_private(restored, private_refs, unrelated.ref, accepted=True)

    def test_departed_defender_leaves_no_impossible_choice_or_look(self):
        game, _, private, unrelated, _, _ = self.board(509370121)
        self.priority(game, "B")
        accepted = game.act("pilot:B", {"action_id": "concede", "choices": {"confirm_concede": True}})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.state.stack)
        self.assert_private(game, (private.ref,), unrelated.ref, accepted=False)
        self.replay(game)

    def test_actual_private_witness_kills_public_inspection_mutant(self):
        from quorune.semantic_choices import intent_host

        game, _, private, unrelated, _, _ = self.board(509370122)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, list(pending.actors)))
        commit = intent_host.commit_hand_inspection
        with patch.object(intent_host, "commit_hand_inspection",
                          side_effect=lambda host, intent: commit(host, replace(intent, public=True))):
            accepted = game.act("pilot:A", {"action_id": "choose", "choice": "apply"})
        self.assertTrue(accepted.ok, accepted.summary)
        with self.assertRaises(AssertionError):
            self.assert_private(game, (private.ref,), unrelated.ref, accepted=True)

    def test_verified_withdrawal_preserves_targeted_hand_look_and_combat(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        registry_path = ROOT / "quorune/rules/capability-registry.json"
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        discovered = set(discover_tests(ROOT).values())
        source = json.loads(registry_path.read_bytes())
        for missing in (CAPABILITY, "effect.choice.optional_fixed", "trigger.event.normalized_public_action"):
            with self.subTest(missing=missing):
                raw = deepcopy(source)
                next(row for row in raw["capabilities"] if row["id"] == missing).update(
                    status="blocked", blockers=["Defending hand dependency withdrawn"])
                registry = CapabilityRegistry(raw)
                evidence = build_index(registry_value=raw, declaration_source=declarations,
                                       discovered_test_ids=discovered)
                registry.mark_evidence_verified(validate_capability_evidence_index(evidence, registry=registry))
                self.assertIn("status:" + missing + ":blocked", registry.closure((missing,), profile=PROFILE).blockers)
                for name in (NAME, LEGACY, "Grizzly Bears"):
                    admission, _ = register_card(self.db, registry, name)
                    self.assertEqual(name != NAME, admission["strict_capability_ready"], admission["blockers"])
