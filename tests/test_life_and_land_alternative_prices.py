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
from quorune.public_alternative_costs import (
    FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY,
    FixedPublicAlternativeCondition,
    FixedPublicAlternativeCostError,
    FixedPublicAlternativeCostSpec,
)
from quorune.public_history_query_model import PUBLIC_HISTORY_QUERY_CAPABILITY
from quorune.record import authoritative_state_hash
from quorune.replacement.immutable import thaw_value
from quorune.rules.capabilities import load_default_capability_registry
from quorune.util import mana_cost_to_vector
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PROFILE = "commander_review"
FIXTURES = ROOT / "tests" / "fixtures"
PRICES = {
    "Needlebite Trap": ("opponent_gained_life", "{B}", ["lose_life", "life"]),
    "Lavaball Trap": ("opponent_land_arrivals", "{3}{R}{R}", ["destroy_targets", "damage_fixed_set"]),
}


class LifeAndLandAlternativePriceTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        support = {
            "ability-label-composition-cards.json": {"Plains"},
            "ability-source-counter-cards.json": {"Shock"},
            "activation-restriction-material-cards.json": {"Memnite"},
            "activation-result-views-cards.json": {"Mountain"},
            "additional-conditional-modifier-cards.json": {"Revitalize"},
            "bound-effect-program-cards.json": {"Generic Bound Plains"},
            "library-search-rules-cards.json": {"Flooded Strand"},
            "life-and-land-alternative-price-cards.json": set(PRICES),
            "source-attack-alone-cards.json": {"Isamaru, Hound of Konda"},
        }
        pieces = []
        for filename, names in support.items():
            data = json.loads((FIXTURES / filename).read_bytes())
            data["cards"] = [row for row in data["cards"] if row["name"] in names]
            assert {row["name"] for row in data["cards"]} == names
            ids = {row["oracle_id"] for row in data["cards"]}
            data["rulings"] = [row for row in data.get("rulings", ()) if row["oracle_id"] in ids]
            piece = folder / filename
            piece.write_text(json.dumps(data), encoding="utf-8")
            pieces.append(piece)
        database = folder / "life-and-land-alternative-prices.sqlite3"
        build_fixture_database(pieces, database)
        cls.db = CardDatabase(database)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Opponent life and land arrivals", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"),
            DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def original_programs(self, name):
        card = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding)
        return card, {program.event: program for program in semantics.programs_for_oracle(card.oracle_id)}

    def test_both_pinned_life_and_land_prices_preserve_each_independent_result(self):
        for name, (condition, cost, operations) in PRICES.items():
            with self.subTest(card=name):
                original, programs = self.original_programs(name)
                ir = compile_oracle_card(original, capability_registry=self.registry, capability_profile=PROFILE)
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertEqual({"cast.cost", "resolve"}, programs.keys())
                price = programs["cast.cost"]
                raw = thaw_value(price.handlers[0]["alternative_cost"])
                spec = FixedPublicAlternativeCostSpec.from_dict(raw)
                self.assertEqual((4, condition, cost),
                                 (spec.condition.schema_version, spec.condition.kind.value, spec.cost_text))
                self.assertEqual(raw, spec.to_dict())
                dependencies = {FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY}
                self.assertLessEqual(dependencies, set(price.capability_dependencies))
                self.assertEqual(operations, [effect["op"] for effect in programs["resolve"].effects])

    def test_life_and_land_price_specs_reject_legacy_widened_and_undeclared_history(self):
        for name in PRICES:
            with self.subTest(card=name):
                _, programs = self.original_programs(name)
                program = programs["cast.cost"]
                raw = thaw_value(program.handlers[0]["alternative_cost"])
                condition = raw["condition"]
                for fields in ({"schema_version": 3}, {"schema_version": 4.0}, {"kind": "always"},
                               {"opponent_basic_land_type": "forest"}):
                    with self.subTest(fields=fields), self.assertRaises(FixedPublicAlternativeCostError):
                        FixedPublicAlternativeCondition.from_dict({**condition, **fields})
                requirements, _ = mana_cost_to_vector("{R}")
                with self.assertRaises(FixedPublicAlternativeCostError):
                    FixedPublicAlternativeCostSpec.from_dict({**raw, "cost_text": "{R}", "mana_requirements": requirements})
                retained = tuple(value for value in program.capability_dependencies if value != PUBLIC_HISTORY_QUERY_CAPABILITY)
                closure = self.registry.closure(retained, profile=PROFILE).to_dict()
                self.assertTrue(closure["trusted"], closure["blockers"])
                stripped = replace(program, capability_dependencies=retained, capability_closure=closure,
                                   provenance={**program.provenance, "capability_closure_fingerprint": closure["fingerprint"]})
                binding = bind_semantic_program_runtime(stripped, capability_registry=self.registry, profile=PROFILE)
                self.assertIn("capability:undeclared_runtime_dependency:" + PUBLIC_HISTORY_QUERY_CAPABILITY, binding["blockers"])

    def _action(self, game, trap):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        return next((row for row in actions if row["id"] == "cast:" + trap.ref), None)

    def _price(self, game, trap, expected):
        action = self._action(game, trap)
        self.assertIsNotNone(action, game.state.phase)
        choice, = [price for price in action["cost_options"] if price["kind"] == "alternate"]
        self.assertEqual(expected, {key: count for key, count in choice["requirements"].items() if count})
        return action, choice

    def _reject(self, game, seat, command):
        before = authoritative_state_hash(game.state)
        refused = game.act("pilot:" + seat, command)
        self.assertFalse(refused.ok, refused.summary)
        self.assertEqual(before, authoritative_state_hash(game.state))

    def _no_price(self, game, trap, targets):
        action = self._action(game, trap)
        self.assertNotIn("alternate", {price["kind"] for price in (action or {}).get("cost_options", ())})
        self._reject(game, "A", {"action_id": "cast:" + trap.ref, "cost_option": "plain-alternative-ab1",
                                 "targets": targets, "pay": "auto"})

    def _cast_and_resolve(self, game, seat, card, targets=(), *, payment=None):
        self.priority(game, seat)
        action = self.action(game, seat, card)
        command = {"action_id": action["id"], "targets": list(targets), "pay": "auto"}
        if payment is not None:
            command.update(pay="manual", payment=payment)
        cast = game.act("pilot:" + seat, command)
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))

    def test_actual_needlebite_checks_committed_gain_below_starting_life_and_targets_another_player(self):
        game = self.session(77123001)
        trap = self.add(game.engine, "Needlebite Trap", zone="hand")
        own_gain = self.add(game.engine, "Revitalize", zone="hand")
        own_shock = self.add(game.engine, "Shock", zone="hand", ref="needlebite-own-shock")
        other_shock = self.add(game.engine, "Shock", seat="B", zone="hand", ref="needlebite-other-shock")
        their_gain = self.add(game.engine, "Revitalize", seat="C", zone="hand", ref="needlebite-opponent-gain")
        hidden = self.add(game.engine, "Memnite", seat="C", zone="hand", ref="needlebite-private-hand")
        for card in (trap, own_gain, own_shock, other_shock, their_gain):
            self.assert_closed(game, card)
        self.prepare(game, mana={"C": 1, "W": 1, "R": 1, "B": 1})
        game.state.players["B"].mana_pool["R"] = 1
        game.state.players["C"].mana_pool.update({"C": 1, "W": 1})
        self.checkpoint(game)
        self._cast_and_resolve(game, "A", own_gain, payment={"C": 1, "W": 1})
        self._cast_and_resolve(game, "A", own_shock, ["C"])
        self._cast_and_resolve(game, "B", other_shock, ["C"])
        self.assertEqual((43, 36), (game.state.players["A"].life, game.state.players["C"].life))
        self._no_price(game, trap, ["D"])

        self._cast_and_resolve(game, "C", their_gain, payment={"C": 1, "W": 1})
        self.assertEqual(39, game.state.players["C"].life)
        self.assertIn(("C", 3), {(fact.target, fact.amount) for fact in game.state.turn_history.events
                                 if fact.kind == "player_gained_life"})
        action, price = self._price(game, trap, {"B": 1})
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        cast = game.act("pilot:A", {"action_id": action["id"], "cost_option": price["id"],
                                    "targets": ["D"], "pay": "manual", "payment": {"B": 1}})
        self.assertTrue(cast.ok, cast.summary)
        self.assertIsNone(self.resolve(game))
        self.assertEqual((48, 39, 35), tuple(game.state.players[seat].life for seat in "ACD"))
        self.assertNotIn(hidden.ref, json.dumps(game.packet("pilot:A", full=True)))
        self.replay(game, load=True)

    def _search(self, game, seat, land, choice=None):
        self.priority(game, seat)
        legal = game.packet("pilot:" + seat, full=True)["decision"]["ctx"]["legal"]["actions"]
        option, = [row for row in legal if row["id"].startswith("activate:" + land.ref + ":")]
        life = game.state.players[seat].life
        activated = game.act("pilot:" + seat, {"action_id": option["id"], "pay": "auto"})
        self.assertTrue(activated.ok, activated.summary)
        self.assertEqual(life - 1, game.state.players[seat].life)
        pending = self.resolve(game)
        self.assertEqual(("search.fetch", [seat]), (pending.kind, pending.actors))
        selected = {} if choice is None else {"search_card": choice.ref}
        if choice is not None:
            self.assertNotIn(choice.ref, json.dumps(game.packet("pilot:A", full=True)))
        result = game.act("pilot:" + seat, {"action_id": "choose", **selected})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", game.state.cards[land.object_id].zone)
        if choice is not None:
            moved = game.state.cards[choice.object_id]
            self.assertEqual(("battlefield", seat, False), (moved.zone, moved.controller, moved.tapped))

    def test_actual_lavaball_counts_one_opponents_play_and_effect_arrivals_and_revalidates_two_lands(self):
        game = self.session(77123002)
        trap = self.add(game.engine, "Lavaball Trap", zone="hand", ref="lavaball-invalidated")
        later = self.add(game.engine, "Lavaball Trap", zone="hand", ref="lavaball-resolving")
        plays = self.add(game.engine, "Mountain", seat="B", zone="hand", ref="lavaball-played-land")
        response = self.add(game.engine, "Shock", seat="D", zone="hand", ref="lavaball-pending-response")
        lands = {seat: self.add(game.engine, "Flooded Strand", seat=seat, ref="lavaball-fetch-" + seat) for seat in "ABCD"}
        finds = {seat: self.add(game.engine, "Plains", seat=seat, zone="library", ref="lavaball-picked-" + seat)
                 for seat in "BC"}
        targets = [self.add(game.engine, name, seat=seat, ref="lavaball-independent-land-" + seat)
                   for name, seat in (("Plains", "A"), ("Mountain", "D"))]
        creatures = [self.add(game.engine, "Memnite", seat=seat, ref="lavaball-damage-" + seat) for seat in "ACD"]
        for card in (trap, later, plays, response, *lands.values(), *finds.values(), *targets, *creatures):
            self.assert_closed(game, card)
        self.prepare(game, active="B", actor="B")
        game.state.players["A"].mana_pool.update({"C": 6, "R": 4})
        game.state.players["D"].mana_pool["R"] = 1
        self.checkpoint(game)
        self._search(game, "C", lands["C"], finds["C"])
        self.priority(game, "B")
        played = game.act("pilot:B", {"action_id": "play-land:" + plays.ref})
        self.assertTrue(played.ok, played.summary)
        self.assertEqual("battlefield", game.state.cards[plays.object_id].zone)
        self.cast(game, "D", response, ["B"])
        target_refs = [land.ref for land in targets]
        self._no_price(game, trap, target_refs)
        printed = self._action(game, trap)
        self.assertIn("mana", {price["kind"] for price in printed["cost_options"]})

        self._search(game, "B", lands["B"], finds["B"])
        facts = [fact.actor for fact in game.state.turn_history.events
                 if fact.kind == "permanent_entered" and "land" in fact.types]
        self.assertEqual(["C", "B", "B"], facts)
        self.assertEqual((6, 4), tuple(game.state.players["A"].mana_pool[c] for c in "CR"))
        action, option = self._price(game, trap, {"GENERIC": 3, "R": 2})
        cast = {"action_id": action["id"], "cost_option": option["id"], "pay": "manual", "payment": {"C": 3, "R": 2}}
        self._reject(game, "A", {**cast, "targets": [lands["A"].ref]})
        first = game.act("pilot:A", {**cast, "targets": [lands["A"].ref, lands["D"].ref]})
        self.assertTrue(first.ok, first.summary)
        self._search(game, "A", lands["A"])
        self._search(game, "D", lands["D"])
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in creatures))

        action, option = self._price(game, later, {"GENERIC": 3, "R": 2})
        final = game.act("pilot:A", {**cast, "action_id": action["id"], "cost_option": option["id"], "targets": target_refs})
        self.assertTrue(final.ok, final.summary)
        self.assertIsNone(self.resolve(game))
        self.assertTrue(all(game.state.cards[card.object_id].zone == "graveyard" for card in (*targets, *creatures)))
        self.assertEqual(0, sum(game.state.players["A"].mana_pool.values()))
        self.replay(game, load=True)

    def test_omission_restores_both_life_and_land_price_residuals(self):
        with patch("quorune.compiler.fixed_public_alternative_costs._opponent_history_price", return_value=None):
            for name in PRICES:
                outcome, _ = register_card(self.db, self.registry, name)
                self.assertFalse(outcome["strict_capability_ready"], outcome)
            retained, _ = register_card(self.db, self.registry, "Revitalize")
            self.assertTrue(retained["strict_capability_ready"], retained["blockers"])

    def test_genuine_withdrawals_block_both_life_and_land_price_originals(self):
        from quorune.rules.capabilities import CapabilityRegistry
        from quorune.rules.evidence import validate_capability_evidence_index
        from quorune.rules.evidence_storage import read_evidence_document
        from scripts.update_capability_evidence import build_index, discover_tests

        original = json.loads((ROOT / "quorune/rules/capability-registry.json").read_bytes())
        declarations = read_evidence_document(ROOT / "platform/capability-evidence-declarations.json")
        known = set(discover_tests(ROOT).values())
        for capability in (FIXED_PUBLIC_ALTERNATIVE_COST_CAPABILITY, PUBLIC_HISTORY_QUERY_CAPABILITY):
            with self.subTest(capability=capability):
                document = deepcopy(original)
                next(row for row in document["capabilities"] if row["id"] == capability).update(
                    status="blocked", blockers=["Life and land price authority withdrawn"])
                registry = CapabilityRegistry(document)
                index = build_index(registry_value=document, declaration_source=declarations, discovered_test_ids=known)
                registry.mark_evidence_verified(validate_capability_evidence_index(index, registry=registry))
                closure = registry.closure((capability,), profile=PROFILE)
                self.assertIn("status:" + capability + ":blocked", closure.blockers)
                self.assertFalse(any("unverified" in blocker for blocker in closure.blockers))
                for name in PRICES:
                    outcome, _ = register_card(self.db, registry, name)
                    self.assertFalse(outcome["strict_capability_ready"], outcome)
                retained, _ = register_card(self.db, registry, "Revitalize")
                self.assertTrue(retained["strict_capability_ready"], retained["blockers"])
