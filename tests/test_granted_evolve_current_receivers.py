"""Two complete originals grant ordinary Evolve to their current recipients."""

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
from quorune.evolve import EVOLVE_EVENT_CONDITION_FIELD
from quorune.oracle_ir import compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database
from test_controller_creature_keyword_absence import register_card


PRIME = "Tyranid Prime"
DRONE = "Propagator Drone"
EVOLVE = "counter.producer.evolve"
GRANT = "continuous.ability.fixed_keyword_event_grant"
PROFILE = "commander_review"
FIXTURES = ROOT / "tests/fixtures"


class GrantedEvolveCurrentReceiversTests(InheritedNativeFixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        folder = Path(cls.temporary.name)
        base = json.loads((FIXTURES / "bound-effect-program-cards.json").read_bytes())
        base["cards"] = [row for row in base["cards"] if row["name"] == "Generic Bound Plains"]
        base["rulings"] = []
        basic = folder / "base.json"
        basic.write_text(json.dumps(base), encoding="utf-8")
        path = folder / "granted-evolve.sqlite3"
        build_fixture_database([basic, FIXTURES / "granted-evolve-current-receivers-cards.json"], path)
        cls.db = CardDatabase(path)
        cls.registry = load_default_capability_registry()
        cls.deck = DeckDefinition("Kindred Evolve grants", [
            DeckEntry("Isamaru, Hound of Konda", 1, "commander"), DeckEntry("Generic Bound Plains", 30),
        ], ["Isamaru, Hound of Konda"])

    def programs(self, name):
        record = self.db.lookup(name, fuzzy=False)
        binding, semantics = register_card(self.db, self.registry, name)
        self.assertTrue(binding["strict_capability_ready"], binding["blockers"])
        programs = semantics.programs_for_oracle(record.oracle_id)
        self.assertTrue(any(item.event == "characteristics.evaluate" and GRANT in item.capability_dependencies
                            and EVOLVE in item.capability_dependencies for item in programs))
        granted, = [part for part in programs if part.event == "creature.enter" and part.provenance.get("granted_only")]
        return record, programs, granted

    def test_both_originals_keep_their_separate_material_non_granted_programs(self):
        for name in (PRIME, DRONE):
            with self.subTest(name=name):
                record, programs, granted = self.programs(name)
                self.assertEqual({"field": EVOLVE_EVENT_CONDITION_FIELD, "op": "truthy"}, granted.event_condition)
                self.assertTrue({EVOLVE, "counter.producer.fixed_effect"} <= set(granted.capability_dependencies))
                if name == PRIME:
                    printed, = [item for item in programs if item.event == "creature.enter"
                                and not item.provenance.get("granted_only")]
                    self.assertNotEqual(printed.key, granted.key)
                else:
                    self.assertTrue(any("continuous.characteristics.devoid" in p.capability_dependencies for p in programs))
                    self.assertTrue(any("token.creation.typed_ability_definition" in p.capability_dependencies for p in programs))
                    self.assertTrue(any(p.provenance.get("granted_only") and p.event == "activate" for p in programs))
                text = record.oracle_text.replace("you control have evolve", "your opponents control have evolve")
                ir = compile_oracle_card(replace(record, oracle_text=text), capability_registry=self.registry,
                                         capability_profile=PROFILE)
                self.assertFalse(any(node.kind == "granted_triggered_ability" and node.event == "creature.enter"
                                     for face in ir.faces for node in face.nodes))

    def test_raw_granted_child_keeps_exact_intervening_event_count_and_distinct_authority(self):
        _, _, granted = self.programs(PRIME)
        required = [cap for cap in granted.capability_dependencies if cap != EVOLVE]
        missing = replace(granted, capability_dependencies=required,
                          capability_closure=self.registry.closure(required, profile=PROFILE).to_dict())
        bound = bind_semantic_program_runtime(missing, capability_registry=self.registry, profile=PROFILE)
        self.assertFalse(bound["strict"], bound)
        self.assertIn("capability:undeclared_runtime_dependency:" + EVOLVE, bound["blockers"])
        effect, = granted.effects
        for changed in (replace(granted, effects=({**effect, "amount": 2},)),
                        replace(granted, event_condition={"field": EVOLVE_EVENT_CONDITION_FIELD, "op": "falsey"})):
            binding = bind_semantic_program_runtime(changed, capability_registry=self.registry, profile=PROFILE)
            self.assertFalse(binding["strict"], binding)

    def choose_evolves(self, game, expected_sources):
        pending = game.state.pending_decision
        self.assertEqual(("trigger.order", ["A"]), (pending.kind, pending.actors))
        packet = game.packet("pilot:A", full=True)
        order = [item["id"] for item in packet["decision"]["ctx"]["triggers"]]
        self.assertEqual(len(expected_sources), len(order))
        accepted = game.act("pilot:A", {"action_id": "order", "order": order})
        self.assertTrue(accepted.ok, accepted.summary)
        self.assertEqual({card.object_id for card in expected_sources},
                         {item.source_object_id for item in game.state.stack})

    def prime_board(self, seed, *, departing):
        game = self.session(seed)
        engine = game.engine
        prime = self.add(engine, PRIME, zone="hand")
        recipient = self.add(engine, "Grizzly Bears", ref="granted-evolve-own-recipient")
        foreign = self.add(engine, "Grizzly Bears", seat="B", ref="granted-evolve-other-recipient")
        wurm = self.add(engine, "Craw Wurm", zone="hand")
        bounce = self.add(engine, "Boomerang", zone="hand", ref="granted-evolve-prime-removal") if departing else None
        response = self.add(engine, "Boomerang" if departing else "Giant Growth", seat="B", zone="hand",
                            ref="granted-evolve-entered-response")
        secret = self.add(engine, "Counterspell", seat="D", zone="hand", ref="granted-evolve-private")
        self.assert_closed(game, prime)
        self.prepare(game, mana={"C": 5, "G": 3, "U": 3 if departing else 1})
        game.state.players["B"].mana_pool.update({"U": 2} if departing else {"G": 1})
        self.checkpoint(game)

        reserve = {"C": 4, "G": 2, **({"U": 2} if departing else {})}
        self.cast_for(game, "A", prime, reserve=reserve)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[recipient.object_id].counters.get("+1/+1", 0),
                         "The existing creature receives Evolve as Prime enters")
        self.assertFalse(game.state.cards[foreign.object_id].counters)

        self.cast_for(game, "A", wurm, reserve={"U": 2} if departing else None)
        self.resolve_top(game)
        self.choose_evolves(game, (prime, recipient))
        return game, prime, recipient, foreign, wurm, bounce, response, secret

    def test_actual_prime_grant_survives_granter_and_entrant_departure_on_the_stack(self):
        game, prime, recipient, foreign, wurm, bounce, response, secret = self.prime_board(702100701, departing=True)
        self.cast_for(game, "B", response, [wurm.ref])
        self.cast_for(game, "A", bounce, [prime.ref])
        game = self.replay(game, load=True)
        self.assertIsNone(self.resolve(game))
        self.assertEqual("hand", game.state.cards[prime.object_id].zone)
        self.assertEqual("hand", game.state.cards[wurm.object_id].zone)
        self.assertEqual(2, game.state.cards[recipient.object_id].counters["+1/+1"])
        self.assertFalse(game.state.cards[foreign.object_id].counters)
        for principal in ("pilot:A", "pilot:B", "pilot:C", "spectator"):
            self.assertNotIn(secret.ref, json.dumps(game.packet(principal, full=True)))
        self.replay(game, load=True)

    def test_actual_granted_evolve_rechecks_current_receiver_stats_independently(self):
        game, prime, recipient, foreign, _wurm, _bounce, response, _secret = self.prime_board(702100702, departing=False)
        self.cast_for(game, "B", response, [recipient.ref])
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[prime.object_id].counters["+1/+1"])
        self.assertEqual(1, game.state.cards[recipient.object_id].counters["+1/+1"])
        self.assertFalse(game.state.cards[foreign.object_id].counters)
        self.replay(game, load=True)

    def activate(self, game, source, *, reserve=None, mana_output=None):
        self.priority(game, "A")
        actions = game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        offered, = [row for row in actions if row["id"].startswith("activate:" + source.ref + ":")]
        command = {"action_id": offered["id"], "pay": "auto"}
        if reserve is not None:
            command["reserve"] = reserve
        if mana_output is not None:
            command["mana_output"] = mana_output
        accepted = game.act("pilot:A", command)
        self.assertTrue(accepted.ok, accepted.summary)

    def test_actual_drone_creates_evolving_spawn_and_preserves_its_intrinsic_mana(self):
        game = self.session(702100703)
        engine = game.engine
        drone = self.add(engine, DRONE, zone="hand")
        elf = self.add(engine, "Llanowar Elves")
        entrant = self.add(engine, "Grizzly Bears", zone="hand")
        self.assert_closed(game, drone)
        self.prepare(game, mana={"C": 5, "G": 3})
        self.checkpoint(game)
        self.cast_for(game, "A", drone, reserve={"C": 4, "G": 2})
        self.assertIsNone(self.resolve(game))
        self.assertFalse(game.engine._effective_card_data(game.state.cards[drone.object_id])["colors"])
        self.activate(game, drone, reserve={"C": 1, "G": 1})
        self.assertIsNone(self.resolve(game))
        spawn, = [card for card in game.state.cards.values() if card.zone == "battlefield" and card.is_token]
        self.assertEqual("Eldrazi Spawn Token", game.engine.display_name(spawn.object_id))
        self.assertFalse(spawn.counters)
        self.cast_for(game, "A", entrant)
        self.assertIsNone(self.resolve(game))
        self.assertEqual(1, game.state.cards[spawn.object_id].counters["+1/+1"])
        self.assertFalse(game.state.cards[elf.object_id].counters)
        self.activate(game, spawn, mana_output={"C": 1})
        self.assertEqual(1, game.state.players["A"].mana_pool["C"])
        self.assertEqual("outside", game.state.cards[spawn.object_id].zone)
        self.replay(game, load=True)

    def test_actual_granted_evolve_mutation_loses_the_first_recipient_counter(self):
        from quorune.semantic_runtime.continuous_components import FixedQueryAbilityGrantHandler

        with patch.object(FixedQueryAbilityGrantHandler, 'lower', return_value=()):
            with self.assertRaisesRegex(AssertionError, "receives Evolve as Prime enters"):
                self.prime_board(702100701, departing=True)
