"""Native face-up and entry copy choices, same-incarnation refacing and expiry."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_legendary_snow_copy_entry as entry_witnesses
import test_vanishing_rules as upkeep_witnesses
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import FACE_UP_OR_ENTRY_COPY_CAPABILITY
from quorune.entry_results import begin_face_status_copy, expire_copy_scope, record_temporary_copy
from quorune.morph import MORPH_FACE_DOWN_ANNOTATION, face_down_characteristics, is_turned_face_down_by_effect
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.face_status_copy import COPY_HISTORY_FIELD, CopyExpiry
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.zone_transitions import ZoneTransitionOwner
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class FaceStatusCopyTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Refacing Echo"
    first = "Trial Copy Rowan"
    second = "Trial Copy Crest"
    spell = "Trial Expose Reflection"
    _offer = entry_witnesses.LegendarySnowCopyEntryTests._offer

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        database = Path(cls.temporary.name) / "face-status-copy.sqlite3"
        fixtures = ROOT / "tests/fixtures"
        build_fixture_database([fixtures / "bound-effect-program-cards.json",
                                fixtures / "morph-rules-cards.json",
                                fixtures / "face-status-copy-cards.json"], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        commander = "Generic Bound Commander"
        cls.deck = DeckDefinition("Face-status copy", [
            DeckEntry(commander, 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], [commander])

    def _pick(self, game, seat, target):
        pending = game.state.pending_decision
        self.assertEqual(("replacement.order", [seat]), (pending.kind, pending.actors))
        options = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["options"]
        choice = next(row for row in options if row.get("decline")) if target is None else next(
            row for row in options if not row.get("decline") and row["id"].endswith(":" + target.ref))
        command = {"action_id": "choose", "replacement": choice["id"]}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", command).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        answer = game.act("pilot:" + seat, command)
        self.assertTrue(answer.ok, answer.summary)
        return self.resolve(game)

    def _enter(self, game):
        source = self.add(game.engine, self.copier, zone="hand")
        first = self.add(game.engine, self.first, seat="B", ref="PUBLIC-FIRST")
        second = self.add(game.engine, self.second, seat="C", ref="PUBLIC-SECOND")
        self.prepare(game, mana={"C": 3, "U": 2})
        self.checkpoint(game)
        self._offer(game, source)
        self.assertIsNone(self._pick(game, "A", first))
        return source, first, second

    def _upkeep_refacing(self, game, seat, choice):
        upkeep_witnesses.VanishingRuntimeTests.upkeep(self, game, seat=seat)
        self.checkpoint(game)
        pending = self.resolve(game)
        self.assertEqual([seat], pending.actors)
        packet = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]
        self.assertEqual("offer_optional_effect", packet["operation"])
        answer = game.act("pilot:" + seat, {"action_id": "choose", "choice": choice})
        self.assertTrue(answer.ok, answer.summary)
        self.assertIsNone(self.resolve(game))

    def test_entry_copy_upkeep_refaces_then_paid_turn_up_preserves_one_incarnation(self):
        game = self.session(708_212)
        source, first, second = self._enter(game)
        current = game.state.cards[source.object_id]
        start = current.logical_object_id, current.zone_timestamp
        self.assertEqual(self.first, game.engine.display_name(current.object_id))
        self.assertIn(COPY_HISTORY_FIELD, current.annotations)
        current.counters["+1/+1"] = 2
        current.tapped = True
        self._upkeep_refacing(game, "A", "apply")
        down = game.state.cards[source.object_id]
        self.assertTrue(is_turned_face_down_by_effect(down))
        self.assertFalse(COPY_HISTORY_FIELD in down.annotations)
        self.assertEqual(start[0], down.logical_object_id)
        self.assertGreater(down.zone_timestamp, start[1])
        self.assertEqual(2, down.counters["+1/+1"])
        self.assertTrue(down.tapped)
        self.replay(game,load=True)
        self.prepare(game,mana={"C":1,"U":1})
        self.checkpoint(game)
        offers = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        turn, = [row for row in offers if row["id"].startswith("turn-face-up:" + down.ref)]
        paid = game.act("pilot:A", {"action_id": turn["id"], "pay": "auto"})
        self.assertTrue(paid.ok,paid.summary)
        paused = game.state.cards[source.object_id]
        self.assertTrue(paused.face_down)
        self.assertGreater(sum(game.state.players["A"].mana_pool.values()),0)
        options = game.packet("pilot:A",full=True)["decision"]["ctx"]["options"]
        self.assertFalse(any(row["id"].endswith(":" + down.ref) for row in options if not row["decline"]))
        game = self.replay(game,load=True)
        self.assertIsNone(self._pick(game,"A",second))
        turned = game.state.cards[source.object_id]
        self.assertFalse(turned.face_down)
        self.assertEqual(start[0],turned.logical_object_id)
        self.assertGreater(turned.zone_timestamp,paused.zone_timestamp)
        self.assertEqual(self.second,game.engine.display_name(turned.object_id))
        self.assertEqual(2,turned.counters["+1/+1"])
        self.assertFalse(any(game.state.players["A"].mana_pool.values()))
        self.replay(game,load=True)

    def test_upkeep_may_decline_and_another_players_upkeep_does_not_offer_refacing(self):
        game = self.session(708_213)
        source, _, _ = self._enter(game)
        upkeep_witnesses.VanishingRuntimeTests.upkeep(self, game, seat="B")
        self.assertFalse(game.state.stack)
        self._upkeep_refacing(game, "A", "decline")
        still = game.state.cards[source.object_id]
        self.assertFalse(still.face_down)
        self.assertEqual(self.first,game.engine.display_name(still.object_id))
        self.assertIn(COPY_HISTORY_FIELD,still.annotations)
        self.replay(game,load=True)

    def test_spell_turn_up_is_controller_choice_and_can_copy_anonymous_public_values(self):
        game = self.session(708_214)
        spell = self.add(game.engine,self.spell,zone="hand")
        source = self.add(game.engine,self.copier,seat="B",ref="EFFECT-FACEDOWN")
        hidden = self.add(game.engine,self.first,seat="C",ref="ANONYMOUS-MODEL")
        for card,seat in ((source,"B"),(hidden,"C")):
            card.face_down=True
            card.known_to=[seat]
            card.revealed_to=[]
            card.annotations[MORPH_FACE_DOWN_ANNOTATION]=face_down_characteristics("morph")
        self.assert_closed(game,source)
        self.assert_closed(game,spell)
        self.prepare(game,mana={"C":1,"R":1})
        self.checkpoint(game)
        self.cast(game,"A",spell,[source.ref])
        pending = self.resolve(game)
        self.assertEqual(("replacement.order",["B"]),(pending.kind,pending.actors))
        packet=game.packet("pilot:D",full=True)
        self.assertFalse(self.first in json.dumps(packet),"The public offer disclosed the anonymous model")
        game=self.replay(game,load=True)
        self.assertIsNone(self._pick(game,"B",hidden))
        current=game.state.cards[source.object_id]
        self.assertFalse(current.face_down)
        self.assertEqual(source.logical_object_id,current.logical_object_id)
        data=game.engine._effective_card_data(current)
        self.assertEqual(("2","2"),(str(data["power"]),str(data["toughness"])))
        self.assertFalse(self.first in json.dumps(game.packet("pilot:D",full=True)),
                         "The copy disclosed the anonymous model")
        self.replay(game,load=True)

    def test_composed_turn_up_uses_inherited_copy_then_refaces_all_physical_parts(self):
        from test_permanent_composition import CompositionCaptureNativeTests

        game, root = CompositionCaptureNativeTests.morphed_composition_fixture(self,other_name=self.copier)
        first = self.add(game.engine,self.first,seat="B",ref="COMPOSED-MODEL")
        self.prepare(game,mana={"C":1,"W":1})
        self.checkpoint(game)
        source=game.state.cards[root.object_id]
        identity=source.logical_object_id
        topology=source.permanent_composition.topology
        offers=game.packet("pilot:A",full=True)["decision"]["ctx"]["legal"]["actions"]
        option=next(row for row in offers if row["id"].startswith("turn-face-up:"+source.ref)
                    and row["requirements"]["W"]==1)
        result=game.act("pilot:A",{"action_id":option["id"],"pay":"auto"})
        self.assertTrue(result.ok,result.summary)
        self.assertIsNone(self._pick(game,"A",first))
        shown=game.state.cards[root.object_id]
        self.assertEqual(self.first,game.engine.display_name(shown.object_id))
        self.assertTrue(all(not part.card_state["face_down"] for part in shown.permanent_composition.parts))
        self._upkeep_refacing(game,"A","apply")
        down=game.state.cards[root.object_id]
        self.assertEqual(identity,down.logical_object_id)
        self.assertEqual(topology,down.permanent_composition.topology)
        self.assertTrue(down.face_down)
        self.assertTrue(all(part.card_state["face_down"] for part in down.permanent_composition.parts))
        self.assertNotIn(COPY_HISTORY_FIELD,down.annotations)
        self.replay(game,load=True)

    def test_face_down_and_cleanup_expire_their_own_layer_in_either_order(self):
        for copy_first in ("end_of_turn","face_down"):
            with self.subTest(copy_first=copy_first):
                game=self.session(708_215 if copy_first=="face_down" else 708_216)
                card=self.add(game.engine,self.copier)
                early=self.add(game.engine,self.first)
                late=self.add(game.engine,self.second)
                a=game.engine._copyable_characteristics(early)
                b=game.engine._copyable_characteristics(late)
                if copy_first=="end_of_turn":
                    record_temporary_copy(card,a)
                    begin_face_status_copy(card,b)
                    expire_copy_scope(card,CopyExpiry.END_OF_TURN)
                    self.assertEqual(self.second,card.annotations["copy_overrides"]["name"])
                    self.assertTrue(ZoneTransitionOwner(game.engine).turn_face_down(card))
                else:
                    begin_face_status_copy(card,a)
                    record_temporary_copy(card,b)
                    self.assertTrue(ZoneTransitionOwner(game.engine).turn_face_down(card))
                    self.assertEqual(self.second,card.annotations["copy_overrides"]["name"])
                    expire_copy_scope(card,CopyExpiry.END_OF_TURN)
                self.assertNotIn("copy_overrides",card.annotations)
                self.assertNotIn(COPY_HISTORY_FIELD,card.annotations)

    def test_complete_shape_withdrawal_reopens_copy_and_keeps_the_morph_node(self):
        record=self.db.lookup(self.copier)
        def compile_card(registry):
            return compile_oracle_card(record,capability_registry=registry,capability_profile="commander_review")
        intact=compile_card(self.registry)
        self.assertEqual("exact",intact.status,intact.material_residuals)
        self.assertTrue(any(n.template_id=="intrinsic-entry-copy-v12" for f in intact.faces for n in f.nodes))
        raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        cap=next(row for row in raw["capabilities"] if row["id"]==FACE_UP_OR_ENTRY_COPY_CAPABILITY)
        cap.update(status="blocked",blockers=["Face-status copy authority withdrawn."])
        withdrawn=CapabilityRegistry(raw)
        declarations=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index=build_index(registry_value=raw,declaration_source=declarations,
                          discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index,registry=withdrawn))
        uncertain=compile_card(withdrawn)
        self.assertNotEqual("exact",uncertain.status)
        self.assertTrue(any(n.exact and "morph" in (n.template_id or "") for f in uncertain.faces for n in f.nodes))
        with patch("quorune.compiler.entry_copy_ability_nodes._face_status_copy_nodes",return_value=None):
            self.assertNotEqual("exact",compile_card(self.registry).status)
        from quorune.errors import GameRuleError
        from quorune.face_up_effect_model import source_face_down_instruction

        game=self.session(708_217)
        source=self.add(game.engine,self.first)
        before=authoritative_state_hash(game.state)
        with self.assertRaises(GameRuleError):
            game.engine.apply_effect({**source_face_down_instruction(),"card":source.ref},actor="A")
        self.assertEqual(before,authoritative_state_hash(game.state))
