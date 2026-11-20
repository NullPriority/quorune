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
from quorune.model import GameState
from quorune.next_player_untap_model import NextPlayerUntapRestriction, NextPlayerUntapScope
from quorune.oracle_ir import compile_oracle_card
from quorune.record import authoritative_state_hash
from quorune.rules.capabilities import load_default_capability_registry
from quorune.untap_step import plan_untap_step
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
ORIGINALS = FIXTURES / "next-player-untap-restrictions-cards.json"
EXHAUSTION = "Exhaustion"
VAPORS = "Mana Vapors"
RESTRICTION_CAPABILITY = "untap.step.prohibit_next_controller"
STATIC_PARTICIPATION = "untap.step.static_participation"


class NextPlayerUntapRestrictionTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        snapshots = {
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "targeted-graveyard-exile-cards.json": {
                "Sliver Hivelord", "Grizzly Bears", "Sol Ring", "Shock", "Forest", "Island", "Swamp",
            },
            "fixed-untap-participation-cards.json": {"Back to Basics", "Twiddle"},
            "library-search-rules-cards.json": {"Flooded Strand"},
            "static-step-skip-cards.json": {"Stasis"},
            "fixed-extra-turn-cards.json": {"Time Warp"},
        }
        fixtures = [ORIGINALS]
        for name, cards in snapshots.items():
            source = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            source["cards"] = [card for card in source["cards"] if card["name"] in cards]
            assert {card["name"] for card in source["cards"]} == cards
            oracle_ids = {card["oracle_id"] for card in source["cards"]}
            source["rulings"] = [ruling for ruling in source.get("rulings", ()) if ruling["oracle_id"] in oracle_ids]
            fixture = folder / name
            fixture.write_text(json.dumps(source), encoding="utf-8")
            fixtures.append(fixture)
        path = folder / "next-player-untap.sqlite3"
        build_fixture_database(fixtures, path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Next actual untap", [
            DeckEntry("Sliver Hivelord", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Sliver Hivelord"])

    def test_exact_direct_player_schemas_and_narrow_grammar(self):
        for name, relation, scope in (
            (EXHAUSTION, "opponent", NextPlayerUntapScope.CREATURES_AND_LANDS),
            (VAPORS, "any", NextPlayerUntapScope.LANDS),
        ):
            with self.subTest(name=name):
                card = self.db.lookup(name, fuzzy=False)
                ir = compile_oracle_card(card, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                resolution, = ir.faces[0].nodes
                self.assertEqual("resolve", resolution.event)
                self.assertEqual(relation, resolution.target_schema["player_relation"])
                self.assertEqual({RESTRICTION_CAPABILITY, "target.revalidate_resolution"}, set(resolution.capability_dependencies))
                (effect,) = resolution.effects
                self.assertEqual({"op": "prohibit_next_untap", "schema_version": 2,
                    "player": "$target.0", "scope": scope.value}, effect)
                binding, semantic_registry = register_card(self.db, self.registry, name)
                self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
                program, = semantic_registry.programs_for_oracle(card.oracle_id)
                for changed in (
                    replace(program, effects=[{**effect, "schema_version": True}]),
                    replace(program, effects=[{**effect, "player": "$controller"}]),
                    replace(program, target_schema={**program.target_schema,
                        "player_relation": "any" if relation == "opponent" else "opponent"}),
                    replace(program, event="permanent.enter.self"),
                ):
                    raw = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
                    self.assertFalse(raw["strict"], raw)

                for oracle in (
                    "Artifacts target opponent controls don’t untap during their next untap step.",
                    "Creatures and lands target player controls don’t untap during their next untap step.",
                    "Lands target player controls don’t untap during their next two untap steps.",
                    card.oracle_text + " Perform an unsupported action.",
                ):
                    changed = compile_oracle_card(replace(card, oracle_text=oracle),
                        capability_registry=self.registry, capability_profile=PROFILE)
                    self.assertNotEqual("exact", changed.status, oracle)

    def playing(self, seed):
        game = self.session(seed)
        for player in game.state.players.values():
            for object_id in tuple(player.zones["hand"][3:]):
                game.engine.move_card(object_id, "library", log=False)
        return game

    def activate(self, game, seat, card):
        self.priority(game, seat)
        actions = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        action, = [row for row in actions if row["id"].startswith("activate:" + card.ref + ":")]
        result = game.act("pilot:" + seat, {"action_id": action["id"], "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def test_exhaustion_live_permanents_and_manual_untap(self):
        game = self.playing(50219071)
        spell = self.add(game.engine, EXHAUSTION, zone="hand")
        manual = self.add(game.engine, "Twiddle", seat="B", zone="hand")
        self.add(game.engine, "Island", seat="B")
        fetch = self.add(game.engine, "Flooded Strand", seat="B")
        late = self.add(game.engine, "Island", seat="B", zone="library", ref="next-step-late-land")
        land = self.add(game.engine, "Swamp", seat="B", ref="next-step-stunned-land")
        creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="next-step-restricted-creature")
        manual_creature = self.add(game.engine, "Grizzly Bears", seat="B", ref="next-step-manual-creature")
        artifact = self.add(game.engine, "Sol Ring", seat="B", ref="next-step-artifact")
        hidden = self.add(game.engine, "Shock", seat="D", zone="hand", ref="next-step-private")
        for card in (land, creature, manual_creature, artifact):
            card.tapped = True
        land.counters["stun"] = 1
        for card in (spell, manual, fetch):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 2, "U": 1})
        self.checkpoint(game)
        legacy = game.state.to_dict()
        self.assertNotIn("next_player_untap_restrictions", legacy)
        self.assertEqual(legacy, GameState.from_dict(legacy).to_dict())

        offered = self.action(game, "A", spell)
        invalid = {"action_id": offered["id"], "targets": ["A"], "pay": "auto"}
        before = authoritative_state_hash(game.state)
        self.assertFalse(game.act("pilot:A", invalid).ok)
        self.assertEqual(before, authoritative_state_hash(game.state))
        self.cast(game, "A", spell, ["B"])
        self.assertEqual((), game.state.next_player_untap_restrictions)
        self.resolve_top(game)
        scheduled = (NextPlayerUntapRestriction("B", NextPlayerUntapScope.CREATURES_AND_LANDS),)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)

        self.cast(game, "B", manual, [manual_creature.ref])
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        chosen = game.act("pilot:B", {"action_id": "choose", "choice": "untap"})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertFalse(game.state.cards[manual_creature.object_id].tapped)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)

        self.activate(game, "B", fetch)
        choice = self.resolve(game)
        self.assertIsNotNone(choice)
        self.assertEqual("search.fetch", choice.kind)
        chosen = game.act("pilot:B", {"action_id": "choose", "search_card": late.ref})
        self.assertTrue(chosen.ok, chosen.summary)
        self.assertEqual("graveyard", game.state.cards[fetch.object_id].zone)
        self.assertEqual("battlefield", game.state.cards[late.object_id].zone)
        self.assertFalse(game.state.cards[late.object_id].tapped)
        self.activate(game, "B", late)
        self.assertTrue(game.state.cards[late.object_id].tapped)
        game = self.replay(game, load=True)

        self.until(game, lambda session: session.state.active_player == "B")
        self.assertEqual("upkeep", game.state.step)
        self.assertEqual(1, game.state.cards[land.object_id].counters.get("stun", 0),
            "A prohibited land cannot spend its stun counter")
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        self.assertTrue(game.state.cards[late.object_id].tapped)
        self.assertFalse(game.state.cards[artifact.object_id].tapped)
        self.assertEqual((), game.state.next_player_untap_restrictions)
        for seat in "ABC":
            self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:" + seat, full=True)))
        self.replay(game, load=True)

    def test_mana_vapors_self_target_waits_through_stasis_and_expires_once(self):
        game = self.playing(50219072)
        vapors = [self.add(game.engine, VAPORS, zone="hand", ref=f"vapors-{i}") for i in range(2)]
        warps = [self.add(game.engine, "Time Warp", zone="hand", ref=f"vapors-warp-{i}") for i in range(2)]
        stasis = self.add(game.engine, "Stasis")
        land = self.add(game.engine, "Forest")
        creature = self.add(game.engine, "Grizzly Bears")
        land.tapped = creature.tapped = True
        for card in (vapors[0], warps[0], stasis):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 8, "U": 6})
        self.checkpoint(game)
        sequence = game.state.turn_sequence
        for card in (*vapors, *warps):
            self.cast(game, "A", card, ["A"])
            self.resolve_top(game)
        self.assertEqual(["A", "A"], [entry.player for entry in game.state.extra_turns])
        scheduled = (NextPlayerUntapRestriction("A", NextPlayerUntapScope.LANDS),)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)
        wire = game.state.to_dict()
        field = "next_player_untap_restrictions"
        item, = wire[field]
        self.assertEqual(wire, GameState.from_dict(wire).to_dict())
        for malformed in ([item, item], [{**item, "player": "foreign"}], [{**item, "scope": "artifact"}],
                [{**item, "schema_version": True}], {**item}):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                GameState.from_dict({**wire, field: malformed})

        self.until(game, lambda session: session.state.turn_sequence == sequence + 1)
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertFalse(any(event.code == "step.begin" and event.turn_sequence == sequence + 1
            and event.step == "untap" for event in game.state.events))
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertTrue(game.state.cards[creature.object_id].tapped)
        self.assertEqual(scheduled, game.state.next_player_untap_restrictions)
        self.assertEqual("semantic.choice", self.resolve(game).kind)
        game = self.replay(game, load=True)
        declined = game.act("pilot:A", {"action_id": "choose", "pay": False})
        self.assertTrue(declined.ok, declined.summary)
        self.assertEqual("graveyard", game.state.cards[stasis.object_id].zone)

        self.until(game, lambda session: session.state.turn_sequence == sequence + 2)
        self.assertEqual(("A", "upkeep"), (game.state.active_player, game.state.step))
        self.assertTrue(game.state.cards[land.object_id].tapped)
        self.assertFalse(game.state.cards[creature.object_id].tapped)
        self.assertEqual((), game.state.next_player_untap_restrictions)
        self.assertNotIn(field, game.state.to_dict())
        self.replay(game, load=True)

    def test_losing_planner_prohibition_breaks_native_stun_witness(self):
        def ignore_restrictions(active_player, rows, participations, *, prohibited_object_ids=()):
            return plan_untap_step(active_player, rows, participations)

        with patch("quorune.semantic_runtime.untap_steps.plan_untap_step", side_effect=ignore_restrictions):
            with self.assertRaisesRegex(AssertionError, "cannot spend its stun counter"):
                self.test_exhaustion_live_permanents_and_manual_untap()

    def test_verified_withdrawals_distinguish_pending_and_static_untap_owners(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        source = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())

        def verified(rows):
            registry = CapabilityRegistry(rows)
            index = build_index(registry_value=rows, declaration_source=declarations, discovered_test_ids=known)
            registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
            return registry

        names = (EXHAUSTION, VAPORS, "Back to Basics", "Grizzly Bears")
        baseline = verified(deepcopy(source))
        for name in names:
            ready, _ = register_card(self.db, baseline, name)
            self.assertTrue(ready["strict_capability_ready"], ready["blockers"])
        for capability, blocked in (
            (RESTRICTION_CAPABILITY, {EXHAUSTION, VAPORS}), (STATIC_PARTICIPATION, {"Back to Basics"}),
        ):
            with self.subTest(withdrawn=capability):
                rows = deepcopy(source)
                next(row for row in rows["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Next-player untap owner withdrawn"])
                registry = verified(rows)
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in names:
                    ready, _ = register_card(self.db, registry, name)
                    self.assertEqual(name not in blocked, ready["strict_capability_ready"],
                        (name, ready["blockers"]))
