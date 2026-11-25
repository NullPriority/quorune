"""Native witnesses for temporary self-copy through ordinary activation."""

from dataclasses import replace
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
from quorune.rules.temporary_self_copy import TEMPORARY_SELF_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class TemporarySelfCopyTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Copying Lens"
    creature = "Generic Bound Body"
    artifact = "Trial Signal Relic"
    destroy = "Trial Artifact Break"
    restore = "Trial Swift Rebuild"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        path = Path(cls.temporary.name) / "temporary-copy.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/bound-effect-program-cards.json",
            ROOT / "tests/fixtures/temporary-self-copy-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Temporary copy", [
            DeckEntry("Generic Bound Commander",1,"commander"), DeckEntry(cls.basic,30),
        ],["Generic Bound Commander"])

    def quote(self, game, source):
        self.priority(game, "A")
        packet = game.packet("pilot:A", full=True)
        row, = [a for a in packet["decision"]["ctx"]["legal"]["actions"]
                if a["id"].startswith("activate:"+source.ref+":")]
        return row

    def activate(self, game, source, target, *, reject=(), payment=None):
        quote = self.quote(game, source)
        command = {"action_id":quote["id"],"targets":[target.ref],"pay":"auto"}
        if payment is not None:
            command.update(pay="manual", payment=payment)
        before = authoritative_state_hash(game.state)
        for candidate in reject:
            result = game.act("pilot:A", {**command, "targets":[candidate.ref]})
            self.assertFalse(result.ok, result.summary)
            self.assertEqual(before, authoritative_state_hash(game.state))
        sent = game.act("pilot:A", command)
        self.assertTrue(sent.ok, sent.summary)
        self.assertEqual("activated_ability", game.state.stack[-1].kind)

    def test_two_temporary_copies_change_the_same_source_then_expire(self):
        game = self.session(707_201)
        source = self.add(game.engine, self.copier)
        creature = self.add(game.engine,self.creature,seat="B")
        artifact = self.add(game.engine,self.artifact,seat="C")
        hidden = self.add(game.engine,self.artifact,seat="D",zone="hand",ref="private-copy-hidden")
        decoy = self.add(game.engine,self.creature,seat="C",zone="graveyard",ref="copy-wrong-zone")
        source.counters["+1/+1"] = 1
        creature.counters["+1/+1"] = 3
        artifact.tapped = True
        self.assert_closed(game, source)
        self.assert_closed(game, artifact)
        self.prepare(game, mana={"C":4})
        self.checkpoint(game)
        quote = self.quote(game,source)
        self.assertNotIn(hidden.ref,json.dumps(quote))
        self.activate(game, source, artifact, reject=(hidden, decoy))
        self.activate(game, source, creature)
        game=self.replay(game,load=True)
        self.resolve_top(game)
        live=game.state.cards[source.object_id]
        self.assertEqual(self.creature, game.engine.display_name(live.object_id))
        self.assertEqual(3, game.engine._numeric_stat(live.object_id,"power"))
        self.assertEqual(1, live.counters["+1/+1"])
        self.assertEqual("A",live.controller)
        self.resolve_top(game)
        live=game.state.cards[source.object_id]
        self.assertEqual(self.artifact, game.engine.display_name(live.object_id))
        self.assertFalse(live.tapped)
        self.assertTrue(any(ability.mana_ability for ability in game.engine._activated_abilities(live)))
        game=self.replay(game,load=True)
        turn=game.state.turn_sequence
        self.until(game,lambda s:s.state.turn_sequence>turn)
        live=game.state.cards[source.object_id]
        self.assertEqual(self.copier,game.engine.display_name(live.object_id))
        self.assertNotIn("copy_overrides",live.annotations)
        self.assertEqual(1,live.counters["+1/+1"])
        self.replay(game,load=True)

    def test_old_copy_ability_resolves_after_its_source_reenters(self):
        game=self.session(707_202)
        source=self.add(game.engine,self.copier)
        artifact=self.add(game.engine,self.artifact,seat="C")
        removal=self.add(game.engine,self.destroy,seat="B",zone="hand")
        restore=self.add(game.engine,self.restore,zone="hand")
        original=source.logical_object_id
        self.assert_closed(game,source)
        self.assert_closed(game,removal)
        self.assert_closed(game,restore)
        game.state.players["B"].mana_pool.update({"W":1,"C":1})
        self.prepare(game,mana={"W":1,"C":2})
        self.checkpoint(game)
        self.activate(game,source,artifact,payment={"C":2})
        self.cast(game,"B",removal,[source.ref])
        self.resolve_top(game)
        current=game.state.cards[source.object_id]
        self.assertEqual("graveyard",current.zone)
        self.assertEqual(1,game.state.players["A"].mana_pool["W"])
        self.cast(game,"A",restore,[current.ref])
        self.resolve_top(game)
        current=game.state.cards[source.object_id]
        self.assertEqual("battlefield",current.zone)
        self.assertNotEqual(original,current.logical_object_id)
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(self.copier,game.engine.display_name(source.object_id))
        self.assertNotIn("copy_overrides",game.state.cards[source.object_id].annotations)
        self.replay(game,load=True)

    def test_copy_target_revalidation_uses_its_current_public_object(self):
        game=self.session(707_203)
        source=self.add(game.engine,self.copier)
        artifact=self.add(game.engine,self.artifact,seat="C")
        removal=self.add(game.engine,self.destroy,seat="B",zone="hand")
        self.assert_closed(game,source)
        self.assert_closed(game,removal)
        game.state.players["B"].mana_pool.update({"W":1,"C":1})
        self.prepare(game,mana={"C":2})
        self.checkpoint(game)
        self.activate(game,source,artifact)
        self.cast(game,"B",removal,[artifact.ref])
        self.resolve_top(game)
        game=self.replay(game,load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard",game.state.cards[artifact.object_id].zone)
        self.assertEqual(self.copier,game.engine.display_name(source.object_id))
        self.replay(game,load=True)

    def test_temporary_copy_authority_withdrawal_is_local_to_its_exact_shape(self):
        row=self.db.lookup(self.copier)
        def compile(record, registry):
            return compile_oracle_card(record,capability_registry=registry,capability_profile="commander_review")
        actual=compile(row,self.registry)
        self.assertEqual("exact",actual.status,actual.material_residuals)
        self.assertIn(TEMPORARY_SELF_COPY_CAPABILITY,actual.faces[0].nodes[0].capability_dependencies)
        raw=json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        cap=next(r for r in raw["capabilities"] if r["id"]==TEMPORARY_SELF_COPY_CAPABILITY)
        cap.update(status="blocked",blockers=["Temporary copy owner withdrawn."])
        withdrawn=CapabilityRegistry(raw)
        declarations=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index=build_index(registry_value=raw,declaration_source=declarations,
                          discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index,registry=withdrawn))
        self.assertNotEqual("exact",compile(row,withdrawn).status)
        self.assertEqual("exact",compile(self.db.lookup(self.artifact),withdrawn).status)
        unsupported=replace(row,oracle_text=row.oracle_text.replace("until end of turn","for as long as you control it"))
        self.assertNotEqual("exact",compile(unsupported,self.registry).status)
        with patch("quorune.compiler.temporary_self_copy_templates.temporary_self_copy_template",return_value=None):
            self.assertNotEqual("exact",compile(row,self.registry).status)
