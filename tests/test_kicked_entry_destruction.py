from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
from quorune.affected_permanents import (
    KICKED_ENTRY_DESTRUCTION_CAPABILITIES,
    KICKED_ENTRY_DESTRUCTION_FIELD,
    KickedEntryDestructionScope as Scope,
    KickedEntryDestructionSpec,
)
from quorune.card_programs.binding import bind_semantic_program_runtime
from quorune.carddb import CardDatabase
from quorune.compiler import kicked_entry_trigger_nodes
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = {"Desolation Angel": Scope.LANDS, "Desolation Giant": Scope.OTHER_CREATURES}


class KickedEntryDestructionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-paid-result-slot-cards.json": {"Cackling Counterpart", "Darksteel Myr"},
            "activation-restriction-material-cards.json": {"Cloudshift"},
            "additional-continuous-carriers-cards.json": {"Walking Corpse"},
            "ability-source-counter-cards.json": {"Sol Ring"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "empty-battlefield-condition-cards.json": {"Ashcoat Bear"},
            "fixed-qualified-stack-copy-cards.json": {"Lithoform Engine"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "kicked-entry-destruction-cards.json": set(ORIGINALS),
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names, (filename, names)
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "kicked-entry-destruction.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Kicked destruction", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_entry(self, name):
        card = self.db.lookup(name, fuzzy=False)
        result, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(result["strict_capability_ready"], result["blockers"])
        programs = semantics.programs_for_oracle(card.oracle_id)
        trigger, = [program for program in programs if program.event == "permanent.enter.self"]
        return card, trigger, programs

    def test_both_kicked_destruction_originals_bind_one_unconditional_trigger_and_the_fixed_price(self):
        for name, scope in ORIGINALS.items():
            with self.subTest(name=name):
                card, trigger, programs = self.original_entry(name)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual([KickedEntryDestructionSpec(scope).effect()], thaw_value(trigger.effects))
                self.assertIsNone(trigger.target_schema)
                self.assertIsNone(trigger.event_condition)
                self.assertLessEqual(set(KICKED_ENTRY_DESTRUCTION_CAPABILITIES), set(trigger.capability_dependencies))
                self.assertTrue(any("casting.kicker.fixed_mana" in program.capability_dependencies
                                    and program.event == "cast.cost" for program in programs))

    def test_kicked_destruction_descriptor_rejects_changed_source_set_and_trigger_scope(self):
        _, trigger, _ = self.original_entry("Desolation Giant")
        effect, = thaw_value(trigger.effects)
        for changes in (
            {"effects": [{**effect, KICKED_ENTRY_DESTRUCTION_FIELD: True}]},
            {"effects": [{**effect, "source": "$source"}]},
            {"effects": [{**effect, "set": {**effect["set"], "controller_relation": "any"}}]},
            {"effects": [{**effect, "set": {**effect["set"], "exclude_source": False}}]},
            {"event_condition": {"field": "cast_option", "op": "eq", "value": "kicked"}},
            {"target_schema": {"zones": ["battlefield"], "categories": ["permanent"], "count": 1}},
        ):
            with self.subTest(changes=changes):
                bound = bind_semantic_program_runtime(replace(trigger, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(bound["strict"], bound)

    def _reject(self, game, command, seat="A"):
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:" + seat, command)
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _cast_with_payment(self, game, source, *, kicked, payment):
        self.priority(game, "A")
        offered = self.action(game, "A", source)
        self.assertEqual({"normal", "kicked"}, {row["id"] for row in offered["cost_options"]})
        command = {"action_id": offered["id"], "cost_option": "kicked" if kicked else "normal",
                   "pay": "manual", "payment": payment}
        self._reject(game, command, "B")
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def _entry_triggers(self, game, source=None):
        return [item for item in game.state.stack if item.kind == "triggered_ability"
                and (source is None or item.source_object_id == source.object_id)]

    def _zone(self, game, card):
        return game.state.cards[card.object_id].zone

    def test_actual_angel_uses_one_entry_for_the_current_controlled_or_global_land_set(self):
        for kicked in (False, True):
            with self.subTest(kicked=kicked):
                game = self.session(77132001 + kicked)
                angel = self.add(game.engine, "Desolation Angel", zone="hand")
                entries = {seat: self.add(game.engine, "Generic Bound Plains", seat=seat, ref="land-" + seat)
                           for seat in "ABCD"}
                survivor = self.add(game.engine, "Walking Corpse", seat="B")
                self.assert_closed(game, angel)
                self.assert_closed(game, survivor)
                mana = {"C": 3, "B": 2, "W": 2}
                self.prepare(game, mana=mana)
                self.checkpoint(game)
                self._cast_with_payment(game, angel, kicked=kicked,
                    payment=mana if kicked else {"C": 3, "B": 2})
                self.resolve_top(game)
                entry, = self._entry_triggers(game, angel)
                self.assertEqual("kicked" if kicked else None, entry.context.get("cast_option"))
                self.assertEqual({self._zone(game, land) for land in entries.values()}, {"battlefield"})
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", self._zone(game, angel))
                self.assertEqual("battlefield", self._zone(game, survivor))
                self.assertEqual({"A": "graveyard",
                    **{seat: "graveyard" if kicked else "battlefield" for seat in "BCD"}},
                    {seat: self._zone(game, land) for seat, land in entries.items()})
                self.replay(game, load=True)

    def test_actual_giant_blink_queues_a_new_ordinary_entry_and_preserves_original_kicked_fact(self):
        game = self.session(77132003)
        giant = self.add(game.engine, "Desolation Giant", zone="hand")
        blink = self.add(game.engine, "Cloudshift", zone="hand")
        own = self.add(game.engine, "Walking Corpse")
        other = self.add(game.engine, "Walking Corpse", seat="B", ref="kicked-entry-other")
        protected = self.add(game.engine, "Darksteel Myr", seat="C")
        for card in (giant, blink, own, other, protected):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "R": 2, "W": 3})
        self.checkpoint(game)
        self._cast_with_payment(game, giant, kicked=True, payment={"C": 2, "R": 2, "W": 2})
        self.resolve_top(game)
        original, = self._entry_triggers(game, giant)
        identity = game.state.cards[giant.object_id].logical_object_id
        self.assertEqual(("kicked", identity),
            (original.context.get("cast_option"), original.context.get("source_logical_object_id")))
        self.cast(game, "A", blink, [giant.ref])
        self.resolve_top(game)
        queued = self._entry_triggers(game, giant)
        self.assertEqual(2, len(queued))
        fresh = game.state.cards[giant.object_id]
        self.assertNotEqual(identity, fresh.logical_object_id)
        self.assertEqual(["kicked", None], [item.context.get("cast_option") for item in queued])
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual(("graveyard", "battlefield", "battlefield"),
            (self._zone(game, own), self._zone(game, other), self._zone(game, giant)))
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "graveyard", "battlefield"),
            (self._zone(game, giant), self._zone(game, other), self._zone(game, protected)))
        self.replay(game, load=True)

    def test_actual_copied_kicked_giant_inherits_its_entry_scope_and_excludes_itself(self):
        game = self.session(77132004)
        giant = self.add(game.engine, "Desolation Giant", zone="hand")
        copier = self.add(game.engine, "Lithoform Engine")
        body = self.add(game.engine, "Walking Corpse", seat="B")
        for card in (giant, copier, body):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 6, "R": 2, "W": 2})
        self.checkpoint(game)
        self._cast_with_payment(game, giant, kicked=True, payment={"C": 2, "R": 2, "W": 2})
        source, = (item for item in game.state.stack if item.card_object_id == giant.object_id)
        self.priority(game, "A")
        choice, = (row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                   if row["id"].startswith("activate:" + copier.ref + ":")
                   and source.ref in row.get("target_schema", {}).get("legal_refs", ()))
        accepted = game.act("pilot:A", {"action_id": choice["id"], "targets": [source.ref], "pay": "auto"})
        self.assertTrue(accepted.ok, accepted.summary)
        self.resolve_top(game)
        self.assertTrue(any(item.kind == "spell_copy" for item in game.state.stack))
        self.resolve_top(game)
        token, = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        copy_entry, = self._entry_triggers(game, token)
        self.assertEqual("kicked", copy_entry.context.get("cast_option"))
        game = self.replay(game, load=True)
        self.resolve_top(game)
        self.assertEqual("battlefield", self._zone(game, token))
        self.assertEqual("graveyard", self._zone(game, body))
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", self._zone(game, giant))
        remaining = game.state.cards.get(token.object_id)
        if remaining is not None:
            self.assertEqual("outside", remaining.zone)
        self.replay(game, load=True)

    def test_actual_battlefield_token_copy_uses_ordinary_entry_even_when_original_was_kicked(self):
        game = self.session(77132005)
        giant = self.add(game.engine, "Desolation Giant", zone="hand")
        duplicate = self.add(game.engine, "Cackling Counterpart", zone="hand")
        other = self.add(game.engine, "Ashcoat Bear", seat="B", zone="hand")
        for card in (giant, duplicate, other):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 3, "R": 2, "W": 2, "U": 2})
        game.state.players["B"].mana_pool.update({"C": 1, "G": 1})
        self.checkpoint(game)
        self._cast_with_payment(game, giant, kicked=True, payment={"C": 2, "R": 2, "W": 2})
        self.assertIsNone(self.resolve(game))
        self.cast(game, "B", other)
        self.assertIsNone(self.resolve(game))
        self.cast(game, "A", duplicate, [giant.ref])
        self.resolve_top(game)
        token, = (card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token)
        entry, = self._entry_triggers(game, token)
        self.assertEqual(("outside", None), (entry.context.get("from"), entry.context.get("cast_option")))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield", "battlefield"),
                         (self._zone(game, giant), self._zone(game, other), self._zone(game, token)))
        self.replay(game, load=True)

    def test_omitting_the_two_arm_entry_lowerer_leaves_both_full_cards_partial(self):
        import re

        with patch.object(kicked_entry_trigger_nodes, "_KICKED_DESTRUCTION", re.compile(r"(?!)")):
            for name in ORIGINALS:
                bound, _ = register_card(self.db, self.registry, name)
                self.assertFalse(bound["strict_capability_ready"], bound)
            unrelated, _ = register_card(self.db, self.registry, "Sol Ring")
            self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])

    def test_genuine_kicked_and_destruction_withdrawals_disable_only_dependent_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in KICKED_ENTRY_DESTRUCTION_CAPABILITIES:
            with self.subTest(capability=capability):
                document = deepcopy(raw)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Kicked entry destruction authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    bound, _ = register_card(self.db, registry, name)
                    self.assertFalse(bound["strict_capability_ready"], bound)
                unrelated, _ = register_card(self.db, registry, "Sol Ring")
                self.assertTrue(unrelated["strict_capability_ready"], unrelated["blockers"])
