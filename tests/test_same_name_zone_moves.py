from __future__ import annotations

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
from quorune.compiler.public_zone_move_templates import public_zone_move_effect_template
from quorune.deck import DeckDefinition, DeckEntry
from quorune.public_zone_moves import PUBLIC_SAME_NAME_MOVE_VERSION, PublicZoneMoveSetSpec
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.same_name_zone_moves import (
    SAME_NAME_ZONE_MOVE_CAPABILITY as CAPABILITY,
    SameNameZoneMoveScope,
)
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
RETURN = "Echoing Return"
EXILE = "Sever the Bloodline"
TRUTH = "Echoing Truth"
RATS = "Relentless Rats"
MORPH = "Lumithread Field"


class SameNameZoneMoveTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activated-self-transform-cards.json": {"Cremate", "Unsummon"},
            "activation-paid-result-slot-cards.json": {"Spined Wurm"},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "fixed-public-zone-move-cards.json": {"Inundate"},
            "fixed-basic-type-setting-cards.json": {"Song of the Dryads"},
            "morph-rules-cards.json": {MORPH},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
            "printed-deck-copy-limit-cards.json": {RATS},
            "temporary-creature-copy-set-cards.json": {"Mirage Mirror"},
        }
        parts = [FIXTURES / "same-name-zone-move-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "same-name-zone-moves.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Same-name public zone moves", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards, mana):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana)

    def _act(self, game, seat, command):
        result = game.act("pilot:" + seat, command)
        self.assertTrue(result.ok, result.summary)

    def _assert_batch(self, game, cursor, destination, cards):
        event, = [row for row in game.state.events[cursor:] if row.code == "effect.public_zone_move_set"]
        self.assertEqual(destination, event.details["requested_destination"])
        self.assertEqual(len(cards), event.details["affected_count"])
        self.assertEqual({card.object_id for card in cards}, set(event.changed_objects))
        current = [game.state.cards[card.object_id] for card in cards]
        self.assertTrue(all(card.zone == destination for card in current))
        self.assertEqual(1, len({card.zone_timestamp for card in current}))

    def test_complete_originals_use_the_closed_anchor_and_result_domains(self):
        for name, scope in ((EXILE, SameNameZoneMoveScope.EXILE_BATTLEFIELD_CREATURES),
                            (RETURN, SameNameZoneMoveScope.RETURN_OWN_GRAVEYARD_CARDS)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                report, registry = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                program, = (program for program in registry.programs_for_oracle(card.oracle_id)
                            if CAPABILITY in program.capability_dependencies)
                effect, = program.effects
                serialized = effect["set"]
                self.assertEqual(PUBLIC_SAME_NAME_MOVE_VERSION, serialized["schema_version"])
                self.assertEqual(scope.spec(), PublicZoneMoveSetSpec.from_dict(serialized))
                self.assertEqual(scope.target_schema(), program.target_schema)
                if name == EXILE:
                    required = {owner for p in registry.programs_for_oracle(card.oracle_id) for owner in p.capability_dependencies}
                    self.assertIn("casting.flashback.fixed_mana", required)
                for set_fields in ({"schema_version": True}, {"schema_version": 2}, {"same_name_target": "$target.1"},
                                   {"exclude_source": True}, {"destination": "battlefield"}, {"seat_relation": "opponents"}):
                    changed = replace(program, effects=[{**effect, "set": {**serialized, **set_fields}}])
                    self.assertFalse(bind_semantic_program_runtime(
                        changed, capability_registry=self.registry, profile=PROFILE,
                    )["strict"], set_fields)
                widened = replace(program, target_schema={**program.target_schema, "count": 2})
                self.assertFalse(bind_semantic_program_runtime(
                    widened, capability_registry=self.registry, profile=PROFILE,
                )["strict"])
                deps = tuple(key for key in program.capability_dependencies if key != CAPABILITY)
                undeclared = replace(program, capability_dependencies=deps, coverage=(),
                                     capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(undeclared, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        for text in (
            "Exile target nonland permanent and all other permanents with the same name as that permanent.",
            "Exile target creature and all other creatures with the same name as that creature, then draw a card.",
            "Return target creature card and all other cards with the same name as that card from any graveyard to their owners' hands.",
            "Return target nonland permanent and all other nonland permanents with the same name as that permanent to their owners' hands.",
        ):
            self.assertIsNone(public_zone_move_effect_template(text), text)

    def test_fresh_zone_producer_omission_blocks_exact_same_name_admission(self):
        def witness():
            for name in (EXILE, RETURN):
                report, _ = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])

        witness()
        with patch("quorune.compiler.public_zone_move_templates._same_name_zone_move_template", return_value=None):
            with self.assertRaises(AssertionError):
                witness()
        witness()

    def test_actual_echoing_return_moves_only_own_graveyard_matches(self):
        game = self.session(201200611)
        spell = self.add(game.engine, RETURN, zone="hand")
        anchor = self.add(game.engine, RATS, zone="graveyard", ref="return-own-target")
        same = self.add(game.engine, RATS, zone="graveyard", ref="return-own-other")
        foreign = self.add(game.engine, RATS, zone="graveyard", seat="B", ref="return-foreign")
        active = self.add(game.engine, RATS, seat="C", ref="return-battlefield")
        other = self.add(game.engine, "Grizzly Bears", zone="graveyard", ref="return-different")
        private = self.add(game.engine, "Mirage Mirror", seat="D", zone="hand", ref="return-private")
        self._start(game, (spell, anchor), {"B": 1})
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offer["id"], "targets": [foreign.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [anchor.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self._assert_batch(game, cursor, "hand", (anchor, same))
        for card, zone in ((foreign, "graveyard"), (active, "battlefield"), (other, "graveyard")):
            self.assertEqual(zone, game.state.cards[card.object_id].zone)
        self.assertEqual({anchor.object_id, same.object_id}, {i for i in game.state.players["A"].zones["hand"]
                         if i in {anchor.object_id, same.object_id}})
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_echoing_return_fizzles_after_its_single_target_leaves(self):
        game = self.session(201200612)
        spell = self.add(game.engine, RETURN, zone="hand")
        anchor = self.add(game.engine, RATS, zone="graveyard", ref="return-lost-target")
        same = self.add(game.engine, RATS, zone="graveyard", ref="return-lost-target-other")
        response = self.add(game.engine, "Cremate", zone="hand", seat="B")
        self._start(game, (spell, anchor, response), {"B": 1})
        game.state.players["B"].mana_pool["B"] = 1
        self.checkpoint(game)
        self.cast(game, "A", spell, [anchor.ref])
        self.cast(game, "B", response, [anchor.ref])
        self.resolve_top(game)
        self.assertEqual("exile", game.state.cards[anchor.object_id].zone)
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("graveyard", game.state.cards[same.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertFalse(any(row.code == "effect.public_zone_move_set" for row in game.state.events[cursor:]))
        self.replay(game, load=True)

    def test_actual_flashback_exiles_one_current_named_battlefield_batch_and_the_spell(self):
        game = self.session(201200613)
        spell = self.add(game.engine, EXILE, zone="graveyard")
        anchor = self.add(game.engine, "Grizzly Bears", seat="C", ref="sever-target")
        other = self.add(game.engine, "Grizzly Bears", seat="D", ref="sever-public-other")
        mirror = self.add(game.engine, "Mirage Mirror", seat="B", ref="sever-copy-in-response")
        graveyard = self.add(game.engine, "Grizzly Bears", seat="D", zone="graveyard", ref="sever-graveyard")
        different = self.add(game.engine, "Isamaru, Hound of Konda", seat="B", ref="sever-unrelated")
        self._start(game, (spell, mirror), {"C": 5, "B": 2})
        game.state.players["B"].mana_pool["C"] = 2
        self.checkpoint(game)
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        option, = [row["id"] for row in offer["cost_options"] if row["id"] == "flashback"]
        self._act(game, "A", {"action_id": offer["id"], "cost_option": option, "targets": [anchor.ref],
                              "pay": "manual", "payment": {"C": 5, "B": 2}})
        self.priority(game, "B")
        rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [row for row in rows if row["id"].startswith(f"activate:{mirror.ref}:")]
        self._act(game, "B", {"action_id": activation["id"], "targets": [anchor.ref], "pay": "auto"})
        self.resolve_top(game)
        self.assertEqual("Grizzly Bears", game.engine._effective_card_data(mirror.object_id)["name"])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self._assert_batch(game, cursor, "exile", (anchor, other, mirror))
        for card, zone in ((spell, "exile"), (graveyard, "graveyard"), (different, "battlefield")):
            self.assertEqual(zone, game.state.cards[card.object_id].zone)
        self.replay(game, load=True)

    def test_actual_sever_exiles_only_the_nameless_target_and_keeps_other_morph_private(self):
        game = self.session(201200614)
        spell = self.add(game.engine, EXILE, zone="hand")
        first = self.add(game.engine, MORPH, zone="hand", ref="sever-hidden-target")
        second = self.add(game.engine, MORPH, zone="hand", ref="sever-other-hidden")
        self._start(game, (spell, first, second), {"C": 9, "B": 1})
        self.checkpoint(game)
        for card in (first, second):
            self.priority(game, "A")
            self._act(game, "A", {"action_id": f"cast-morph:{card.ref}", "pay": "manual", "payment": {"C": 3}})
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[card.object_id].face_down)
        self.assertNotIn(MORPH, json.dumps(game.packet("pilot:C", full=True)))
        self.cast(game, "A", spell, [first.ref])
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self._assert_batch(game, cursor, "exile", (first,))
        self.assertFalse(game.state.cards[first.object_id].face_down)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertTrue(game.state.cards[second.object_id].face_down)
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)["state"]
            self.assertIn(MORPH, json.dumps(packet))
            permanent, = [row for row in packet["players"]["A"]["bf"] if row.get("id") == second.ref]
            self.assertNotEqual(MORPH, permanent.get("n"))
            self.assertNotEqual(second.oracle_id[:8], permanent.get("cid"))
        self.replay(game, load=True)

    def test_pinned_echoing_truth_keeps_a_nonland_anchor_and_all_same_name_permanents(self):
        original = self.db.lookup(TRUTH, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, TRUTH)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(original.oracle_id)
        effect, = program.effects
        scope = SameNameZoneMoveScope.RETURN_BATTLEFIELD_PERMANENTS
        self.assertEqual(scope.spec(), PublicZoneMoveSetSpec.from_dict(effect["set"]))
        self.assertEqual(scope.target_schema(), program.target_schema)
        self.assertEqual(["land"], program.target_schema["types_none"])
        self.assertEqual((), scope.spec().query.excluded_types)
        widened = replace(program, target_schema={key: value for key, value in program.target_schema.items() if key != "types_none"})
        self.assertFalse(bind_semantic_program_runtime(widened, capability_registry=self.registry, profile=PROFILE)["strict"])
        from quorune.compiler import public_zone_move_templates

        with patch.dict(public_zone_move_templates._SAME_NAME_ZONE_GRAMMAR, {original.oracle_text.casefold(): None}):
            omitted, _ = register_card(self.db, self.registry, TRUTH)
            self.assertFalse(omitted["strict_capability_ready"], omitted)

    def test_actual_echoing_truth_returns_a_same_name_land_and_stolen_permanent_to_their_owners(self):
        game = self.session(201200615)
        spell = self.add(game.engine, TRUTH, zone="hand")
        song = self.add(game.engine, "Song of the Dryads", zone="hand")
        totem = self.add(game.engine, "Avarice Totem")
        land = self.add(game.engine, "Spined Wurm", seat="B", ref="truth-land-sibling")
        stolen = self.add(game.engine, "Spined Wurm", seat="C", ref="truth-stolen-anchor")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="truth-unrelated")
        self._start(game, (spell, song, totem), {"C": 8, "G": 1, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", song, [land.ref])
        self.assertIsNone(self.resolve(game))
        land_data = game.engine._effective_card_data(land.object_id)
        self.assertEqual("Spined Wurm", land_data["name"])
        self.assertEqual({"land"}, game.engine._type_parts(land_data["type_line"])[0])
        self.priority(game, "A")
        rows = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [row for row in rows if row["id"].startswith(f"activate:{totem.ref}:")]
        self._act(game, "A", {"action_id": activation["id"], "targets": [stolen.ref],
                              "pay": "manual", "payment": {"C": 5}})
        self.assertIsNone(self.resolve(game))
        self.assertEqual("A", game.state.cards[stolen.object_id].controller)
        self.assertEqual("C", game.state.cards[stolen.object_id].owner)
        self.priority(game, "A")
        offer = self.action(game, "A", spell)
        self.assertNotIn(land.ref, offer["target_schema"]["legal_refs"])
        before = authoritative_state_hash(game.state)
        rejected = game.act("pilot:A", {"action_id": offer["id"], "targets": [land.ref], "pay": "auto"})
        self.assertFalse(rejected.ok, rejected.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, [stolen.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self._assert_batch(game, cursor, "hand", (land, stolen))
        self.assertIn(land.object_id, game.state.players["B"].zones["hand"])
        self.assertIn(stolen.object_id, game.state.players["C"].zones["hand"])
        self.assertNotIn(stolen.object_id, game.state.players["A"].zones["hand"])
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.replay(game, load=True)

    def test_actual_echoing_truth_reveals_the_departing_nameless_target_only(self):
        game = self.session(201200616)
        spell = self.add(game.engine, TRUTH, zone="hand")
        first = self.add(game.engine, MORPH, zone="hand", ref="truth-hidden-target")
        second = self.add(game.engine, MORPH, zone="hand", ref="truth-other-hidden")
        self._start(game, (spell, first, second), {"C": 7, "U": 1})
        self.checkpoint(game)
        for card in (first, second):
            self.priority(game, "A")
            self._act(game, "A", {"action_id": f"cast-morph:{card.ref}", "pay": "manual", "payment": {"C": 3}})
            self.assertIsNone(self.resolve(game))
            self.assertTrue(game.state.cards[card.object_id].face_down)
        self.assertNotIn(MORPH, json.dumps(game.packet("pilot:C", full=True)))
        self.cast(game, "A", spell, [first.ref])
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self._assert_batch(game, cursor, "hand", (first,))
        reveal, = [event for event in game.state.events[cursor:] if event.code == "card.reveal"]
        self.assertEqual((first.ref, MORPH), (reveal.details["object"], reveal.details["card"]))
        self.assertTrue({"A", "B", "C", "D"}.issubset(reveal.visibility))
        self.assertTrue(game.state.cards[second.object_id].face_down)
        for seat in "BCD":
            packet = game.packet("pilot:" + seat, full=True)["state"]
            self.assertIn(MORPH, json.dumps(packet))
            permanent, = [row for row in packet["players"]["A"]["bf"] if row.get("id") == second.ref]
            self.assertNotEqual(MORPH, permanent.get("n"))
            self.assertNotEqual(second.oracle_id[:8], permanent.get("cid"))
        self.replay(game, load=True)

    def test_actual_echoing_truth_does_not_return_matches_after_the_anchor_departs(self):
        game = self.session(201200617)
        spell = self.add(game.engine, TRUTH, zone="hand")
        anchor = self.add(game.engine, "Grizzly Bears", seat="B", ref="truth-departing")
        other = self.add(game.engine, "Grizzly Bears", seat="C", ref="truth-retained")
        response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self._start(game, (spell, response), {"C": 1, "U": 1})
        game.state.players["B"].mana_pool["U"] = 1
        self.checkpoint(game)
        self.cast(game, "A", spell, [anchor.ref])
        self.cast(game, "B", response, [anchor.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[anchor.object_id].zone)
        game = self.replay(game, load=True)
        cursor = len(game.state.events)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[other.object_id].zone)
        self.assertEqual("graveyard", game.state.cards[spell.object_id].zone)
        self.assertFalse(any(event.code == "effect.public_zone_move_set" for event in game.state.events[cursor:]))
        self.replay(game, load=True)

    def test_verified_same_name_zone_withdrawal_keeps_ordinary_public_zone_batches(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Same-name public set authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (EXILE, RETURN, TRUTH):
            rejected, _ = register_card(self.db, registry, name)
            self.assertFalse(rejected["strict_capability_ready"], rejected)
        retained, _ = register_card(self.db, registry, "Inundate")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
