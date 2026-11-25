"""Native checks for the chosen-type temporary artifact copy."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from common import ROOT
from inherited_composition_fixture import InheritedNativeFixture
import test_linked_creature_type_targets as linked
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.evidence import validate_capability_evidence_index
from quorune.rules.evidence_storage import read_evidence_document
from quorune.rules.temporary_self_copy import TEMPORARY_ARTIFACT_COPY_CAPABILITY
from scripts.build_test_database import build_fixture_database
from scripts.update_capability_evidence import build_index, discover_tests


class LinkedTemporaryArtifactCopyTests(InheritedNativeFixture, unittest.TestCase):
    copier = "Trial Heraldic Lens"
    other_copier = "Trial Copying Lens"
    matching = "Trial Goblin Lookout"
    nonmatching = "Generic Bound Body"
    wall = "Mistform Wall"
    destroy = "Trial Artifact Break"
    restore = "Trial Swift Rebuild"
    basic = "Generic Bound Plains"

    _reject = linked.LinkedCreatureTypeTargetTests._reject
    _choose = linked.LinkedCreatureTypeTargetTests._choose
    _start = linked.LinkedCreatureTypeTargetTests._start
    _cast_source = linked.LinkedCreatureTypeTargetTests._cast_source
    _offers = linked.LinkedCreatureTypeTargetTests._offers
    _activate = linked.LinkedCreatureTypeTargetTests._activate
    _wall_type = linked.LinkedCreatureTypeTargetTests._wall_type

    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        fixtures = ROOT / "tests/fixtures"
        wall_row = json.loads((fixtures / "temporary-source-keyword-cards.json").read_bytes())
        wall_row["cards"] = [row for row in wall_row["cards"] if row["name"] == cls.wall]
        assert len(wall_row["cards"]) == 1
        ids = {row["oracle_id"] for row in wall_row["cards"]}
        wall_row["rulings"] = [row for row in wall_row.get("rulings", []) if row["oracle_id"] in ids]
        wall = folder / "wall.json"
        wall.write_text(json.dumps(wall_row), encoding="utf-8")
        database = folder / "linked-temporary-artifact-copy.sqlite3"
        build_fixture_database([
            fixtures / "bound-effect-program-cards.json",
            fixtures / "temporary-self-copy-cards.json",
            fixtures / "linked-temporary-artifact-copy-cards.json",
            wall,
        ], database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Linked temporary artifact copy", [
            DeckEntry("Generic Bound Commander", 1, "commander"), DeckEntry(cls.basic, 30),
        ], ["Generic Bound Commander"])

    def test_paid_chosen_type_copy_adds_copiable_artifact_and_expires(self):
        game = self.session(707_221)
        source = self.add(game.engine, self.copier, zone="hand")
        copier = self.add(game.engine, self.other_copier)
        allowed = self.add(game.engine, self.matching)
        foreign = self.add(game.engine, self.matching, seat="B", ref="foreign-matching-creature")
        wrong = self.add(game.engine, self.nonmatching)
        hidden = self.add(game.engine, self.matching, seat="D", zone="hand", ref="private-matching-creature")
        allowed.counters["+1/+1"] = 2
        self.assert_closed(game, source)
        self.assert_closed(game, copier)
        self._start(game, {"C": 5})
        self._cast_source(game, source)
        incarnation = game.state.cards[source.object_id].logical_object_id
        offer, = self._offers(game, source)
        self.assertEqual({allowed.ref}, set(offer["target_schema"]["legal_refs"]))
        self.assertNotIn(hidden.ref, json.dumps(offer))
        self._activate(game, source, allowed, (foreign, wrong, hidden), mana={"C": 1})
        game = self.replay(game, load=True)
        self.resolve_top(game)
        live = game.state.cards[source.object_id]
        self.assertEqual(incarnation, live.logical_object_id)
        self.assertEqual(self.matching, game.engine.display_name(live.object_id))
        types, subtypes, _ = game.engine._effective_type_parts(live)
        self.assertEqual({"artifact", "creature"}, set(types))
        self.assertIn("goblin", subtypes)
        self.assertEqual(game.engine._numeric_stat(allowed.object_id, "power") - 2,
                         game.engine._numeric_stat(source.object_id, "power"))
        self.assertEqual("goblin", live.annotations["chosen_creature_type"])
        self._activate(game, copier, live, mana={"C": 2})
        self.resolve_top(game)
        follower = game.state.cards[copier.object_id]
        follower_types, follower_subtypes, _ = game.engine._effective_type_parts(follower)
        self.assertEqual({"artifact", "creature"}, set(follower_types))
        self.assertIn("goblin", follower_subtypes)
        self.assertEqual(self.matching, game.engine.display_name(follower.object_id))
        game = self.replay(game, load=True)
        turn = game.state.turn_sequence
        self.until(game, lambda s: s.state.turn_sequence > turn)
        self.assertEqual(self.copier, game.engine.display_name(source.object_id))
        self.assertEqual(self.other_copier, game.engine.display_name(copier.object_id))
        self.assertEqual("goblin", game.state.cards[source.object_id].annotations["chosen_creature_type"])
        self.replay(game, load=True)

    def test_linked_copy_revalidates_the_chosen_creature_type_at_resolution(self):
        game = self.session(707_222)
        source = self.add(game.engine, self.copier, zone="hand")
        wall = self.add(game.engine, self.wall)
        self.assert_closed(game, source)
        self.assert_closed(game, wall)
        self._start(game, {"C": 6})
        self._cast_source(game, source)
        self.assertEqual([], self._offers(game, source))
        self._wall_type(game, wall, "Goblin")
        self.assertIsNone(self.resolve(game))
        self._activate(game, source, wall, mana={"C": 1})
        game = self.replay(game, load=True)
        self._wall_type(game, game.state.cards[wall.object_id], "Cat")
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[source.object_id]
        self.assertEqual(self.copier, game.engine.display_name(live.object_id))
        self.assertNotIn("copy_overrides", live.annotations)
        self.assertIn("cat", game.engine._effective_type_parts(game.state.cards[wall.object_id])[1])
        self.replay(game, load=True)

    def test_linked_copy_uses_the_old_incarnation_after_a_new_entry_choice(self):
        game = self.session(707_223)
        source = self.add(game.engine, self.copier, zone="hand")
        target = self.add(game.engine, self.matching)
        destroy = self.add(game.engine, self.destroy, seat="B", zone="hand")
        restore = self.add(game.engine, self.restore, zone="hand")
        for card in (source, destroy, restore):
            self.assert_closed(game, card)
        game.state.players["B"].mana_pool.update({"W": 1, "C": 1})
        self._start(game, {"C": 4, "W": 1})
        self._cast_source(game, source)
        initial = game.state.cards[source.object_id].logical_object_id
        self._activate(game, source, target, mana={"C": 1})
        self.cast(game, "B", destroy, [source.ref])
        self.resolve_top(game)
        current = game.state.cards[source.object_id]
        self.assertEqual("graveyard", current.zone)
        self.cast(game, "A", restore, [current.ref])
        self.until(game, lambda s: s.state.pending_decision.kind == "replacement.order")
        options = game.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        row, = [choice for choice in options if choice["id"].endswith(":elf")]
        self._choose(game, {"action_id": "choose", "replacement": row["id"]})
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        live = game.state.cards[source.object_id]
        self.assertEqual("battlefield", live.zone)
        self.assertNotEqual(initial, live.logical_object_id)
        self.assertEqual("elf", live.annotations["chosen_creature_type"])
        self.assertEqual(self.copier, game.engine.display_name(live.object_id))
        self.assertNotIn("copy_overrides", live.annotations)
        self.replay(game, load=True)

    def test_linked_copy_requires_the_source_entry_choice(self):
        game = self.session(707_224)
        source = self.add(game.engine, self.copier)
        target = self.add(game.engine, self.matching)
        self.assert_closed(game, source)
        self._start(game, {"C": 2})
        self.assertEqual([], self._offers(game, source))
        ability, = game.engine._activated_abilities(source)
        self._reject(game, "A", {"action_id": f"activate:{source.ref}:{ability.ability_id}",
                                  "targets": [target.ref], "pay": "auto"})

    def test_linked_artifact_exception_authority_keeps_base_copy_and_entry_independent(self):
        card = self.db.lookup(self.copier)
        def compile(rec, registry):
            return compile_oracle_card(rec, capability_registry=registry, capability_profile="commander_review")
        exact = compile(card, self.registry)
        self.assertEqual("exact", exact.status, exact.material_residuals)
        node, = [n for face in exact.faces for n in face.nodes if n.event == "activate"]
        self.assertIn(TEMPORARY_ARTIFACT_COPY_CAPABILITY, node.capability_dependencies)
        raw = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        capability = next(row for row in raw["capabilities"] if row["id"] == TEMPORARY_ARTIFACT_COPY_CAPABILITY)
        capability.update(status="blocked", blockers=["Artifact copy exception withdrawn."])
        withdrawn = CapabilityRegistry(raw)
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        index = build_index(registry_value=raw, declaration_source=declarations,
                            discovered_test_ids=set(discover_tests(ROOT).values()))
        withdrawn.mark_evidence_verified(validate_capability_evidence_index(index, registry=withdrawn))
        partial = compile(card, withdrawn)
        self.assertNotEqual("exact", partial.status)
        self.assertTrue(any(n.exact and n.event == "zone.change" for f in partial.faces for n in f.nodes))
        self.assertEqual("exact", compile(self.db.lookup(self.other_copier), withdrawn).status)
        missing_choice = replace(card, oracle_text=card.oracle_text.split("\n", 1)[1])
        self.assertNotEqual("exact", compile(missing_choice, self.registry).status)
        with patch("quorune.compiler.temporary_self_copy_templates._linked_temporary_artifact_copy", return_value=None):
            self.assertNotEqual("exact", compile(card, self.registry).status)
