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
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


ORIGINALS = ("Liminal Hold", "Prayer of Binding", "Makeshift Binding")
LINK = "zone.linked_exile_return.fixed"
LIFE = "life.change.effect"
ORDERED = "resolution.effect_program.closed_components"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"


class LinkedExileAndLifeTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selections = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
            "attached-destruction-set-cards.json": {"Grizzly Bears", "Unsummon", "Island", "Murder"},
            "enchant-public-state-cards.json": {"Disenchant"},
            "public-graveyard-entry-cards.json": {"Sol Ring"},
        }
        fixtures = [FIXTURES / "linked-exile-and-life-cards.json"]
        for name, names in selections.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            path = folder / name
            path.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(path)
        database = folder / "linked-exile-and-life.sqlite3"
        build_fixture_database(fixtures, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked exile and fixed gain", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_originals_keep_the_exact_pair_and_independent_final_gain(self):
        for name in ORIGINALS:
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                n, = [n for n in ir.faces[0].nodes if any(e.get("op") == "linked_exile_return" for e in n.effects)]
                self.assertTrue({LINK, LIFE, ORDERED, "target.revalidate_resolution"}
                    <= set(n.capability_dependencies))
                first, returned, life = n.effects
                self.assertEqual(("exile", "return"), (first["phase"], returned["phase"]))
                self.assertEqual(first["binding_id"], returned["binding_id"])
                self.assertEqual(first["spec"], returned["spec"])
                self.assertEqual(("source_departure", "owner"), (first["spec"]["timing"], first["spec"]["controller"]))
                self.assertEqual({"op": "life", "player": "$controller", "delta": 2}, life)
                self.assertEqual("opponent", n.target_schema["controller_relation"])
                if name == "Makeshift Binding":
                    self.assertEqual((1, ["creature"]), (n.target_schema["count"], n.target_schema["types_any"]))
                else:
                    self.assertEqual((1, ["land"]), (n.target_schema["up_to"], n.target_schema["types_none"]))
                ready, _ = register_card(self.db, self.registry, name)
                self.assertTrue(ready["strict_capability_ready"], ready["blockers"])

    def test_raw_ordered_shape_keeps_the_pair_roles_and_life_amount(self):
        card = self.db.lookup("Liminal Hold", fuzzy=False)
        ready, semantics = register_card(self.db, self.registry, card.name)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = [p for p in semantics.programs_for_oracle(card.oracle_id)
            if any(e.get("op") == "linked_exile_return" for e in p.effects)]
        first, returned, life = thaw_value(program.effects)
        for changes in (
            {"effects": [returned, first, life]},
            {"effects": [first, life]},
            {"effects": [first, {**returned, "binding_id": "blink:different"}, life]},
            {"effects": [first, returned, {**life, "delta": 3}]},
            {"effects": [first, returned, {**life, "player": "$opponent"}]},
            {"target_schema": {**program.target_schema, "up_to": 2}},
        ):
            with self.subTest(changes=changes):
                strict = bind_semantic_program_runtime(replace(program, **changes),
                    capability_registry=self.registry, profile=PROFILE)
                self.assertFalse(strict["strict"], strict)

    def enter(self, name, seed, *, active="A", target="Grizzly Bears", control_owner=None, response=None):
        game = self.session(seed)
        source = self.add(game.engine, name, zone="hand")
        other = self.add(game.engine, target, seat="B")
        if control_owner is not None:
            other.owner = control_owner
        reply = self.add(game.engine, response, seat="B", zone="hand") if response else None
        private = self.add(game.engine, "Murder", seat="D", zone="hand", ref="binding-private-hand")
        self.add(game.engine, "Generic Bound Plains")
        if response == "Disenchant":
            for n in range(2):
                self.add(game.engine, "Generic Bound Plains", seat="B", ref=f"binding-reply-plains-{n}")
        elif response == "Unsummon":
            self.add(game.engine, "Island", seat="B")
        for card in (source, other):
            self.assert_closed(game, card)
        if reply is not None:
            self.assert_closed(game, reply)
        self.prepare(game, active=active, actor="A", mana={"C": 6})
        self.checkpoint(game)
        life = game.state.players["A"].life
        self.cast(game, "A", source)
        pending = self.resolve(game)
        self.assertIsNotNone(pending)
        self.assertEqual(("semantic.target", ["A"]), (pending.kind, pending.actors))
        current = game.state.cards[source.object_id]
        self.assertEqual("battlefield", current.zone)
        return game, current, other, reply, private, life

    def test_actual_flash_link_and_life_return_the_artifact_to_its_owner(self):
        game, source, artifact, reply, private, life = self.enter("Prayer of Binding", 76155111,
            active="B", target="Sol Ring", control_owner="C", response="Disenchant")
        self.assertEqual(("C", "B"), (artifact.owner, artifact.controller))
        original = artifact.logical_object_id
        self.target(game, "A", [artifact.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("exile", game.state.cards[artifact.object_id].zone)
        self.assertEqual(life + 2, game.state.players["A"].life)

        self.cast(game, "B", reply, [source.ref])
        self.assertIsNone(self.resolve(game))
        current = game.state.cards[artifact.object_id]
        self.assertEqual(("battlefield", "C"), (current.zone, current.controller))
        self.assertNotEqual(original, current.logical_object_id)
        self.assertEqual(life + 2, game.state.players["A"].life)
        for seat in "ABC":
            packet = json.dumps(game.packet("pilot:" + seat, full=True))
            self.assertNotIn(private.ref, packet)
            self.assertNotIn(private.printed_name, packet)
        self.replay(game, load=True)

    def test_actual_optional_zero_resolves_but_a_lost_announced_target_fizzles(self):
        for choose_target in (False, True):
            with self.subTest(choose_target=choose_target):
                game, source, creature, reply, _, life = self.enter("Liminal Hold", 76155112 + choose_target,
                    response="Unsummon" if choose_target else None)
                self.target(game, "A", [creature.ref] if choose_target else [])
                if choose_target:
                    self.cast(game, "B", reply, [creature.ref])
                    self.resolve_top(game)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(life if choose_target else life + 2, game.state.players["A"].life)
                self.assertEqual("hand" if choose_target else "battlefield", game.state.cards[creature.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[source.object_id].zone)
                self.replay(game, load=True)

    def test_actual_source_departure_with_a_legal_target_still_gains_life(self):
        game, source, creature, reply, _, life = self.enter("Makeshift Binding", 76155114,
            response="Disenchant")
        self.target(game, "A", [creature.ref])
        self.cast(game, "B", reply, [source.ref])
        self.resolve_top(game)
        self.assertEqual("graveyard", game.state.cards[source.object_id].zone)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[creature.object_id].zone)
        self.assertEqual(life + 2, game.state.players["A"].life)
        self.replay(game, load=True)

    def test_omitting_the_pair_owner_blocks_ordinary_original_registration(self):
        with patch("quorune.rules.closed_effect_program_shapes._linked_exile_then_life_dependencies", return_value=()):
            b, _ = register_card(self.db, self.registry, "Liminal Hold")
        self.assertFalse(b["strict_capability_ready"], b)
        self.assertTrue(any("Ordered programs require every complete child owner" in blocker for blocker in b["blockers"]),
            b["blockers"])

    def test_verified_withdrawals_require_both_link_and_life_for_each_original(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for cap in (LINK, LIFE):
            with self.subTest(cap=cap):
                rows = deepcopy(raw)
                next(item for item in rows["capabilities"] if item["id"] == cap).update(
                    status="blocked", blockers=["Linked result requirement withdrawn"])
                registry = CapabilityRegistry(rows)
                index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((cap,), profile=PROFILE)
                self.assertIn("status:" + cap + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in ORIGINALS:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertFalse(ready["strict_capability_ready"], (name, ready["blockers"]))
                other, _ = register_card(self.db, registry, "Grizzly Bears")
                self.assertTrue(other["strict_capability_ready"], other["blockers"])
