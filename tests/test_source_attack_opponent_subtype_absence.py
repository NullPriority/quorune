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
from quorune.life_state import LifeChange
from quorune.oracle_ir import compile_oracle_card
from quorune.per_player_query_life_model import (
    OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION,
    PER_PLAYER_QUERY_LIFE_CAPABILITY,
    PerPlayerQueryLifeLossSpec,
)
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "source-attack-opponent-subtype-absence-cards.json"
ARCHER = "Thornbow Archer"
SOURCE_ATTACK = "trigger.event.normalized_self_attack"


class SourceAttackOpponentSubtypeAbsenceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        selected = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "source-attack-alone-cards.json": {
                "Isamaru, Hound of Konda", "Grizzly Bears", "Fervor", "Unsummon", "Island",
            },
            "activation-target-view-cards.json": {"Llanowar Elves"},
            "fixed-behold-additional-cost-cards.json": {"Prowess of the Fair"},
            "per-player-query-life-cards.json": {"Stronghold Discipline"},
        }
        fixtures = [ORIGINALS]
        for name, names in selected.items():
            data = json.loads((FIXTURES / name).read_bytes())
            data["cards"] = [card for card in data["cards"] if card["name"] in names]
            assert {card["name"] for card in data["cards"]} == names
            oracle_ids = {card["oracle_id"] for card in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in oracle_ids]
            fixture = folder / name
            fixture.write_text(json.dumps(data), encoding="utf-8")
            fixtures.append(fixture)
        path = folder / "opponent-subtype-absence.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Current opponent subtype absence", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def test_complete_archer_has_one_exact_attack_and_public_subtype_without_a_target(self):
        card = self.db.lookup(ARCHER, fuzzy=False)
        ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        self.assertEqual("creature.attacks", node.event)
        self.assertIsNone(node.target_schema)
        self.assertTrue({SOURCE_ATTACK, PER_PLAYER_QUERY_LIFE_CAPABILITY} <= set(node.capability_dependencies))
        effect, = node.effects
        self.assertEqual("lose_life_each_opponent", effect["op"])
        raw = effect["amount"]
        spec = PerPlayerQueryLifeLossSpec.from_dict(raw)
        self.assertEqual(OPPONENT_SUBTYPE_ABSENCE_LIFE_VERSION, spec.schema_version)
        query = spec.amount.quantity.query
        self.assertEqual(("elf",), query.subtypes_all)
        self.assertEqual((), query.types_all)
        self.assertEqual([1, 0, 0], [spec.life_loss_for_quantity(n) for n in range(3)])
        self.assertEqual(raw, spec.to_dict())
        prior = PerPlayerQueryLifeLossSpec.from_dict({**raw, "schema_version": 1})
        self.assertEqual([0, 1, 2], [prior.life_loss_for_quantity(n) for n in range(3)])
        self.assertEqual({**raw, "schema_version": 1}, prior.to_dict())

        ready, semantics = register_card(self.db, self.registry, ARCHER)
        self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        program, = semantics.programs_for_oracle(card.oracle_id)
        target = {"zones": ["player"], "categories": ["player"], "count": 1, "player_relation": "opponent"}
        for changed in (
            replace(program, event="permanent.enter.self"),
            replace(program, event_condition=None),
            replace(program, target_schema=target),
            replace(program, effects=[{**effect, "amount": {**raw, "schema_version": 1}}]),
            replace(program, effects=[{**effect, "amount": {**raw, "schema_version": True}}]),
            replace(program, effects=[{**effect, "amount": {**raw, "relation": "all_active_players"}}]),
        ):
            bound = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(bound["strict"], bound)
        for oracle in (
            card.oracle_text.replace("an Elf", "an Elf creature"),
            card.oracle_text.replace("an Elf", "an Elf or a Goblin"),
            card.oracle_text.replace("an Elf", "a hypothetical unknown thing"),
            card.oracle_text.replace("1 life", "2 life"),
            card.oracle_text.replace("each opponent", "each player"),
            card.oracle_text.replace("this creature attacks", "this creature enters"),
            card.oracle_text.replace("each opponent who", "you may have each opponent who"),
        ):
            with self.subTest(oracle=oracle):
                changed = compile_oracle_card(replace(card, oracle_text=oracle),
                    capability_registry=self.registry, capability_profile=PROFILE)
                self.assertNotEqual("exact", changed.status, changed.material_residuals)

    def test_actual_attack_reads_all_current_opponents_and_one_atomic_loss(self):
        from quorune.effect_runtime import life_effects

        game = self.session(10925081)
        archer = self.add(game.engine, ARCHER, zone="hand")
        other = self.add(game.engine, "Grizzly Bears", ref="other-attacker")
        removed = self.add(game.engine, "Llanowar Elves", seat="B", ref="leaving-defender-elf")
        kindred = self.add(game.engine, "Prowess of the Fair", seat="C")
        hidden = self.add(game.engine, "Llanowar Elves", seat="D", zone="hand", ref="private-elf")
        bounce = self.add(game.engine, "Unsummon", zone="hand", ref="return-defending-elf")
        source_bounce = self.add(game.engine, "Unsummon", zone="hand", ref="return-archer")
        self.add(game.engine, "Fervor")
        self.add(game.engine, "Island", ref="one-blue-source")
        self.add(game.engine, "Island", ref="second-blue-source")
        self.assert_closed(game, archer)
        self.assert_closed(game, kindred)
        self.prepare(game, mana={"B": 1})
        self.checkpoint(game)
        self.cast(game, "A", archer)
        self.assertIsNone(self.resolve(game))
        self.attack(game, [(archer, "B"), (other, "C")])
        item, = game.state.stack
        self.assertEqual(archer.object_id, item.source_object_id)
        self.assertEqual([], item.targets)
        self.assertEqual("B", item.context["defending_player"])
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:D", {"action_id": "choose", "targets": ["B"]}).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))

        self.cast(game, "A", bounce, [removed.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[removed.object_id].zone)
        self.cast(game, "A", source_bounce, [archer.ref])
        self.resolve_top(game)
        self.assertEqual("hand", game.state.cards[archer.object_id].zone)
        self.assertEqual([item.ref], [entry.ref for entry in game.state.stack])
        game = self.replay(game, load=True)
        before_life = [game.state.players[seat].life for seat in "ABCD"]
        self.assertEqual([40, 40, 40, 40], before_life)
        observed = []
        original = life_effects._commit

        def commit(host, changes, **kwargs):
            batch = tuple(changes)
            observed.append(batch)
            self.assertEqual(before_life, [host.state.players[seat].life for seat in "ABCD"])
            return original(host, batch, **kwargs)

        with patch.object(life_effects, "_commit", side_effect=commit):
            self.assertIsNone(self.resolve(game))
        self.assertEqual([(LifeChange("B", -1), LifeChange("D", -1))], observed,
            "Every opponent with zero public Elves loses one in the same batch")
        self.assertEqual([40, 39, 40, 39], [game.state.players[seat].life for seat in "ABCD"])
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_count_scaling_instead_of_absence_breaks_the_actual_attack(self):
        with patch.object(PerPlayerQueryLifeLossSpec, "life_loss_for_quantity", side_effect=lambda count: count):
            with self.assertRaisesRegex(AssertionError, "Every opponent with zero public Elves"):
                self.test_actual_attack_reads_all_current_opponents_and_one_atomic_loss()

    def test_verified_withdrawals_preserve_v1_when_only_source_attacks_are_removed(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        prior = "Stronghold Discipline"
        names = (ARCHER, prior, "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for capability, blocked in (
            (SOURCE_ATTACK, {ARCHER}), (PER_PLAYER_QUERY_LIFE_CAPABILITY, {ARCHER, prior}),
        ):
            with self.subTest(capability=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Subtype absence dependency withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))
