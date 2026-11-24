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
    SAME_NAME_CONDITIONAL_EXILE_CAPABILITY as CAPABILITY,
    SAME_NAME_CONDITIONAL_EXILE_VERSION as VERSION,
    LinkedExileReturnSpec,
    SameNameConditionalExileScope as Scope,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


FIXTURES = ROOT / "tests" / "fixtures"
PROFILE = "commander_review"
BANISHMENT = "Banishment"
DEPUTY = "Deputy of Detention"
WURM = "Spined Wurm"


class SameNameConditionalExileTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "activation-paid-result-slot-cards.json": {WURM},
            "attached-destruction-set-cards.json": {"Grizzly Bears"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "conditional-exile-cards.json": {"Banishing Light", "Disenchant", "Unsummon"},
            "creature-subtype-addition-cards.json": {"Isamaru, Hound of Konda"},
            "fixed-basic-type-setting-cards.json": {"Song of the Dryads"},
            "permanent-control-exchange-cards.json": {"Avarice Totem"},
        }
        parts = [FIXTURES / "same-name-conditional-exile-cards.json"]
        for source, names in support.items():
            doc = json.loads((FIXTURES / source).read_bytes())
            cards = [row for row in doc["cards"] if row["name"] in names]
            assert {row["name"] for row in cards} == names, source
            ids = {row["oracle_id"] for row in cards}
            rulings = [row for row in doc.get("rulings", ()) if row["oracle_id"] in ids]
            part = folder / source
            part.write_text(json.dumps({**doc, "cards": cards, "rulings": rulings}), encoding="utf-8")
            parts.append(part)
        path = folder / "same-name-conditional-exile.sqlite3"
        build_fixture_database(parts, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Same-name conditional exile", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def _start(self, game, cards, mana, **kwargs):
        for card in cards:
            self.assert_closed(game, card)
        game.state.config.semantic_policy = "trusted_only"
        self.prepare(game, mana=mana, **kwargs)

    def _zones(self, game, cards):
        return tuple(game.state.cards[card.object_id].zone for card in cards)

    def _verify_hold(self, game, source, exiled):
        source = game.state.cards[source.object_id]
        binding, = source_conditional_bindings(source)
        self.assertEqual(source.logical_object_id, binding.source.logical_object_id)
        self.assertEqual({c.object_id for c in exiled}, {row.object_id for row in binding.objects})
        self.assertTrue(all(row.matches_exiled_card(game.state.cards[row.object_id]) for row in binding.objects))

    def _verify_return(self, game, cards):
        current = [game.state.cards[card.object_id] for card in cards]
        self.assertTrue(all(card.zone == "battlefield" and card.controller == card.owner for card in current))
        self.assertEqual(1, len({card.zone_timestamp for card in current}))

    def test_two_intact_originals_close_the_single_anchor_and_separate_controller_domains(self):
        identifiers = set()
        for name, scope in ((BANISHMENT, Scope.OPPONENTS), (DEPUTY, Scope.ANCHOR_CONTROLLER)):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                report, semantics = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])
                self.assertEqual("capability_closed", report["trust_basis"])
                programs = semantics.programs_for_oracle(card.oracle_id)
                program, = (p for p in programs if CAPABILITY in p.capability_dependencies)
                self.assertEqual("permanent.enter.self", program.event)
                self.assertEqual({"zones": ["battlefield"], "categories": ["permanent"], "types_none": ["land"],
                                  "count": 1, "controller_relation": "opponent"}, program.target_schema)
                first, second = program.effects
                spec = LinkedExileReturnSpec.from_dict(first["spec"])
                self.assertEqual((VERSION, "source_departure", scope), (spec.schema_version, spec.timing, spec.same_name_scope))
                self.assertEqual(first["spec"], second["spec"])
                self.assertEqual(first["binding_id"], second["binding_id"])
                self.assertEqual("$targets", first["cards"])
                identifiers.add(first["binding_id"])
                if name == BANISHMENT:
                    self.assertTrue(any(p.event == "cast.permission" for p in programs))
                for changed in (
                    replace(program, effects=[{**first, "spec": {**first["spec"], "same_name_scope": "all"}}, second]),
                    replace(program, effects=[{**first, "cards": "$source.zone_object"}, second]),
                    replace(program, target_schema={**program.target_schema, "controller_relation": "any"}),
                    replace(program, event="resolve"),
                ):
                    binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(binding["strict"], binding)
                deps = tuple(value for value in program.capability_dependencies if value != CAPABILITY)
                omitted = replace(program, capability_dependencies=deps,
                                  capability_closure=self.registry.closure(deps, profile=PROFILE).to_dict())
                binding = bind_semantic_program_runtime(omitted, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + CAPABILITY, binding["blockers"])
        self.assertEqual(2, len(identifiers))
        self.assertEqual({"schema_version": 2, "timing": "source_departure", "controller": "owner",
                          "tapped": False, "entry_counters": [], "keywords": []},
                         LinkedExileReturnSpec(schema_version=2, timing="source_departure").to_dict())
        original = self.db.lookup(DEPUTY, fuzzy=False)
        body = original.oracle_text.split(", ", 1)[1]
        for near in (body.replace("all other nonland", "all other"),
                     body.replace("that player controls", "you control"),
                     body.replace("leaves the battlefield", "is tapped")):
            self.assertIsNone(conditional_source_exile_template(
                near, card_name=DEPUTY, source_is_permanent=True,
            ))

    def test_fresh_same_name_producer_omission_removes_the_complete_originals(self):
        def witness():
            for name in (BANISHMENT, DEPUTY):
                report, _ = register_card(self.db, self.registry, name)
                self.assertTrue(report["strict_capability_ready"], report["blockers"])

        witness()
        with patch("quorune.compiler.conditional_exile_templates._SAME_NAME") as grammar:
            grammar.fullmatch.return_value = None
            with self.assertRaises(AssertionError):
                witness()
        witness()

    def test_actual_banishment_uses_flash_and_holds_only_the_opposing_nonland_matches(self):
        game = self.session(61003051)
        source = self.add(game.engine, BANISHMENT, zone="hand")
        anchor = self.add(game.engine, WURM, seat="B", ref="banishment-target")
        opposing = self.add(game.engine, WURM, seat="C", ref="banishment-opposing-match")
        land = self.add(game.engine, WURM, seat="D", ref="banishment-land")
        friendly = self.add(game.engine, WURM, ref="banishment-friendly")
        song = self.add(game.engine, "Song of the Dryads", seat="B", zone="hand")
        removal = self.add(game.engine, "Disenchant", seat="B", zone="hand")
        private = self.add(game.engine, "Generic Bound Plains", seat="D", zone="hand", ref="banishment-private")
        self._start(game, (source, song, removal), {"C": 3, "W": 1}, active="B")
        game.state.players["B"].mana_pool.update({"C": 3, "G": 1, "W": 1})
        self.checkpoint(game)
        self.cast(game, "B", song, [land.ref])
        self.assertIsNone(self.resolve(game))
        values = game.engine._effective_card_data(land.object_id)
        self.assertEqual(WURM, values["name"])
        self.assertIn("land", game.engine._type_parts(values["type_line"])[0])
        self.assertEqual("B", game.state.active_player)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.target(game, "A", (anchor.ref,))
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "exile", "battlefield", "battlefield"),
                         self._zones(game, (anchor, opposing, land, friendly)))
        self._verify_hold(game, source, (anchor, opposing))
        self.cast(game, "B", removal, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("graveyard", "battlefield", "battlefield"), self._zones(game, (source, land, friendly)))
        self._verify_return(game, (anchor, opposing))
        for seat in "ABC":
            self.assertNotIn(private.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_actual_deputy_uses_the_targets_current_controller_and_returns_to_the_owners(self):
        game = self.session(61003052)
        source = self.add(game.engine, DEPUTY, zone="hand")
        totem = self.add(game.engine, "Avarice Totem", seat="B")
        anchor = self.add(game.engine, WURM, seat="C", ref="deputy-current-target")
        new_match = self.add(game.engine, WURM, seat="B", ref="deputy-current-member")
        old_match = self.add(game.engine, WURM, seat="C", ref="deputy-old-member")
        other = self.add(game.engine, WURM, seat="D", ref="deputy-unrelated-opponent")
        bounce = self.add(game.engine, "Unsummon", seat="B", zone="hand")
        self._start(game, (source, totem, bounce), {"C": 1, "W": 1, "U": 1})
        game.state.players["B"].mana_pool.update({"C": 5, "U": 1})
        self.checkpoint(game)
        self.cast(game, "A", source)
        self.resolve_top(game)
        self.target(game, "A", (anchor.ref,))
        self.priority(game, "B")
        actions = game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
        activation, = [row for row in actions if row["id"].startswith(f"activate:{totem.ref}:")]
        used = game.act("pilot:B", {"action_id": activation["id"], "targets": [anchor.ref],
                                    "pay": "manual", "payment": {"C": 5}})
        self.assertTrue(used.ok, used.summary)
        self.resolve_top(game)
        self.assertEqual("B", game.state.cards[anchor.object_id].controller)
        self.assertEqual("C", game.state.cards[anchor.object_id].owner)
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(("exile", "exile", "battlefield", "battlefield"),
                         self._zones(game, (anchor, new_match, old_match, other)))
        self._verify_hold(game, source, (anchor, new_match))
        self.cast(game, "B", bounce, [source.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[source.object_id].zone)
        self._verify_return(game, (anchor, new_match))
        self.replay(game, load=True)

    def test_actual_deputy_exiles_nothing_if_its_source_or_only_target_leaves_first(self):
        for remove_source in (False, True):
            with self.subTest(remove_source=remove_source):
                game = self.session(61003053 + remove_source)
                source = self.add(game.engine, DEPUTY, zone="hand")
                anchor = self.add(game.engine, "Grizzly Bears", seat="B", ref="deputy-lost-target")
                other = self.add(game.engine, "Grizzly Bears", seat="B", ref="deputy-retained-match")
                response = self.add(game.engine, "Unsummon", seat="B", zone="hand")
                self._start(game, (source, response), {"C": 1, "W": 1, "U": 1})
                game.state.players["B"].mana_pool["U"] = 1
                self.checkpoint(game)
                self.cast(game, "A", source)
                self.resolve_top(game)
                self.target(game, "A", (anchor.ref,))
                self.cast(game, "B", response, [(source if remove_source else anchor).ref])
                self.resolve_top(game)
                game = self.replay(game, load=True)
                self.assertIsNone(self.resolve(game))
                self.assertEqual(("hand", "battlefield", "battlefield") if remove_source
                                 else ("battlefield", "hand", "battlefield"),
                                 self._zones(game, (source, anchor, other)))
                self.assertFalse(source_conditional_bindings(game.state.cards[source.object_id]))
                self.replay(game, load=True)

    def test_verified_same_name_withdrawal_keeps_ordinary_banishing_light(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        next(row for row in raw["capabilities"] if row["id"] == CAPABILITY).update(
            status="blocked", blockers=["Same-name source-departure exile authority withdrawn"],
        )
        registry = CapabilityRegistry(raw)
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
        closure = registry.closure((CAPABILITY,), profile=PROFILE)
        self.assertIn(f"status:{CAPABILITY}:blocked", closure.blockers)
        self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
        for name in (BANISHMENT, DEPUTY):
            report, _ = register_card(self.db, registry, name)
            self.assertFalse(report["strict_capability_ready"], report)
        retained, _ = register_card(self.db, registry, "Banishing Light")
        self.assertTrue(retained["strict_capability_ready"], retained["blockers"])


if __name__ == "__main__":
    unittest.main()
