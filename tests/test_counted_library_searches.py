from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from common import ROOT, keep_all, make_session, pass_current
from scripts.build_test_database import build_fixture_database, compact_ci_fixture_paths
from quorune.carddb import CardDatabase
from quorune.card_programs import bind_card_program_runtime
from quorune.card_programs.adapters import compile_best_available_card_program
from quorune.deck import DeckLoader
from quorune.library_search_model import FixedCountedLibrarySearchTemplate
from quorune.model import CardInstance
from quorune.oracle_ir import compile_oracle_card, register_generated_programs
from quorune.record import authoritative_state_hash, checkpoint_envelope, replay_record
from quorune.rules.capabilities import CapabilityRegistry, load_default_capability_registry
from quorune.rules.library_search_capability_shapes import fixed_counted_library_search_node_capabilities
from quorune.semantics import SemanticProgram, SemanticRegistry
from quorune.session import CommanderSession
from quorune.zone_transitions import ZoneTransitionOwner


CAPABILITY = "library.search.fixed_counted"


class CountedLibrarySearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "counted-searches.sqlite3"
        build_fixture_database(compact_ci_fixture_paths(root=ROOT), path)
        cls.db = CardDatabase(path)
        loader = DeckLoader(cls.db)
        cls.mishra = loader.load(ROOT / "examples/mishra-eminent-one.txt", commander="Mishra, Eminent One")
        cls.zimone = loader.load(ROOT / "examples/zimone-and-dina.txt", commander="Zimone and Dina")

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def card(self, session, name, zone="hand", owner="A"):
        engine = session.engine
        record = self.db.lookup(name)
        ref = engine._next_ref("P")
        card = CardInstance(
            object_id=f"counted-search:{ref}", ref=ref, oracle_id=record.oracle_id,
            printed_name=record.name, owner=owner, controller=owner, zone=zone,
            known_to=list(engine.seats) if zone == "battlefield" else [owner],
            revealed_to=list(engine.seats) if zone == "battlefield" else [],
        )
        engine.state.cards[card.object_id] = card
        engine.state.players[owner].zones[zone].append(card.object_id)
        register_generated_programs(
            self.db, engine.semantics, (record,),
            capability_registry=load_default_capability_registry(),
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        return card

    def setup_cast(self, name, seed):
        session = make_session(self.db, self.mishra, self.zimone, players=4, seed=seed, auto_pass_empty=False)
        keep_all(session)
        spell = self.card(session, name)
        engine = session.engine
        engine.permissions.invalidate_current()
        engine.state.pending_decision = None
        engine.state.started = True
        engine.state.active_player = "A"
        engine.state.phase = "precombat_main"
        engine.state.step = "main"
        engine.state.players["A"].mana_pool.update(C=12, W=12, U=12, B=12, R=12, G=12)
        return session, spell

    def offered_cast(self, session, spell):
        engine = session.engine
        engine._grant_priority("A")
        engine.pump()
        actions = session.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
        action_id = f"cast:{spell.ref}"
        self.assertTrue(any(action["id"] == action_id for action in actions), actions)
        session.initial_checkpoint = checkpoint_envelope(engine.state)
        session.commands.clear()
        session.decisions.clear()
        result = session.act("pilot:A", {"action_id": action_id, "pay": "auto"})
        self.assertTrue(result.ok, result.summary)

    def reach_search(self, session):
        for _ in range(24):
            decision = session.state.pending_decision
            if decision is not None and decision.kind == "semantic.search":
                return session.packet("pilot:A", full=True)["decision"]
            if decision is not None and decision.kind == "semantic.choice":
                result = session.act("pilot:A", {"action_id": "choose", "choice": "apply"})
                self.assertTrue(result.ok, result.summary)
            else:
                pass_current(session)
        self.fail("Printed search did not become available")

    def assert_replay(self, session):
        expected = authoritative_state_hash(session.state)
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            result = replay_record(directory, self.db, verify=True)
        self.assertTrue(result["ok"], result)
        self.assertEqual(expected, result["final_state_hash"])

    def test_buried_alive_moves_a_private_ordered_batch_and_replays(self):
        # CR 404.3 and 701.23b: choose the ordering of simultaneous arrivals
        # and find any number from zero through the printed maximum.
        record = self.db.lookup("Buried Alive")
        registry = load_default_capability_registry()
        program = compile_best_available_card_program(
            self.db, record, semantic_registry=SemanticRegistry(),
            capability_registry=registry, capability_profile="commander_review",
        )
        binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
        self.assertTrue(binding["strict_capability_ready"], binding)

        session, spell = self.setup_cast("Buried Alive", 404301)
        selected = [self.card(session, name, "library") for name in (
            "Duplicant", "Myr Battlesphere", "Stonecoil Serpent",
        )]
        spare = self.card(session, "Duplicant", "library")
        invalid = self.card(session, "Sol Ring", "library")
        existing = self.card(session, "Myr Battlesphere", "graveyard")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual((0, 3, "graveyard_top_to_bottom"), (
            schema["minimum"], schema["maximum"], schema["order"],
        ))
        self.assertNotIn(invalid.ref, schema["legal_refs"])
        refs = [card.ref for card in selected]
        self.assertTrue(set(refs + [spare.ref]).issubset(schema["legal_refs"]))
        before = authoritative_state_hash(session.state)
        for seat in ("B", "C", "D"):
            other = json.dumps(session.packet(f"pilot:{seat}", full=True))
            self.assertTrue(all(ref not in other for ref in [*refs, spare.ref]))
        for pilot, rejected in (
            ("B", refs), ("A", [refs[0], refs[0]]),
            ("A", [*refs, spare.ref]), ("A", [invalid.ref]),
        ):
            self.assertFalse(session.act(f"pilot:{pilot}", {
                "action_id": "choose", "search_cards": rejected,
            }).ok)
            self.assertEqual(before, authoritative_state_hash(session.state))

        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        original_shuffle = ZoneTransitionOwner.shuffle_library

        def fail_after_shuffle(owner, seat, **kwargs):
            original_shuffle(owner, seat, **kwargs)
            raise RuntimeError("injected failure after shuffle")

        choice = {"action_id": "choose", "search_cards": refs}
        with patch.object(ZoneTransitionOwner, "shuffle_library", fail_after_shuffle):
            with self.assertRaisesRegex(RuntimeError, "injected failure after shuffle"):
                session.act("pilot:A", choice)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", choice)
        self.assertTrue(result.ok, result.summary)
        player = session.state.players["A"]
        expected = [existing.object_id, *(card.object_id for card in reversed(selected)), spell.object_id]
        self.assertEqual(expected, player.zones["graveyard"][-len(expected):])
        timestamps = {session.state.cards[card.object_id].zone_timestamp for card in selected}
        self.assertEqual(1, len(timestamps))
        self.assertGreater(min(timestamps), existing.zone_timestamp)
        self.assertEqual(shuffles + 1, player.stats["shuffle_count"])
        self.assertIn(spare.object_id, player.zones["library"])
        other = json.dumps(session.packet("pilot:B", full=True))
        self.assertNotIn(spare.ref, other)
        self.assert_replay(session)

    def test_buried_alive_accepts_either_top_card_and_replays(self):
        for names in (("Duplicant", "Myr Battlesphere"), ("Myr Battlesphere", "Duplicant")):
            with self.subTest(names=names):
                session, spell = self.setup_cast("Buried Alive", 404303)
                candidates = {
                    name: self.card(session, name, "library")
                    for name in ("Duplicant", "Myr Battlesphere")
                }
                top, bottom = (candidates[name] for name in names)
                self.offered_cast(session, spell)
                self.reach_search(session)
                result = session.act("pilot:A", {
                    "action_id": "choose", "search_cards": [top.ref, bottom.ref],
                })
                self.assertTrue(result.ok, result.summary)
                self.assertEqual(
                    [bottom.object_id, top.object_id, spell.object_id],
                    session.state.players["A"].zones["graveyard"][-3:],
                )
                self.assert_replay(session)

    def test_buried_alive_respects_the_shared_destination_replacement_and_replays(self):
        session, spell = self.setup_cast("Buried Alive", 404304)
        self.card(session, "Rest in Peace", "battlefield", owner="B")
        selected = [self.card(session, name, "library") for name in ("Duplicant", "Myr Battlesphere")]
        self.offered_cast(session, spell)
        self.reach_search(session)
        shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        result = session.act("pilot:A", {
            "action_id": "choose", "search_cards": [card.ref for card in selected],
        })
        self.assertTrue(result.ok, result.summary)
        player = session.state.players["A"]
        self.assertEqual([], player.zones["graveyard"])
        self.assertTrue({spell.object_id, *(card.object_id for card in selected)}.issubset(player.zones["exile"]))
        self.assertEqual(shuffles + 1, player.stats["shuffle_count"])
        self.assert_replay(session)

    def test_buried_alive_zero_still_shuffles_and_replays(self):
        session, spell = self.setup_cast("Buried Alive", 404302)
        creature = self.card(session, "Duplicant", "library")
        self.offered_cast(session, spell)
        self.reach_search(session)
        shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        player = session.state.players["A"]
        self.assertEqual(shuffles + 1, player.stats["shuffle_count"])
        self.assertIn(creature.object_id, player.zones["library"])
        self.assertEqual(spell.object_id, player.zones["graveyard"][-1])
        self.assert_replay(session)

    def test_iname_searches_kindred_and_creature_spirits_and_commits_either_graveyard_order(self):
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                session, spell = self.setup_cast("Iname, Death Aspect", 46072001 + reverse)
                spirits = [self.card(session, name, "library") for name in
                           ("Hana Kami", "Changeling Outcast", "Crib Swap", "Hana Kami")]
                wrong = self.card(session, "Spirit Link", "library")
                incumbent = self.card(session, "Hana Kami", "graveyard")
                for card in (*spirits, wrong):
                    card.known_to.clear()
                self.offered_cast(session, spell)
                decision = self.reach_search(session)
                schema = decision["legal_actions"][0]["choice_schema"]
                self.assertEqual("graveyard_top_to_bottom", schema["order"])
                self.assertGreaterEqual(schema["maximum"], 4)
                self.assertTrue(all(card.ref in schema["legal_refs"] for card in spirits))
                self.assertNotIn(wrong.ref, schema["legal_refs"])
                for seat in "BCD":
                    packet = json.dumps(session.packet("pilot:" + seat, full=True))
                    self.assertTrue(all(card.ref not in packet for card in spirits))
                ordered = spirits[::-1] if reverse else spirits
                payload = {"action_id": "choose", "search_cards": [card.ref for card in ordered]}
                before = authoritative_state_hash(session.state)
                rejected = session.act("pilot:B", payload)
                self.assertFalse(rejected.ok)
                rejected = session.act("pilot:A", {**payload, "search_cards": [wrong.ref]})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(session.state))
                self.assert_replay(session)
                accepted = session.act("pilot:A", payload)
                self.assertTrue(accepted.ok, accepted.summary)
                graveyard = session.state.players["A"].zones["graveyard"]
                self.assertEqual([incumbent.object_id, *(card.object_id for card in reversed(ordered))],
                                 graveyard[-5:])
                self.assertEqual(1, len({session.state.cards[card.object_id].zone_timestamp for card in ordered}))
                self.assertEqual("library", session.state.cards[wrong.object_id].zone)
                self.assert_replay(session)

    def test_iname_decline_avoids_search_and_zero_find_still_shuffles(self):
        for apply in (False, True):
            with self.subTest(apply=apply):
                session, spell = self.setup_cast("Iname, Death Aspect", 46072003 + apply)
                self.card(session, "Hana Kami", "library")
                self.offered_cast(session, spell)
                for _ in range(24):
                    if session.state.pending_decision.kind == "semantic.choice":
                        break
                    pass_current(session)
                self.assertEqual("semantic.choice", session.state.pending_decision.kind)
                self.assertEqual(["pilot:A"], session.pending_principals())
                initial_library = tuple(session.state.players["A"].zones["library"])
                before = sum(event.code == "library.shuffle" for event in session.state.events)
                accepted = session.act("pilot:A", {"action_id": "choose", "choice": "apply" if apply else "decline"})
                self.assertTrue(accepted.ok, accepted.summary)
                if apply:
                    self.assertEqual("semantic.search", session.state.pending_decision.kind)
                    accepted = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
                    self.assertTrue(accepted.ok, accepted.summary)
                else:
                    self.assertNotEqual("semantic.search", session.state.pending_decision.kind)
                    self.assertEqual(initial_library, tuple(session.state.players["A"].zones["library"]))
                after = sum(event.code == "library.shuffle" for event in session.state.events)
                self.assertEqual(int(apply), after - before)
                self.assert_replay(session)

    def test_iname_optional_owned_subtype_search_schema_fails_closed(self):
        from dataclasses import replace
        from quorune.library_search_model import SubtypeGraveyardSearchSpec

        registry = load_default_capability_registry()
        record = self.db.lookup("Iname, Death Aspect", fuzzy=False)
        ir = compile_oracle_card(record, capability_registry=registry)
        self.assertEqual("exact", ir.status, ir.material_residuals)
        node, = ir.faces[0].nodes
        wrapper, = node.effects
        self.assertEqual("offer_optional_effect", wrapper["op"])
        effect, = wrapper["effects"]
        self.assertEqual(SubtypeGraveyardSearchSpec("spirit"), SubtypeGraveyardSearchSpec.from_effect(effect))
        for mutation in (
            {**effect, "selector": {"types_any": ["creature"], "subtypes_any": ["spirit"]}},
            {**effect, "shuffle_after": False},
            {**effect, "destination": "hand"},
        ):
            with self.assertRaises(ValueError):
                SubtypeGraveyardSearchSpec.from_effect(mutation)
        self.assertNotEqual("exact", compile_oracle_card(
            replace(record, oracle_text=record.oracle_text.replace("you may search", "search")),
            capability_registry=registry,
        ).status)

    def test_dewdrop_gift_locks_target_limit_and_draws_before_the_batch(self):
        for promised in (False, True):
            with self.subTest(promised=promised):
                game, source = self.setup_cast("Dewdrop Cure", 11502061 + promised)
                targets = [self.card(game, "Grizzly Bears", "graveyard") for _ in range(3)]
                expensive = self.card(game, "Solemn Simulacrum", "graveyard")
                foreign = self.card(game, "Grizzly Bears", "graveyard", owner="B")
                game.engine._grant_priority("A")
                game.engine.pump()
                action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                              if row.get("card") == source.ref)
                option = next(row for row in action["cost_options"]
                              if row["id"].endswith("+gift:B") == promised and
                              (promised or "+gift:" not in row["id"]))
                game.initial_checkpoint = checkpoint_envelope(game.state)
                game.commands.clear()
                game.decisions.clear()
                command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
                invalid = [[expensive.ref], [foreign.ref]]
                if not promised:
                    invalid.append([card.ref for card in targets])
                for refs in invalid:
                    before = authoritative_state_hash(game.state)
                    rejected = game.act("pilot:A", {**command, "targets": refs})
                    self.assertFalse(rejected.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                chosen = targets if promised else targets[:2]
                accepted = game.act("pilot:A", {**command, "targets": [card.ref for card in chosen]})
                self.assertTrue(accepted.ok, accepted.summary)
                self.assert_replay(game)
                hand = len(game.state.players["B"].zones["hand"])
                arrivals = {card.object_id for card in chosen}
                hand_at_return = []
                original = ZoneTransitionOwner.move_cards_simultaneously

                def observe(owner, changes, **kwargs):
                    if {object_id for object_id, zone in changes if zone == "battlefield"} == arrivals:
                        hand_at_return.append(len(game.state.players["B"].zones["hand"]))
                    return original(owner, changes, **kwargs)

                with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", observe):
                    for _ in range(12):
                        if not game.state.stack:
                            break
                        pass_current(game)
                self.assertFalse(game.state.stack)
                self.assertEqual([hand + int(promised)], hand_at_return)
                self.assertEqual(hand + int(promised), len(game.state.players["B"].zones["hand"]))
                self.assertTrue(all(game.state.cards[card.object_id].zone == "battlefield" for card in chosen))
                self.assertEqual(1, len({game.state.cards[card.object_id].zone_timestamp for card in chosen}))
                self.assert_replay(game)

    def test_dewdrop_target_branches_and_forged_domains_fail_closed(self):
        from quorune.rules import gift_graveyard_spells as rules
        from quorune.card_programs.binding import bind_semantic_program_runtime
        from dataclasses import replace

        game, source = self.setup_cast("Dewdrop Cure", 11502064)
        program, = game.engine.semantics.programs_for_oracle(source.oracle_id)
        self.assertEqual(2, rules.target_schema(False)["max"])
        self.assertEqual(3, rules.target_schema(True)["max"])
        mutated = replace(program, target_schema={**program.target_schema, "mana_value_max": 3})
        binding = bind_semantic_program_runtime(mutated, capability_registry=load_default_capability_registry(),
                                                profile="commander_review")
        self.assertFalse(binding["strict"])

    def test_coiling_copies_only_its_actual_nonlegendary_return(self):
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        for outcome in ("ordinary", "promised", "legendary", "redirected"):
            with self.subTest(outcome=outcome):
                promised = outcome != "ordinary"
                game, source = self.setup_cast("Coiling Rebirth", 7070201 + len(outcome))
                model = self.card(game, "Isamaru, Hound of Konda" if outcome == "legendary" else "Grizzly Bears", "graveyard")
                engine = game.engine
                engine._grant_priority("A")
                engine.pump()
                action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                              if row.get("card") == source.ref)
                option = next(row for row in action["cost_options"]
                              if row["id"].endswith("+gift:B") == promised and
                              (promised or "+gift:" not in row["id"]))
                game.initial_checkpoint = checkpoint_envelope(game.state)
                game.commands.clear()
                game.decisions.clear()
                command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto", "targets": [model.ref]}
                accepted = game.act("pilot:A", command)
                self.assertTrue(accepted.ok, accepted.summary)
                hand = len(game.state.players["B"].zones["hand"])
                at_return = []
                original = engine.move_card

                def observe(object_id, destination, **kwargs):
                    if object_id == model.object_id and destination == "battlefield":
                        at_return.append(len(game.state.players["B"].zones["hand"]))
                    return original(object_id, destination, **kwargs)

                collector = zone_replacements.collect_zone_change_replacement_effects
                redirect = ReplacementEffect(
                    effect_id="fixture:coiling-redirect", source_id="fixture:coiling-redirect",
                    event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
                    conditions={"origin": {"eq": "graveyard"}, "destination": {"eq": "battlefield"}},
                    operations=(SetField("destination", "exile"),),
                )

                def redirected(host, **kwargs):
                    return (*collector(host, **kwargs), *((redirect,) if outcome == "redirected" else ()))

                with (patch.object(engine, "move_card", observe),
                      patch.object(zone_replacements, "collect_zone_change_replacement_effects", redirected)):
                    for _ in range(12):
                        if not game.state.stack:
                            break
                        pass_current(game)
                self.assertFalse(game.state.stack)
                self.assertEqual([hand + int(promised)], at_return)
                self.assertEqual("exile" if outcome == "redirected" else "battlefield",
                                 game.state.cards[model.object_id].zone)
                tokens = [card for card in game.state.cards.values() if card.is_token and card.zone == "battlefield"]
                self.assertEqual(int(outcome == "promised"), len(tokens))
                if tokens:
                    self.assertEqual((1, 1), tuple(engine._numeric_stat(tokens[0].object_id, stat)
                                                   for stat in ("power", "toughness")))
                    self.assertEqual("A", tokens[0].controller)
                    self.assertEqual(engine._effective_card_data(model)["name"],
                                     engine._effective_card_data(tokens[0])["name"])
                with patch.object(zone_replacements, "collect_zone_change_replacement_effects", redirected):
                    self.assert_replay(game)

    def test_coiling_rejects_forged_return_and_capability_withdrawal(self):
        from dataclasses import replace
        from quorune.rules.gift_graveyard_spells import RETURN_COPY_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime

        game, card = self.setup_cast("Coiling Rebirth", 7070232)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        registry = load_default_capability_registry()
        deps = tuple(dep for dep in program.capability_dependencies if dep != RETURN_COPY_CAPABILITY)
        for changed in (
            replace(program, capability_dependencies=deps,
                    capability_closure=registry.closure(deps, profile="commander_review").to_dict()),
            replace(program, effects=({**program.effects[0], "tapped": True},)),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=registry, profile="commander_review",
            )["strict"])

    def _start_starfall(self, game, spell, *, promised):
        engine = game.engine
        engine._grant_priority("A")
        engine.pump()
        action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                      if row.get("card") == spell.ref)
        option = next(row for row in action["cost_options"]
                      if row["id"].endswith("+gift:B") == promised and (promised or "+gift:" not in row["id"]))
        game.initial_checkpoint = checkpoint_envelope(game.state)
        game.commands.clear()
        game.decisions.clear()
        done = game.act("pilot:A", {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"})
        self.assertTrue(done.ok, done.summary)

    def test_starfall_returns_one_actual_owned_creature_after_ordering_and_gift(self):
        from quorune.replacement.model import ReplacementClass, ReplacementEffect
        from quorune.replacement.operations import SetField
        from quorune.semantic_runtime import zone_replacements

        game, spell = self.setup_cast("Starfall Invocation", 70245001)
        first, second, redirected = [self.card(game, "Grizzly Bears", "battlefield") for _ in range(3)]
        old = self.card(game, "Grizzly Bears", "graveyard")
        foreign = self.card(game, "Grizzly Bears", "battlefield", owner="B")
        survives = self.card(game, "Darksteel Myr", "battlefield")
        before_tokens = set(game.state.cards)
        game.engine.create_token("A", name="Fixture Creature", characteristics={
            "type_line": "Token Creature — Soldier", "power": "1", "toughness": "1",
        }, reason="Prepared Starfall token")
        token, = [row for key, row in game.state.cards.items() if key not in before_tokens and row.is_token]
        self._start_starfall(game, spell, promised=True)
        hand = len(game.state.players["B"].zones["hand"])
        collector = zone_replacements.collect_zone_change_replacement_effects
        effect = ReplacementEffect(
            effect_id="fixture:starfall-redirect", source_id="fixture:starfall-redirect",
            event_kind="zone.change", replacement_class=ReplacementClass.OTHER,
            conditions={"origin": {"eq": "battlefield"}, "destination": {"eq": "graveyard"},
                        "logical_object_id": {"eq": redirected.logical_object_id}},
            operations=(SetField("destination", "exile"),),
        )

        def collect(host, **kwargs):
            return (*collector(host, **kwargs), effect)

        with patch.object(zone_replacements, "collect_zone_change_replacement_effects", collect):
            for _ in range(16):
                if game.state.pending_decision.kind == "semantic.choice":
                    break
                pass_current(game)
            self.assertEqual("semantic.choice", game.state.pending_decision.kind)
            self.assertEqual(["pilot:A"], game.pending_principals())
            self.assertEqual(hand + 1, len(game.state.players["B"].zones["hand"]))
            self.assertTrue(all(game.state.cards[c.object_id].zone == "battlefield" for c in (first, second, redirected)))
            self.assert_replay(game)
            ordered = game.act("pilot:A", {"action_id": "choose", "cards": [second.ref, first.ref]})
            self.assertTrue(ordered.ok, ordered.summary)
            self.assertEqual("semantic.choice", game.state.pending_decision.kind)
            arrivals = [oid for oid in game.state.players["A"].zones["graveyard"] if game.state.cards[oid].is_card_object]
            self.assertEqual([old.object_id, second.object_id, first.object_id], arrivals)
            view = game.packet("pilot:A", full=True)["decision"]["ctx"]
            self.assertCountEqual([first.ref, second.ref], [row["id"] for row in view["options"]])
            before = authoritative_state_hash(game.state)
            for invalid in (old, foreign, redirected, survives, token):
                rejected = game.act("pilot:A", {"action_id": "choose", "choice": invalid.ref})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
            self.assert_replay(game)
            accepted = game.act("pilot:A", {"action_id": "choose", "choice": second.ref})
            self.assertTrue(accepted.ok, accepted.summary)
            self.assertFalse(game.state.stack)
            self.assertEqual(("battlefield", "A"), (game.state.cards[second.object_id].zone,
                                                   game.state.cards[second.object_id].controller))
            self.assertEqual("exile", game.state.cards[redirected.object_id].zone)
            self.assertEqual("battlefield", game.state.cards[survives.object_id].zone)
            self.assertEqual("graveyard", game.state.cards[foreign.object_id].zone)
            self.assert_replay(game)

    def test_starfall_unpromised_destroys_without_return_or_draw(self):
        game, spell = self.setup_cast("Starfall Invocation", 70245004)
        target = self.card(game, "Grizzly Bears", "battlefield")
        self._start_starfall(game, spell, promised=False)
        hand = len(game.state.players["B"].zones["hand"])
        for _ in range(12):
            if not game.state.stack:
                break
            pass_current(game)
        self.assertFalse(game.state.stack)
        self.assertEqual(hand, len(game.state.players["B"].zones["hand"]))
        self.assertEqual("graveyard", game.state.cards[target.object_id].zone)
        self.assert_replay(game)

    def test_starfall_rejects_forged_sweep_and_missing_committed_result_authority(self):
        from dataclasses import replace
        from quorune.rules.gift_graveyard_spells import SWEEP_RETURN_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime

        from quorune.rules.destruction_result_model import DestructionFollowup
        from quorune.rules.gift_graveyard_spells import sweep_return_base_effect

        with self.assertRaises(ValueError):
            DestructionFollowup.from_destruction_effect({
                **sweep_return_base_effect(), "followup": DestructionFollowup.owned_creature_return().to_dict(),
            })
        game, card = self.setup_cast("Starfall Invocation", 70245007)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        registry = load_default_capability_registry()
        deps = tuple(dep for dep in program.capability_dependencies if dep != SWEEP_RETURN_CAPABILITY)
        for changed in (
            replace(program, capability_dependencies=deps,
                    capability_closure=registry.closure(deps, profile="commander_review").to_dict()),
            replace(program, effects=({**program.effects[0], "regeneration_prohibited": True},)),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=registry, profile="commander_review",
            )["strict"])

    def test_consumed_gift_locks_two_independent_targets_and_opponent_chooses_greatest(self):
        for index, outcome in enumerate(("ordinary", "gift", "stale_return")):
            with self.subTest(outcome=outcome):
                promised = outcome != "ordinary"
                game, spell = self.setup_cast("Consumed by Greed", 70247001 + index)
                owned = self.card(game, "Grizzly Bears", "graveyard")
                foreign = self.card(game, "Grizzly Bears", "graveyard", owner="C")
                high, tied = [self.card(game, "Grizzly Bears", "battlefield", owner="B") for _ in range(2)]
                low = self.card(game, "Llanowar Elves", "battlefield", owner="B")
                crypt = self.card(game, "Tormod's Crypt", "battlefield", owner="B") if outcome == "stale_return" else None
                game.engine._grant_priority("A")
                game.engine.pump()
                action = next(row for row in game.packet("pilot:A", full=True)["decision"]["ctx"]["legal"]["actions"]
                              if row.get("card") == spell.ref)
                option = next(row for row in action["cost_options"]
                              if row["id"].endswith("+gift:C") == promised and
                              (promised or "+gift:" not in row["id"]))
                game.initial_checkpoint = checkpoint_envelope(game.state)
                game.commands.clear()
                game.decisions.clear()
                command = {"action_id": action["id"], "cost_option": option["id"], "pay": "auto"}
                bad = [["A", owned.ref], ["B", foreign.ref], ["B"]] if promised else [["A"], ["B", owned.ref]]
                for refs in bad:
                    before = authoritative_state_hash(game.state)
                    attempt = game.act("pilot:A", {**command, "targets": refs})
                    self.assertFalse(attempt.ok)
                    self.assertEqual(before, authoritative_state_hash(game.state))
                accepted = game.act("pilot:A", {**command, "targets": ["B", owned.ref] if promised else ["B"]})
                self.assertTrue(accepted.ok, accepted.summary)
                hand = len(game.state.players["C"].zones["hand"])
                life = game.state.players["B"].life
                if crypt is not None:
                    for _ in range(8):
                        if game.state.priority_player == "B":
                            break
                        pass_current(game)
                    activation = next(row for row in game.packet("pilot:B", full=True)["decision"]["ctx"]["legal"]["actions"]
                                      if row["id"].startswith(f"activate:{crypt.ref}:"))
                    done = game.act("pilot:B", {"action_id": activation["id"], "targets": ["A"], "pay": "auto"})
                    self.assertTrue(done.ok, done.summary)

                for _ in range(20):
                    if game.state.pending_decision.kind == "choice.apnap":
                        break
                    pass_current(game)
                self.assertEqual("choice.apnap", game.state.pending_decision.kind)
                self.assertEqual(["pilot:B"], game.pending_principals())
                self.assertCountEqual([high.ref, tied.ref], game.state.pending_decision.payload_by_actor["B"]["options"])
                self.assertEqual(hand + int(promised), len(game.state.players["C"].zones["hand"]))
                before = authoritative_state_hash(game.state)
                rejected = game.act("pilot:B", {"action_id": "choose", "cards": [low.ref]})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(game.state))
                self.assert_replay(game)
                done = game.act("pilot:B", {"action_id": "choose", "cards": [tied.ref]})
                self.assertTrue(done.ok, done.summary)
                self.assertFalse(game.state.stack)
                self.assertEqual("graveyard", game.state.cards[tied.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[high.object_id].zone)
                self.assertEqual("battlefield", game.state.cards[low.object_id].zone)
                self.assertEqual(life, game.state.players["B"].life)
                self.assertEqual({"ordinary": "graveyard", "gift": "hand", "stale_return": "exile"}[outcome],
                                 game.state.cards[owned.object_id].zone)
                self.assert_replay(game)

    def test_consumed_rejects_forged_opponent_and_missing_gift_return_capability(self):
        from dataclasses import replace
        from quorune.rules.gift_graveyard_spells import GREED_CAPABILITY
        from quorune.card_programs.binding import bind_semantic_program_runtime

        game, card = self.setup_cast("Consumed by Greed", 70247010)
        program, = game.engine.semantics.programs_for_oracle(card.oracle_id)
        registry = load_default_capability_registry()
        deps = tuple(dep for dep in program.capability_dependencies if dep != GREED_CAPABILITY)
        for changed in (
            replace(program, capability_dependencies=deps,
                    capability_closure=registry.closure(deps, profile="commander_review").to_dict()),
            replace(program, target_schema={**program.target_schema, "player_relation": "any"}),
        ):
            self.assertFalse(bind_semantic_program_runtime(
                changed, capability_registry=registry, profile="commander_review",
            )["strict"])

    def test_original_search_programs_are_strictly_capability_bound(self):
        registry = load_default_capability_registry()
        for name in ("Demonic Tutor", "Vampiric Tutor", "Worldly Tutor", "Merrow Harbinger", "Treasure Mage", "Trophy Mage", "Squadron Hawk", "Ignite the Beacon", "Entomb"):
            with self.subTest(name=name):
                record = self.db.lookup(name)
                ir = compile_oracle_card(record, capability_registry=registry, capability_profile="commander_review")
                self.assertEqual("exact", ir.status, ir.material_residuals)
                self.assertTrue(any(CAPABILITY in node.capability_dependencies for face in ir.faces for node in face.nodes))
                program = compile_best_available_card_program(self.db, record, semantic_registry=SemanticRegistry(), capability_registry=registry, capability_profile="commander_review")
                binding = bind_card_program_runtime(program, capability_registry=registry, profile="commander_review")
                self.assertTrue(binding["strict_capability_ready"], binding)

    def test_counted_search_schema_dependencies_and_mutations_fail_closed(self):
        from quorune.compiler.library_search_templates import fixed_library_search_effect_template
        template = fixed_library_search_effect_template("Search your library for a card, then shuffle and put that card on top.")
        self.assertIsInstance(template, FixedCountedLibrarySearchTemplate)
        effect = template.effect()
        self.assertEqual(template, FixedCountedLibrarySearchTemplate.from_effect(effect))
        for field, value in (("schema_version", True), ("shuffle_after", True), ("shuffle_before_placement", False), ("unknown", True)):
            malformed = copy.deepcopy(effect)
            malformed[field] = value
            self.assertEqual((), fixed_counted_library_search_node_capabilities(effects=(malformed,), target_schema=None, mechanic_ids=template.compiled()[3]))
        registry_data = json.loads((ROOT / "quorune/rules/capability-registry.json").read_text(encoding="utf-8"))
        row = next(row for row in registry_data["capabilities"] if row["id"] == CAPABILITY)
        row["status"] = "blocked"
        row["blockers"] = ["Deliberately blocked owner"]
        blocked = CapabilityRegistry(registry_data)
        record = self.db.lookup("Demonic Tutor")
        self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=blocked, capability_profile="commander_review").status)
        with patch("quorune.oracle_ir.fixed_library_search_effect_template", return_value=None):
            self.assertNotEqual("exact", compile_oracle_card(record, capability_registry=load_default_capability_registry(), capability_profile="commander_review").status)
        for text in ("Search your library for X cards, put them into your hand, then shuffle.", "Search your library for two creature cards, put them onto the battlefield, then shuffle.", "Search your library for a card named Clone or Sol Ring, put it into your hand, then shuffle."):
            self.assertIsNone(fixed_library_search_effect_template(text))

    def test_reviewed_search_precedence_requires_matching_trust_source_and_semantics(self):
        from quorune.card_programs.reviewed_overlay import shadowed_reviewed_program_keys
        record = self.db.lookup("Entomb")
        reviewed = next(
            program for program in SemanticRegistry().programs_for_oracle(record.oracle_id)
            if program.ability_id == "spell:front"
        )
        self.assertNotIn("schema_version", reviewed.effects[0])
        session, _spell = self.setup_cast("Entomb", 70123004)
        generated = session.engine.semantics.get(reviewed.key)
        self.assertEqual(2, generated.effects[0]["schema_version"])
        self.assertEqual(
            {reviewed.key}, shadowed_reviewed_program_keys(record, (generated,), (reviewed,)),
        )
        for field, value in (
            ("trust_level", "proposed"),
            ("target_schema", {"kind": "unsupported"}),
            ("provenance", {**generated.provenance, "source_oracle_hash": "0" * 64}),
            ("effects", [{**generated.effects[0], "reveal": True}]),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(generated)
                setattr(changed, field, value)
                self.assertEqual(
                    set(), shadowed_reviewed_program_keys(record, (changed,), (reviewed,)),
                )
        self.assertNotIn("schema_version", reviewed.effects[0])

    def test_persisted_reviewed_search_keeps_legacy_execution_and_replay(self):
        # Execute the actual preserved reviewed Entomb payload, rather than
        # claiming historical compatibility from decoding alone.
        session, spell = self.setup_cast("Entomb", 70123005)
        chosen = self.card(session, "Duplicant", "library")
        reviewed = next(
            program for program in SemanticRegistry().programs_for_oracle(spell.oracle_id)
            if program.ability_id == "spell:front"
        )
        restored = SemanticProgram.from_dict(reviewed.to_dict())
        self.assertEqual(reviewed.to_dict(), restored.to_dict())
        self.assertNotIn("schema_version", restored.effects[0])
        session.engine.semantics.put(restored)
        self.offered_cast(session, spell)
        self.reach_search(session)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("graveyard", session.state.cards[chosen.object_id].zone)
        self.assertEqual(restored.to_dict(), session.engine.semantics.get(restored.key).to_dict())
        self.assert_replay(session)

    def test_printed_unqualified_search_requires_a_card_and_keeps_private_choices(self):
        # CR 701.23b/d/e distinguish a stated quality from a pure quantity.
        session, spell = self.setup_cast("Demonic Tutor", 70123001)
        chosen = self.card(session, "Duplicant", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(1, schema["minimum"])
        self.assertFalse(schema["rules_may_fail_to_find"])
        self.assertIn(chosen.ref, schema["legal_refs"])
        for seat in ("B", "C", "D"):
            self.assertNotIn(chosen.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        before = authoritative_state_hash(session.state)
        for principal, response in (("pilot:B", {"action_id": "choose", "search_cards": [chosen.ref]}), ("pilot:A", {"action_id": "choose", "search_cards": []})):
            result = session.act(principal, response)
            self.assertFalse(result.ok)
            self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("hand", session.state.cards[chosen.object_id].zone)
        for seat in ("B", "C", "D"):
            self.assertNotIn(chosen.ref, json.dumps(session.packet(f"pilot:{seat}", full=True)))
        self.assert_replay(session)

    def test_printed_multi_search_commander_replacements_persist_per_card(self):
        # Jeska and Tevesh are printed Partner commanders. CR 903.9b applies
        # independently to each selected commander in the simultaneous move.
        session, spell = self.setup_cast("Ignite the Beacon", 90309001)
        engine = session.engine
        for card in engine.state.cards.values():
            if card.owner == "A" and card.is_commander:
                card.is_commander = False
                card.commander_designation_id = None
        commanders = [self.card(session, name, "library") for name in ("Jeska, Thrice Reborn", "Tevesh Szat, Doom of Fools")]
        for card in commanders:
            card.is_commander = True
            card.commander_designation_id = f"commander:{card.object_id}"
        engine.state.commander_oracle_ids["A"] = [card.oracle_id for card in commanders]
        self.offered_cast(session, spell)
        self.reach_search(session)
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in commanders]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        self.assertTrue(all(card.zone == "library" for card in commanders))
        before = authoritative_state_hash(session.state)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        decline = next(option["id"] for option in options if option["id"].startswith("decline:"))
        wrong = session.act("pilot:B", {"action_id": "choose", "choices": {"replacement": decline}})
        self.assertFalse(wrong.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        with tempfile.TemporaryDirectory() as directory:
            session.save(directory)
            session = CommanderSession.load(self.db, directory)
        result = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": decline}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("replacement.order", session.state.pending_decision.kind)
        options = session.packet("pilot:A", full=True)["decision"]["ctx"]["options"]
        apply = next(option["id"] for option in options if not option["id"].startswith("decline:"))
        result = session.act("pilot:A", {"action_id": "choose", "choices": {"replacement": apply}})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual(["command", "hand"], sorted(session.state.cards[card.object_id].zone for card in commanders))
        self.assert_replay(session)

    def test_printed_numeric_search_filters_x_zero_and_allows_failure_to_find(self):
        # Treasure Mage ruling: X in a library card's mana cost is zero.
        session, spell = self.setup_cast("Treasure Mage", 70123002)
        large = self.card(session, "Duplicant", "library")
        small = self.card(session, "Sol Ring", "library")
        variable = self.card(session, "Stonecoil Serpent", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertIn(large.ref, schema["legal_refs"])
        self.assertNotIn(small.ref, schema["legal_refs"])
        self.assertNotIn(variable.ref, schema["legal_refs"])
        self.assertEqual(0, schema["minimum"])
        self.assertTrue(schema["rules_may_fail_to_find"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "search_cards": [small.ref]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        result = session.act("pilot:A", {"action_id": "choose", "search_cards": []})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual("library", large.zone)
        self.assert_replay(session)

    def test_printed_top_search_excludes_found_card_from_shuffle_and_preserves_identity(self):
        # CR 701.24b and Vampiric Tutor's ruling: shuffle-and-place is one action.
        session, spell = self.setup_cast("Vampiric Tutor", 70124001)
        chosen = self.card(session, "Duplicant", "library")
        self.offered_cast(session, spell)
        self.reach_search(session)
        identity = chosen.logical_object_id
        life = session.state.players["A"].life
        shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
        calls = []
        original = ZoneTransitionOwner.shuffle_library
        def observed(owner, seat, **kwargs):
            calls.append(kwargs.get("excluded_object_ids", ()))
            return original(owner, seat, **kwargs)
        with patch.object(ZoneTransitionOwner, "shuffle_library", observed):
            result = session.act("pilot:A", {"action_id": "choose", "search_cards": [chosen.ref]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([(chosen.object_id,)], calls)
        self.assertEqual(chosen.object_id, session.state.players["A"].zones["library"][-1])
        self.assertEqual(identity, chosen.logical_object_id)
        self.assertEqual(shuffles + 1, session.state.players["A"].stats["shuffle_count"])
        self.assertEqual(life - 2, session.state.players["A"].life)
        self.assertIn("A", chosen.known_to)
        self.assertNotIn("B", chosen.known_to)
        library = session.state.players["A"].zones["library"]
        self.assertTrue(all(
            not session.state.cards[object_id].known_to
            and not session.state.cards[object_id].revealed_to
            for object_id in library if object_id != chosen.object_id
        ))
        packet = session.packet("pilot:A", full=True)
        self.assertEqual(
            [chosen.ref],
            [row["id"] for row in packet["state"]["players"]["A"]["known_top"]],
        )
        self.assertNotIn(
            "known_top", session.packet("pilot:B", full=True)["state"]["players"]["A"],
        )
        self.assert_replay(session)

    def test_printed_qualified_top_search_reveals_only_the_found_creature(self):
        for find_card in (True, False):
            with self.subTest(find_card=find_card):
                session, spell = self.setup_cast("Worldly Tutor", 70124010 + find_card)
                found = self.card(session, "Duplicant", "library")
                noncreature = self.card(session, "Sol Ring", "library")
                foreign = self.card(session, "Duplicant", "library", owner="B")
                self.offered_cast(session, spell)
                decision = self.reach_search(session)
                schema = decision["legal_actions"][0]["choice_schema"]
                self.assertTrue(schema["rules_may_fail_to_find"])
                self.assertIn(found.ref, schema["legal_refs"])
                self.assertNotIn(noncreature.ref, schema["legal_refs"])
                self.assertNotIn(foreign.ref, schema["legal_refs"])

                for seat in "BCD":
                    private_state = session.packet(f"pilot:{seat}", full=True)["state"]
                    self.assertNotIn(json.dumps(found.ref), json.dumps(private_state))
                    self.assertNotIn(json.dumps(noncreature.ref), json.dumps(private_state))

                before = authoritative_state_hash(session.state)
                for invalid in (noncreature, foreign):
                    denied = session.act("pilot:A", {"action_id": "choose", "search_cards": [invalid.ref]})
                    self.assertFalse(denied.ok, denied.summary)
                    self.assertEqual(before, authoritative_state_hash(session.state))

                identity = found.logical_object_id
                shuffles = session.state.players["A"].stats.get("shuffle_count", 0)
                calls = []
                original = ZoneTransitionOwner.shuffle_library

                def observe_shuffle(owner, seat, **kwargs):
                    calls.append(kwargs.get("excluded_object_ids", ()))
                    return original(owner, seat, **kwargs)

                with patch.object(ZoneTransitionOwner, "shuffle_library", observe_shuffle):
                    result = session.act(
                        "pilot:A", {"action_id": "choose", "search_cards": [found.ref] if find_card else []}
                    )
                self.assertTrue(result.ok, result.summary)
                self.assertEqual([(found.object_id,) if find_card else ()], calls)
                self.assertEqual(shuffles + 1, session.state.players["A"].stats["shuffle_count"])
                if find_card:
                    self.assertEqual(found.object_id, session.state.players["A"].zones["library"][-1])
                    self.assertEqual(identity, session.state.cards[found.object_id].logical_object_id)
                for seat in "ABCD":
                    player = session.packet(f"pilot:{seat}", full=True)["state"]["players"]["A"]
                    self.assertEqual(
                        [found.ref] if find_card else [],
                        [card["id"] for card in player.get("known_top", ())],
                    )
                self.assert_replay(session)

    def test_printed_named_multi_search_moves_one_simultaneous_private_batch(self):
        session, spell = self.setup_cast("Squadron Hawk", 70123003)
        hawks = [self.card(session, "Squadron Hawk", "library") for _ in range(4)]
        other = self.card(session, "Skyshroud Sentinel", "library")
        self.offered_cast(session, spell)
        decision = self.reach_search(session)
        schema = decision["legal_actions"][0]["choice_schema"]
        self.assertEqual(3, schema["maximum"])
        self.assertTrue(all(card.ref in schema["legal_refs"] for card in hawks))
        self.assertNotIn(other.ref, schema["legal_refs"])
        before = authoritative_state_hash(session.state)
        rejected = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in hawks]})
        self.assertFalse(rejected.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        selected = hawks[:3]
        calls = []
        original = ZoneTransitionOwner.move_cards_simultaneously
        def observed(owner, changes, **kwargs):
            calls.append(tuple(changes))
            return original(owner, changes, **kwargs)
        with patch.object(ZoneTransitionOwner, "move_cards_simultaneously", observed):
            result = session.act("pilot:A", {"action_id": "choose", "search_cards": [card.ref for card in selected]})
        self.assertTrue(result.ok, result.summary)
        self.assertEqual([tuple((card.object_id, "hand") for card in selected)], calls)
        self.assertTrue(all(session.state.cards[card.object_id].zone == "hand" for card in selected))
        self.assertEqual("library", session.state.cards[hawks[3].object_id].zone)
        self.assertTrue(all(set(session.engine.seats) <= set(session.state.cards[card.object_id].known_to) for card in selected))
        self.assert_replay(session)
