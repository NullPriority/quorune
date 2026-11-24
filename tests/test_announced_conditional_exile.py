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
from quorune.compiler.conditional_exile_templates import conditional_source_exile_template
from quorune.conditional_exile_model import source_conditional_bindings
from quorune.deck import DeckDefinition, DeckEntry
from quorune.linked_exile_return_model import (
    ANNOUNCED_OPPONENT_EXILE_CAPABILITY as CAPABILITY,
    ANNOUNCED_OPPONENT_EXILE_VERSION as VERSION,
    LinkedExileReturnSpec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.rules.linked_exile_return_shapes import announced_opponent_exile_target_schema
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
GRASP = "Grasp of Fate"


class AnnouncedConditionalExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-public-state-cards.json": {"Raise the Alarm"},
            "activation-view-equivalence-cards.json": {"Ornithopter"},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "conditional-exile-cards.json": {"Banishing Light", "Disenchant"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
            "token-copy-recipe-cards.json": {"Elminster's Simulacrum"},
        }
        parts = [FIXTURES / "announced-conditional-exile-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "announced-conditional-exile.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Announced conditional exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana={"C": 1, "W": 2})

    def _begin_entry_targets(self, game, source):
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.assertEqual("semantic.target", game.state.pending_decision.kind)
        choose, = [row for row in game.packet("pilot:A", full=True)["decision"]["legal_actions"] if row["id"] == "choose"]
        return choose["target_schema"]

    def _refuse_targets(self, game, targets):
        before = authoritative_state_hash(game.state)
        result = game.act("pilot:A", {"action_id": "choose", "targets": [card.ref for card in targets]})
        self.assertFalse(result.ok, result.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _bindings(self, game, source):
        return source_conditional_bindings(game.state.cards[source.object_id])

    def test_original_grasp_requires_one_optional_retained_slot_per_opponent_and_the_exact_pair(self):
        record = self.db.lookup(GRASP, fuzzy=False)
        report, semantics = register_card(self.db, self.registry, GRASP)
        self.assertTrue(report["strict_capability_ready"], report["blockers"])
        self.assertEqual("capability_closed", report["trust_basis"])
        program, = semantics.programs_for_oracle(record.oracle_id)
        self.assertEqual("permanent.enter.self", program.event)
        self.assertEqual(announced_opponent_exile_target_schema(), program.target_schema)
        self.assertEqual({"selection": "up_to", "parameter": "any_number", "schema_version": 2},
                         program.target_schema["count_parameter"])
        self.assertIs(program.target_schema["distinct_controllers"], True)
        self.assertIs(program.target_schema["retain_controller"], True)
        first, second = program.effects
        self.assertEqual(LinkedExileReturnSpec(schema_version=VERSION, timing="source_departure", announced_opponents=True),
                         LinkedExileReturnSpec.from_dict(first["spec"]))
        self.assertEqual(first["spec"], second["spec"])
        self.assertEqual(first["binding_id"], second["binding_id"])
        self.assertEqual("exile", first["phase"])
        self.assertEqual("return", second["phase"])
        for fields in (
            {"target_schema": {**program.target_schema, "retain_controller": False}},
            {"target_schema": {**program.target_schema, "distinct_controllers": False}},
            {"effects": [{**first, "spec": {**first["spec"], "announced_opponents": False}}, second]},
            {"event": "resolve"},
        ):
            binding = bind_semantic_program_runtime(replace(program, **fields), capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], fields)
        deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
        omitted = replace(program, capability_dependencies=deps,
                          capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
        binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
        self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        body = record.oracle_text.split(", ", 1)[1].split(" (", 1)[0]
        for near in (body.replace("each opponent", "each player"), body.replace("up to one", "one"),
                     body.replace("that player controls", "you control")):
            self.assertIsNone(conditional_source_exile_template(near, card_name=GRASP, source_is_permanent=True))
        with patch("quorune.compiler.conditional_exile_templates._ANNOUNCED_OPPONENTS") as grammar:
            grammar.fullmatch.return_value = None
            residual, _ = register_card(self.db, self.registry, GRASP)
            self.assertFalse(residual["strict_capability_ready"], residual)

    def test_actual_grasp_can_choose_zero_even_with_available_opposing_permanents(self):
        game = self.session(61003061)
        source = self.add(game.engine, GRASP, zone="hand")
        opposing = self.add(game.engine, "Grizzly Bears", seat="B", ref="grasp-available")
        friendly = self.add(game.engine, "Grizzly Bears", ref="grasp-own")
        self._start(game, (source,))
        self.checkpoint(game)
        schema = self._begin_entry_targets(game, source)
        self.assertIn(opposing.ref, schema["legal_refs"])
        self.assertNotIn(friendly.ref, schema["legal_refs"])
        self._refuse_targets(game, (friendly,))
        self.target(game, "A", ())
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("battlefield", game.state.cards[opposing.object_id].zone)
        self.assertFalse(self._bindings(game, source))
        self.replay(game, load=True)

    def test_actual_grasp_holds_one_per_opponent_and_returns_only_the_physical_cards(self):
        game = self.session(61003062)
        source = self.add(game.engine, GRASP, zone="hand")
        first = self.add(game.engine, "Grizzly Bears", seat="B", ref="grasp-first-card")
        extra = self.add(game.engine, "Ornithopter", seat="B", ref="grasp-duplicate-controller")
        second = self.add(game.engine, "Ornithopter", seat="D", ref="grasp-second-card")
        raise_alarm = self.add(game.engine, "Raise the Alarm", seat="C", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="grasp-private")
        self._start(game, (source, raise_alarm, removal))
        game.state.players["B"].mana_pool.update({"C": 1, "W": 1})
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "C", raise_alarm)
        self.assertIsNone(self.resolve(game))
        tokens = [c for c in game.state.cards.values() if c.is_token and c.controller == "C" and c.zone == "battlefield"]
        token, retained_token = tokens
        schema = self._begin_entry_targets(game, source)
        self.assertTrue({first.ref, extra.ref, second.ref, token.ref} <= set(schema["legal_refs"]))
        self._refuse_targets(game, (first, extra))
        self.target(game, "A", (first.ref, token.ref, second.ref))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        binding, = self._bindings(game, source)
        self.assertEqual({first.object_id, second.object_id}, {row.object_id for row in binding.objects})
        self.assertEqual(("exile", "exile", "outside", "battlefield", "battlefield"),
                         tuple(game.state.cards[c.object_id].zone for c in (first, second, token, retained_token, extra)))
        self.cast(game, "B", removal, [source.ref])
        self.assertIsNone(self.resolve(game))
        returned = [game.state.cards[c.object_id] for c in (first, second)]
        self.assertTrue(all(c.zone == "battlefield" and c.controller == c.owner for c in returned))
        self.assertEqual(1, len({c.zone_timestamp for c in returned}))
        self.assertEqual("outside", game.state.cards[token.object_id].zone)
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_grasp_revalidates_each_original_opponent_slot_after_control_changes(self):
        for retain_one in (False, True):
            with self.subTest(retain_one=retain_one):
                game = self.session(61003063 + retain_one)
                source = self.add(game.engine, GRASP, zone="hand")
                totem = self.add(game.engine, "Avarice Totem", seat="B")
                first = self.add(game.engine, "Grizzly Bears", seat="B", ref="grasp-slot-B")
                second = self.add(game.engine, "Ornithopter", seat="C", ref="grasp-slot-C")
                self._start(game, (source, totem))
                game.state.players["B"].mana_pool["C"] = 5
                self.checkpoint(game)
                self._begin_entry_targets(game, source)
                self.target(game, "A", (first.ref, second.ref) if retain_one else (second.ref,))
                self.priority(game, "B")
                rows = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                offer, = [row for row in rows if row["id"].startswith(f"activate:{totem.ref}:")]
                used = game.act("pilot:B", {"action_id": offer["id"], "targets": [second.ref],
                                            "pay": "manual", "payment": {"C": 5}})
                self.assertTrue(used.ok, used.summary)
                self.resolve_top(game)
                self.assertEqual("B", game.state.cards[second.object_id].controller)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual("battlefield", game.state.cards[second.object_id].zone)
                self.assertEqual("exile" if retain_one else "battlefield", game.state.cards[first.object_id].zone)
                bindings = self._bindings(game, source)
                self.assertEqual(1 if retain_one else 0, len(bindings))
                if retain_one:
                    held, = bindings[0].objects
                    self.assertEqual(first.object_id, held.object_id)
                self.replay(game, load=True)

    def test_verified_grasp_withdrawal_keeps_standalone_target_and_duration_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Announced opponent source-duration exile withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        unsupported, _ = register_card(self.db, registry, GRASP)
        self.assertFalse(unsupported["strict_capability_ready"], unsupported)
        for name in ("Banishing Light", "Elminster's Simulacrum"):
            retained, _ = register_card(self.db, registry, name)
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
