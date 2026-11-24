"""Native checks for announced counters on a selected copy entry."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.carddb import CardDatabase
from quorune.compiler.entry_copy_templates import _announced_x_copy_entry_spec
from quorune.deck import DeckDefinition, DeckEntry
from quorune.entry_copy import ANNOUNCED_X_COPY_COUNTER_CAPABILITY, EntryCopySpec
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class AnnouncedXCopyEntryTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Measured Mimic"
    target = "Generic Bound Body"
    copy_spell = "Trial Twin Sign"
    restore = "Trial Creature Return"
    removal = "Trial Creature Unmaking"
    basic = "Generic Bound Plains"

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        database = folder / "announced-x-copy-entry.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "announced-x-copy-entry-cards.json",
            fixtures / "controlled-copy-entry-cards.json",
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced copy entry", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    def _cast_x(self, game, source, x):
        self.priority(game, "A")
        offer, = [row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                  if row["id"] == "cast:" + source.ref]
        payment = {"G": 1, "U": 1, **({"C": x} if x else {})}
        sent = game.act("pilot:A", {"action_id":offer["id"], "pay":"manual", "payment":payment, "x":x})
        self.assertTrue(sent.ok, sent.summary)
        self.assertEqual(x, game.state.stack[-1].x_value)

    def _copy_choice(self, game, target):
        pending = self.resolve(game)
        self.assertEqual(("replacement.order", ["A"]), (pending.kind, pending.actors))
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        choice, = [r for r in options if not r.get("decline") and r["id"].endswith(":" + target.ref)]
        return {"action_id":"choose", "replacement":choice["id"]}

    def _choose(self, game, command):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:D", command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        sent = game.act("pilot:A", command)
        self.assertTrue(sent.ok, sent.summary)

    def test_paid_copy_entry_uses_announced_x_and_only_public_creatures(self):
        for x in (0, 4):
            with self.subTest(x=x):
                game = self.session(707_481+x)
                source = self.add(game.engine, self.copier, zone="hand")
                target = self.add(game.engine, self.target, seat="B", ref="public-copy-value")
                private = self.add(game.engine, self.target, seat="D", zone="hand", ref="hidden-copy-candidate")
                target.counters["+1/+1"] = 3
                self.assert_closed(game, source)
                self.prepare(game,mana={"C":4,"G":1,"U":1})
                self.checkpoint(game)
                self._cast_x(game,source,x)
                choice=self._copy_choice(game,target)
                self.assertNotIn(private.ref,json.dumps(game.packet("pilot:A",full=True)))
                game=self.replay(game,load=True)
                self._choose(game,choice)
                self.assertIsNone(self.resolve(game))
                live=game.state.cards[source.object_id]
                self.assertEqual("battlefield",live.zone)
                self.assertEqual(self.target,game.engine.display_name(live.object_id))
                self.assertEqual(x,live.counters.get("+1/+1",0))
                self.assertEqual(3,game.state.cards[target.object_id].counters["+1/+1"])
                self.replay(game,load=True)

    def test_copied_permanent_spell_retains_announced_x_in_both_entries(self):
        game=self.session(707_486)
        source=self.add(game.engine,self.copier,zone="hand")
        fork=self.add(game.engine,self.copy_spell,zone="hand")
        target=self.add(game.engine,self.target,seat="C")
        self.assert_closed(game,source)
        self.assert_closed(game,fork)
        self.prepare(game,mana={"C":3,"G":2,"U":2})
        self.checkpoint(game)
        self._cast_x(game,source,3)
        original=game.state.stack[-1]
        self.cast(game,"A",fork,[original.ref])
        self.resolve_top(game)
        copied,=[item for item in game.state.stack if item.kind=="spell_copy"]
        self.assertEqual(3,copied.x_value)
        choice=self._copy_choice(game,target)
        game=self.replay(game,load=True)
        self._choose(game,choice)
        self.until(game,lambda s:s.state.cards[copied.card_object_id].zone=="battlefield")
        self.assertEqual(3,game.state.cards[copied.card_object_id].counters.get("+1/+1",0))
        choice=self._copy_choice(game,game.state.cards[target.object_id])
        self._choose(game,choice)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(3,game.state.cards[source.object_id].counters.get("+1/+1",0))
        self.replay(game,load=True)

    def test_uncast_copy_reentry_does_not_reuse_the_old_announced_amount(self):
        game=self.session(707_487)
        source=self.add(game.engine,self.copier,zone="hand")
        target=self.add(game.engine,self.target,seat="C")
        removal=self.add(game.engine,self.removal,seat="B",zone="hand")
        restore=self.add(game.engine,self.restore,zone="hand")
        for card in (source,removal,restore):
            self.assert_closed(game,card)
        game.state.players["B"].mana_pool.update({"B":1,"C":1})
        self.prepare(game,mana={"C":2,"G":1,"U":1,"W":1})
        self.checkpoint(game)
        self._cast_x(game,source,2)
        self._choose(game,self._copy_choice(game,target))
        self.assertIsNone(self.resolve(game))
        live=game.state.cards[source.object_id]
        old=live.logical_object_id
        self.assertEqual(2,live.counters.get("+1/+1",0))
        self.cast(game,"B",removal,[live.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard",game.state.cards[source.object_id].zone)
        self.cast(game,"A",restore,[game.state.cards[source.object_id].ref])
        choice=self._copy_choice(game,game.state.cards[target.object_id])
        game=self.replay(game,load=True)
        self._choose(game,choice)
        self.assertIsNone(self.resolve(game))
        live=game.state.cards[source.object_id]
        self.assertEqual("battlefield",live.zone)
        self.assertNotEqual(old,live.logical_object_id)
        self.assertEqual(0,live.counters.get("+1/+1",0))
        self.replay(game,load=True)

    def test_announced_copy_entry_requires_a_printed_amount_and_exact_authority(self):
        source=self.db.lookup(self.copier)
        line=source.oracle_text.split("\n")[-1]
        spec=_announced_x_copy_entry_spec(line,source_name=source.name)
        self.assertEqual(spec,EntryCopySpec.from_dict(spec.to_dict()))
        self.assertEqual(8,spec.schema_version)
        for flag in (1,False,None):
            with self.assertRaises(ValueError):
                EntryCopySpec.from_dict({**spec.to_dict(),"announced_x_counter":flag})
        def compile(card,registry):
            return compile_oracle_card(card,capability_registry=registry,capability_profile="commander_review")
        self.assertEqual("exact",compile(source,self.registry).status)
        unpriced=replace(source,mana_cost=source.mana_cost.replace("{X}",""))
        self.assertNotEqual("exact",compile(unpriced,self.registry).status)
        raw=json.loads((ROOT/"quorune/rules/capability-registry.json").read_bytes())
        cap=next(row for row in raw["capabilities"] if row["id"]==ANNOUNCED_X_COPY_COUNTER_CAPABILITY)
        cap.update(status="blocked",blockers=["Copy-entry X owner withdrawn."])
        withdrawn=CapabilityRegistry(raw)
        declarations=read_evidence_document(ROOT/"platform/capability-evidence-declarations.json")
        index=build_index(registry_value=raw,declaration_source=declarations,
                          discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index,registry=withdrawn))
        self.assertNotEqual("exact",compile(source,withdrawn).status)
        kept=replace(source,oracle_text=source.oracle_text.split(", except")[0]+".")
        self.assertEqual("exact",compile(kept,withdrawn).status)
        self.assertEqual("exact",compile(self.db.lookup("Trial Shield Mimic"),withdrawn).status)
        with patch("quorune.compiler.entry_copy_templates._announced_x_copy_entry_spec",return_value=None):
            self.assertNotEqual("exact",compile(source,self.registry).status)
