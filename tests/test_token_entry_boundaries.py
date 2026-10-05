"""Unchanged-owner action reproductions; entry-event capture is diagnostic only."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from quorune.record import authoritative_state_hash
from quorune.session import CommanderSession

ROOT = Path(__file__).resolve().parents[1]
from quorune.carddb import CardDatabase
from quorune.deck import DeckDefinition, DeckEntry
from quorune.oracle_ir import register_generated_programs, compile_oracle_card
from quorune.rules.capabilities import load_default_capability_registry
from scripts.build_test_database import build_fixture_database


class TokenEntryActionReproductions(unittest.TestCase):
    from test_linked_exile_return import LinkedExileReturnActionTests as _helpers
    session = _helpers.session
    add = _helpers.add
    seal = _helpers.seal
    cast = _helpers.cast
    replay = _helpers.replay

    def order_triggers(self, session):
        decision = session.state.pending_decision
        if decision is None or decision.kind != "trigger.order":
            return
        principal = session.pending_principals()[0]
        projected = session.packet(principal, full=True)["decision"]
        refs = [row["id"] for row in projected["ctx"]["triggers"]]
        result = session.act(principal, {"action_id": "order", "triggers": refs})
        self.assertTrue(result.ok, result.summary)

    def finish(self, session):
        for _ in range(80):
            if not session.state.stack and not session.state.pending_trigger_batches:
                return
            session.engine.pump()
            if session.state.pending_decision.kind == "trigger.order":
                self.order_triggers(session)
                continue
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            self.assertEqual("priority", decision["kind"])
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Token-entry action did not finish")

    def advance_to_next_upkeep(self, session):
        for _ in range(160):
            if session.state.phase == "beginning" and session.state.step == "upkeep":
                return
            session.engine.pump()
            self.order_triggers(session)
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            kind = decision["kind"]
            if kind == "priority":
                action = {"action_id": "pass"}
            elif kind == "combat.attackers":
                action = {"action_id": "attack", "attackers": []}
            elif kind == "combat.blockers":
                action = {"action_id": "block", "blocks": []}
            else:
                self.fail(f"Unexpected turn-advance decision: {kind}")
            result = session.act(principal, action)
            self.assertTrue(result.ok, result.summary)
        self.fail("Token-entry action did not reach the next upkeep")

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        path = Path(cls.temporary.name) / "token-entry.sqlite3"
        build_fixture_database([
            ROOT / "tests/fixtures/linked-exile-return-cards.json",
            ROOT / "tests/fixtures/fixed-typed-event-trigger-cards.json",
            ROOT / "tests/fixtures/public-history-condition-cards.json",
            ROOT / "tests/fixtures/token-entry-boundary-cards.json",
        ], path)
        cls.db = CardDatabase(path)
        cls.deck = DeckDefinition("Generic entry boundary", [
            DeckEntry("Generic Blink Commander", 1, "commander"),
            DeckEntry("Generic Blink Plains", 15),
        ], ["Generic Blink Commander"])

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temporary.cleanup()

    def register(self, session, names):
        registry = load_default_capability_registry()
        records = tuple(self.db.lookup(name) for name in names)
        for record in records:
            ir = compile_oracle_card(record, capability_registry=registry,
                                     capability_profile="commander_review")
            self.assertEqual("exact", ir.status, (record.name, ir.material_residuals))
        register_generated_programs(
            self.db, session.engine.semantics, records,
            trust_level="provisional", capability_registry=registry,
            capability_profile="commander_review", promote_exact_runtime_handlers=True,
            promote_exact_trigger_programs=True, promote_exact_effect_programs=True,
            promote_exact_capability_declarations=True,
        )
        for record in records:
            programs = session.engine.semantics.programs_for_oracle(record.oracle_id)
            self.assertTrue(programs, record.name)
            self.assertTrue(all(session.engine.semantic_program_is_current_trusted(p)
                                for p in programs), record.name)

    def finish_creation(self, session):
        for _ in range(40):
            if not any(item.kind == "spell" for item in session.state.stack):
                self.order_triggers(session)
                return
            session.engine.pump()
            principal = session.pending_principals()[0]
            decision = session.packet(principal, full=True)["decision"]
            self.assertEqual("priority", decision["kind"])
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        self.fail("Creation spell did not finish")

    def test_simultaneous_copies_see_the_complete_entry_history(self):
        session = self.session(25400001)
        original_name = "Generic Nonland Entry Threshold Draw Fixture"
        spell_name = "Generic Entry Two Copies"
        observer_name = "Generic Token Entry Life Trigger Fixture"
        self.register(session, (original_name, spell_name, observer_name))
        original = self.add(session, original_name, "ORIGINAL", zone="battlefield")
        observer = self.add(session, observer_name, "OBSERVER", zone="battlefield")
        spell = self.add(session, spell_name, "COPY")
        self.assertFalse(session.state.turn_history.events)
        self.seal(session)
        before_draws = sum(e.code == "card.draw" for e in session.state.events)
        before_life = {seat: session.state.players[seat].life for seat in "ABCD"}
        self.cast(session, spell, [original.ref])
        self.finish_creation(session)
        copies = [c for c in session.state.cards.values()
                  if c.is_token and c.zone == "battlefield"]
        self.assertEqual(2, len(copies))
        triggers = [item for item in session.state.stack if item.kind == "triggered_ability"]
        expected_sources = {c.object_id for c in copies} | {observer.object_id}
        actual_sources = {item.source_object_id for item in triggers}
        self.finish(session)
        actual_draws = sum(e.code == "card.draw" for e in session.state.events) - before_draws
        self.replay(session)
        self.assertEqual(expected_sources, actual_sources)
        self.assertEqual(2, actual_draws)
        self.assertEqual(before_life["A"] + 1, session.state.players["A"].life)
        for seat in "BCD":
            self.assertEqual(before_life[seat] - 1, session.state.players[seat].life)

    def test_subsequent_haste_does_not_rewrite_entry_facts(self):
        session = self.session(25400002)
        # This control source has no Haste; its printed/copied keyword is Vigilance.
        original_name = "Generic Entry Vigilance Creature"
        spell_name = "Generic Entry Subsequent Haste"
        self.register(session, (original_name, spell_name))
        original = self.add(session, original_name, "ORIGINAL", zone="battlefield")
        spell = self.add(session, spell_name, "COPY")
        self.assertNotIn("Haste", session.engine._copyable_characteristics(original)["keywords"])
        self.seal(session)
        occurrences = []
        dispatch = session.engine._dispatch_semantic_event
        def capture(kind, context, **kwargs):
            if kind in {"token.created", "permanent.enter", "creature.enter"}:
                occurrences.append((kind, deepcopy(context)))
            return dispatch(kind, context, **kwargs)
        with patch.object(session.engine, "_dispatch_semantic_event", side_effect=capture):
            self.cast(session, spell, [original.ref])
            self.finish(session)
        self.assertEqual(3, len(occurrences))
        print("entry keywords", [(kind, context.get("keywords")) for kind, context in occurrences])
        for kind, context in occurrences:
            self.assertNotIn("haste", context["keywords"], kind)
        token = next(c for c in session.state.cards.values() if c.is_token)
        self.assertIn("haste", session.engine._combat_keywords(token))
        self.assertNotIn("Haste", session.engine._copyable_characteristics(token)["keywords"])
        self.advance_to_next_upkeep(session)
        self.assertNotIn("haste", session.engine._combat_keywords(session.state.cards[token.object_id]))
        self.replay(session)

    def test_intrinsic_exception_and_static_haste_are_entry_facts(self):
        cases = (
            ("intrinsic", "Generic History Victim", "Generic Entry One Copy", False),
            ("copy exception", "Generic Entry Vigilance Creature", "Generic Entry Copy Exception", False),
            ("battlefield static", "Generic Entry Vigilance Creature", "Generic Entry One Copy", True),
        )
        for offset, (label, original_name, spell_name, use_static) in enumerate(cases):
            with self.subTest(label=label):
                session = self.session(25400010 + offset)
                names = [original_name, spell_name]
                if use_static:
                    names.append("Generic Entry Haste Static")
                self.register(session, names)
                original = self.add(session, original_name, "ORIGINAL", zone="battlefield")
                spell = self.add(session, spell_name, "COPY")
                if use_static:
                    self.add(session, "Generic Entry Haste Static", "STATIC", zone="battlefield")
                self.seal(session)
                occurrences = []
                dispatch = session.engine._dispatch_semantic_event
                def capture(kind, context, **kwargs):
                    if kind == "permanent.enter":
                        occurrences.append(deepcopy(context))
                    return dispatch(kind, context, **kwargs)
                with patch.object(session.engine, "_dispatch_semantic_event", side_effect=capture):
                    self.cast(session, spell, [original.ref])
                    self.finish(session)
                self.assertEqual(1, len(occurrences))
                self.assertIn("haste", occurrences[0]["keywords"])
                token = next(c for c in session.state.cards.values() if c.is_token)
                self.assertIn("haste", session.engine._combat_keywords(token))
                copy_keywords = session.engine._copyable_characteristics(token)["keywords"]
                self.assertEqual(not use_static, "Haste" in copy_keywords)

    def test_one_copy_and_sequential_creation_history_controls(self):
        session = self.session(25400020)
        original_name = "Generic Nonland Entry Threshold Draw Fixture"
        self.register(session, (original_name, "Generic Entry One Copy"))
        original = self.add(session, original_name, "ORIGINAL", zone="battlefield")
        spells = [self.add(session, "Generic Entry One Copy", f"COPY{index}") for index in range(2)]
        self.seal(session)
        prior_draws = sum(e.code == "card.draw" for e in session.state.events)
        for index, spell in enumerate(spells):
            self.cast(session, spell, [original.ref])
            self.finish_creation(session)
            tokens = [c for c in session.state.cards.values() if c.is_token]
            triggers = [i for i in session.state.stack if i.kind == "triggered_ability"]
            expected_sources = {tokens[-1].object_id} if index else set()
            self.assertEqual(expected_sources, {i.source_object_id for i in triggers})
            self.finish(session)
            rows = [e for e in session.state.turn_history.events if e.kind == "permanent_entered"]
            self.assertEqual({c.logical_object_id for c in tokens}, {e.object_incarnation for e in rows})
            self.assertEqual(index + 1, len(rows))
            self.assertTrue(all(e.actor == "A" and "creature" in e.types for e in rows))
            self.assertEqual(prior_draws + index, sum(e.code == "card.draw" for e in session.state.events))
        self.replay(session)

    def test_creation_and_entry_counter_choices_resume_once_and_replay(self):
        session = self.session(25400040)
        source_name = "Generic Entry Counter Walker"
        spell_name = "Generic Entry Subsequent Haste"
        bonus_names = ("Generic Entry Map Bonus", "Generic Entry Food Bonus",
                       "Generic Entry Counter Double", "Generic Entry Counter Add")
        self.register(session, (source_name, spell_name, *bonus_names))
        source = self.add(session, source_name, "ORIGINAL", owner="B", controller="A", zone="battlefield")
        spell = self.add(session, spell_name, "COPY")
        for index, name in enumerate(bonus_names):
            self.add(session, name, f"BONUS{index}", zone="battlefield")
        self.seal(session)
        before = authoritative_state_hash(session.state)
        bad = session.act("pilot:B", {"action_id": "cast:" + spell.ref, "targets": [source.ref], "pay": "auto"})
        self.assertFalse(bad.ok)
        self.assertEqual(before, authoritative_state_hash(session.state))
        self.cast(session, spell, [source.ref])
        for _ in range(16):
            if session.state.pending_decision.kind == "replacement.order":
                break
            principal = session.pending_principals()[0]
            result = session.act(principal, {"action_id": "pass"})
            self.assertTrue(result.ok, result.summary)
        else:
            self.fail("Creation replacement choice was not offered")
        kinds = []
        for _ in range(4):
            decision = session.state.pending_decision
            if decision.kind != "replacement.order":
                break
            root_event = decision.continuation["replacement_batch"]["events"][0]
            kind = root_event["kind"]
            if kind == "zone.change":
                self.assertTrue(root_event["payload"]["prospective_subject"])
                self.assertTrue(any(child["kind"] == "counter.place" for child in root_event["children"]))
            kinds.append(kind)
            self.assertFalse(any(c.is_token for c in session.state.cards.values()))
            self.assertFalse([e for e in session.state.turn_history.events if e.kind == "permanent_entered"])
            for seat in "BCD":
                self.assertIsNone(session.packet(f"pilot:{seat}", full=True)["decision"])
            projected = session.packet("pilot:A", full=True)["decision"]
            self.assertNotIn("replacement_batch", str(projected))
            self.assertNotIn("object_id", str(projected))
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "pending"
                session.save(path)
                restored = CommanderSession.load(self.db, path)
            self.assertEqual(authoritative_state_hash(session.state), authoritative_state_hash(restored.state))
            for principal, selection in (("pilot:B", "unknown"), ("pilot:A", "unknown")):
                before = authoritative_state_hash(restored.state)
                rejected = restored.act(principal, {"action_id": "choose", "replacement": selection})
                self.assertFalse(rejected.ok)
                self.assertEqual(before, authoritative_state_hash(restored.state))
            selected = restored.packet("pilot:A", full=True)["decision"]["ctx"]["options"][0]["id"]
            accepted = restored.act("pilot:A", {"action_id": "choose", "replacement": selected})
            self.assertTrue(accepted.ok, accepted.summary)
            session = restored
        self.assertEqual(["token.create", "zone.change"], kinds)
        self.finish(session)
        tokens = [c for c in session.state.cards.values() if c.is_token and c.zone == "battlefield"]
        self.assertEqual(3, len(tokens))
        self.assertTrue(all(c.owner == "A" and c.controller == "A" for c in tokens))
        copied = next(c for c in tokens if c.annotations.get("copied_from") == source.object_id)
        self.assertIn(copied.counters["loyalty"], {9, 10})
        self.assertIn("haste", session.engine._combat_keywords(copied))
        self.assertNotIn("Haste", session.engine._copyable_characteristics(copied)["keywords"])
        rows = [e for e in session.state.turn_history.events if e.kind == "permanent_entered"]
        self.assertEqual(3, len(rows))
        self.assertEqual({c.logical_object_id for c in tokens}, {e.object_incarnation for e in rows})
        self.assertTrue(all(e.actor == "A" for e in rows))
        self.assertEqual(1, sum(e.code == "token.create" for e in session.state.events))
        self.assertEqual(1, len(session.state.continuous_effects))
        self.replay(session)


class TokenEntryHistoryOwnerTests(unittest.TestCase):
    """Isolated batch ordering controls, not card-action certification."""

    def history_contract(self, dispatch_creation):
        from types import SimpleNamespace
        from quorune.model import CardInstance
        for created in ((), ("first",), ("first", "second"), ("second", "first")):
            cards = {key: CardInstance(object_id=key, ref=key.upper(),
                      oracle_id="generic:" + key, printed_name="Generic token",
                      owner="A", controller="A", zone="battlefield", is_token=True)
                     for key in created}
            rows, seen = [], []
            data = {"type_line": "Creature — Wizard", "keywords": ["Vigilance"], "mana_value": 0}
            host = SimpleNamespace(
                state=SimpleNamespace(cards=cards, turn_sequence=4,
                    players={"A": SimpleNamespace(stats={})}),
                _effective_card_data=lambda card: dict(data),
                _copyable_characteristics=lambda card: dict(data),
                _log=lambda *args, **kwargs: None,
                _record_turn_history=lambda kind, **kwargs: rows.append((kind, kwargs)),
            )
            def dispatch(kind, context, **kwargs):
                self.assertEqual(len(created), len(rows), "Complete committed history precedes every discovery")
                seen.append((kind, context["card_object_identity"], tuple(context["keywords"])))
            host._dispatch_semantic_event = dispatch
            dispatch_creation(host, "A", created, name="Generic", base_quantity=len(created),
                              replacement_components=(), replacement_journal=(), reason="owner diagnostic")
            self.assertEqual(len(created), len(rows))
            self.assertEqual({c.logical_object_id for c in cards.values()}, {r[1]["object_incarnation"] for r in rows})
            self.assertEqual(len(created) * 3, len(seen))
            self.assertTrue(all(r[1]["actor"] == "A" and r[1]["types"] == ("creature",) for r in rows))

    def test_zero_single_reversed_groups_have_complete_nonduplicate_history(self):
        from quorune.token_creation import _record_and_dispatch_token_creation
        self.history_contract(_record_and_dispatch_token_creation)

    def test_incomplete_history_ordering_mutant_is_killed(self):
        import inspect
        from quorune import token_creation as owner
        source = inspect.getsource(owner._record_and_dispatch_token_creation)
        before = '''        host._record_turn_history(
            "permanent_entered",
            actor=controller,
            object_incarnation=context["card_object_identity"],
            types=tuple(context["types"]),
        )
'''
        self.assertEqual(1, source.count(before))
        mutant = source.replace("    for context in entry_contexts:\n" + before, "").replace(
            "    for context in entry_contexts:\n",
            '''    for context in entry_contexts:
        host._record_turn_history("permanent_entered", actor=controller,
            object_incarnation=context["card_object_identity"], types=tuple(context["types"]))
''')
        namespace = dict(vars(owner))
        exec(compile(mutant, "<partial-simultaneous-token-history>", "exec"), namespace)
        with self.assertRaises(AssertionError):
            self.history_contract(namespace["_record_and_dispatch_token_creation"])

    def test_subsequent_aftercare_ordering_mutant_is_killed(self):
        import inspect
        from types import SimpleNamespace
        from quorune import token_creation as owner
        from quorune import token_copy_runtime
        original = owner._commit_resolved_token_specs
        source = inspect.getsource(original)
        call = "    finish_copy_aftercare(host, controller, created)\n"
        self.assertEqual(1, source.count(call))
        mutant = source.replace(call, "").replace(
            "    _record_and_dispatch_token_creation(\n", call + "    _record_and_dispatch_token_creation(\n")
        namespace = dict(vars(owner))
        exec(compile(mutant, "<retroactive-copy-aftercare>", "exec"), namespace)
        for resolve, expected in ((original, False), (namespace[original.__name__], True)):
            calls = []
            host = SimpleNamespace(state=SimpleNamespace(timestamp_sequence=0, revision=1,
                event_sequence=0, cards={"created": SimpleNamespace(ref="T1")}))
            replacements = (
                patch.object(owner, "_preflight_aura_token_specs", return_value=()),
                patch.object(owner, "_prepare_token_objects", return_value=()),
                patch.object(owner, "_prepare_token_entry_counters", return_value=((), object(), {})),
                patch.object(owner, "_commit_token_specs", return_value=(["created"], [])),
                patch.object(owner, "_record_and_dispatch_token_creation", side_effect=lambda *a, **k: calls.append("entry")),
                patch.object(token_copy_runtime, "finish_copy_aftercare", side_effect=lambda *a, **k: calls.append("aftercare")),
            )
            from contextlib import ExitStack
            with ExitStack() as stack:
                for replacement in replacements:
                    stack.enter_context(replacement)
                # The extracted mutation uses its copied globals, so give it
                # the same isolated collaborators as the production function.
                if resolve is not original:
                    for name in ("_preflight_aura_token_specs", "_prepare_token_objects",
                                 "_prepare_token_entry_counters",
                                 "_commit_token_specs", "_record_and_dispatch_token_creation"):
                        namespace[name] = getattr(owner, name)
                resolve(host, "A", resolved=owner.ResolvedTokenSpecs((), (), ()),
                        base_name="Generic", base_quantity=1, reason="isolated timing contract")
            if expected:
                with self.assertRaises(AssertionError):
                    self.assertEqual(["entry", "aftercare"], calls)
            else:
                self.assertEqual(["entry", "aftercare"], calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
