"""Attack exile, Clue payment, exact links, and the live temporary copy."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.linked_clue_copy import LINKED_CLUE_COPY_CAPABILITY
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class LinkedClueCopyTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Clue Shapeshifter"
    model = "Trial Linked Copy Model"
    inspector = "Trial Clue Inspector"
    plains = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "linked-clue-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "linked-clue-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Linked Clue copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry(cls.plains, 30),
        ], [commander])

    def _board(self, seed):
        game = self.session(seed)
        source = self.add(game.engine, self.copier, ref="LINK-RECEIVER")
        model = self.add(game.engine, self.model, seat="C", zone="graveyard", ref="LINK-MODEL")
        foreign = self.add(game.engine, self.model, seat="D", zone="exile", ref="FOREIGN-EXILE")
        private = self.add(game.engine, self.model, seat="D", zone="hand", ref="PRIVATE-LINK-DECOY")
        for index in range(2):
            self.add(game.engine, self.plains, ref=f"LINK-MANA-{index}")
        source.acquired_control_turn_count = -1
        first_hand_card = game.state.players['A'].zones['hand'][0]
        ZoneTransitionOwner(game.engine).move_card(first_hand_card, 'graveyard')
        self.assert_closed(game, source)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game)
        self.checkpoint(game)
        return game, source, model, foreign, private

    def _attack_and_choose(self, game, source, model):
        self.attack(game, [(source, "B")])
        pending = game.state.pending_decision
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        picked = game.act("pilot:A", {"action_id": "choose", "targets": [model.ref]})
        self.assertTrue(picked.ok, picked.summary)

    def _clues(self, game):
        return [card for card in game.state.cards.values()
                if card.zone == "battlefield" and card.controller == "A"
                and card.is_token and "clue" in game.engine._effective_type_parts(card)[1]]

    def _pay_clue(self, game):
        clue, = self._clues(game)
        self.priority(game, "A")
        action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row["id"].startswith(f"activate:{clue.ref}:"))
        accepted = game.act("pilot:A", {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertFalse(self._clues(game))

    def test_attack_and_clue_copy_preserve_links_privacy_duration_and_exact_replay(self):
        game, source, model, foreign, private = self._board(19_770_001)
        source_id = source.logical_object_id
        self._attack_and_choose(game, source, model)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[model.object_id].zone)
        hand_before = len(game.state.players["A"].zones["hand"])
        self._pay_clue(game)
        pending = self.resolve(game)
        self.assertEqual(("semantic.choice", ["A"]), (pending.kind, pending.actors))
        view = pending.payload_by_actor["A"]
        choose, = (row for row in view["legal_actions"] if row["id"] == "choose")
        self.assertEqual([model.ref], list(choose["choice_schema"]["legal_refs"]))
        self.assertNotIn(private.ref, json.dumps(game.packet("pilot:A", full=True)))
        for viewer in "BCD":
            self.assertIsNone(game.packet("pilot:" + viewer, full=True)["decision"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", {"action_id": "choose", "cards": [foreign.ref]}).ok)
        self.assertFalse(game.act("pilot:B", {"action_id": "choose", "cards": [model.ref]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        declined = self.replay(game, load=True)
        game = self.replay(game, load=True)
        chosen = game.act("pilot:A", {"action_id": "choose", "cards": [model.ref]})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(hand_before + 1, len(game.state.players["A"].zones["hand"]))
        self.assertEqual(source_id, game.state.cards[source.object_id].logical_object_id)
        self.assertEqual(self.model, game.engine.display_name(source.object_id))
        self.assertEqual("exile", game.state.cards[model.object_id].zone)
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda session: session.state.turn_sequence > turn)
        self.assertEqual(self.copier, game.engine.display_name(source.object_id))
        self.replay(game, load=True)

        result = declined.act("pilot:A", {"action_id": "choose", "cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertIsNone(self.resolve(declined))
        self.assertEqual(self.copier, declined.engine.display_name(source.object_id))
        self.assertEqual(hand_before + 1, len(declined.state.players["A"].zones["hand"]))
        self.replay(declined, load=True)

    def test_illegal_attack_target_and_stale_exile_cannot_offer_a_copy(self):
        for depart_before_exile in (True, False):
            with self.subTest(depart_before_exile=depart_before_exile):
                game, source, model, _, _ = self._board(19_770_002 + depart_before_exile)
                self._attack_and_choose(game, source, model)
                mover = ZoneTransitionOwner(game.engine)
                if depart_before_exile:
                    mover.move_card(model.object_id, "hand")
                    self.checkpoint(game)
                    self.assertIsNone(self.resolve(game))
                    self.assertFalse(self._clues(game))
                else:
                    self.assertIsNone(self.resolve(game))
                    old = game.state.cards[model.object_id].logical_object_id
                    mover.move_card(model.object_id, "graveyard")
                    mover.move_card(model.object_id, "exile")
                    self.assertNotEqual(old, game.state.cards[model.object_id].logical_object_id)
                    self.checkpoint(game)
                    self._pay_clue(game)
                    self.assertIsNone(self.resolve(game))
                    self.assertEqual(self.copier, game.engine.display_name(source.object_id))
                self.replay(game, load=True)

    def test_reacquired_same_printing_ability_uses_its_own_pair(self):
        game=self.session(1977008)
        source=self.add(game.engine,self.copier,ref="DUPLICATE-PAIR-SOURCE")
        model=self.add(game.engine,self.copier,seat="C",zone="graveyard",ref="DUPLICATE-PAIR-MODEL")
        inspector=self.add(game.engine, self.inspector, zone="hand")
        for index in range(5):
            self.add(game.engine,self.plains,ref=f"LINK-SECOND-MANA-{index}")
        self.assert_closed(game,source)
        self.assert_closed(game,inspector)
        source.acquired_control_turn_count=-1
        game.state.config.semantic_policy='trusted_only'
        self.prepare(game,mana={'W':1})
        self.checkpoint(game)
        self.cast(game,'A',inspector)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1,len(self._clues(game)))
        self._attack_and_choose(game,source,model)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(2,len(self._clues(game)))

        def pay():
            clue=self._clues(game)[0]
            self.priority(game,'A')
            action=next(row for row in game.packet('pilot:A',full=True)['decision']['ctx']['legal']['actions']
                        if row['id'].startswith('activate:'+clue.ref+':'))
            accepted=game.act('pilot:A',{'action_id':action['id'],'pay':'auto'})
            self.assertTrue(accepted.ok,accepted.summary)
        pay()
        self.assertEqual('semantic.choice',self.resolve(game).kind)
        first=game.act('pilot:A',{'action_id':'choose','cards':[model.ref]})
        self.assertTrue(first.ok, first.summary)
        self.assertIsNone(self.resolve(game))
        game = self.replay(game, load=True)
        pay()
        self.assertIsNone(self.resolve(game), "The copied pair starts with its own empty exile links")
        self.replay(game, load=True)

    def test_printed_pair_and_verified_authority_are_required_for_both_phases(self):
        record = self.db.lookup(self.copier)
        def compiled(registry):
            return compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")

        ir = compiled(self.registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        producer, result = ir.faces[0].nodes
        self.assertEqual(("creature.attacks", "permanent.sacrificed"), (producer.event, result.event))
        self.assertIn(LINKED_CLUE_COPY_CAPABILITY, producer.capability_dependencies)
        self.assertIn(LINKED_CLUE_COPY_CAPABILITY, result.capability_dependencies)
        self.assertIsNone(result.target_schema)
        data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        row = next(row for row in data["capabilities"] if row["id"] == LINKED_CLUE_COPY_CAPABILITY)
        row.update(status="blocked", blockers=["Linked Clue copy authority withdrawn."])
        registry = CapabilityRegistry(data)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=data, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        self.assertNotEqual("exact", compiled(registry).status)
        with patch("quorune.compiler.linked_clue_copy_nodes.linked_clue_copy_source", return_value=None):
            self.assertNotEqual("exact", compiled(self.registry).status)
