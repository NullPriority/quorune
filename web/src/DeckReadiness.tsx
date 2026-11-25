import type { Seat } from "./api";

export function DeckReadiness({ deck }: { deck: NonNullable<Seat["deck"]> }) {
  const support = deck.preflight;
  const status = deck.format_legality.status;
  const preview = status === "preview_override_confirmed";
  const cardCount = support?.affected_card_count ?? 0;
  const affected = support?.affected_cards ?? [];
  const blockers = support?.data_integrity_blockers ?? [];
  const cardLabel = `${cardCount} ${cardCount === 1 ? "card needs" : "cards need"} review`;
  const behaviorReady = support?.card_behavior_ready ?? support?.trusted_only_ready;
  return <section className="deck-review-result" aria-label="Your deck checks">
    <dl className="deck-review-summary">
      <div><dt>Commander legality</dt><dd>{preview ? "Preview cards approved for this table"
        : status === "legal" ? "Legal" : "Validate this list again to check"}</dd></div>
      <div><dt>Card data</dt><dd>{support?.data_integrity_issue ? "Snapshot needs review" : support ? "Passed deck data check" : "Run the deck checks"}</dd></div>
      <div><dt>Card behavior</dt><dd>{support?.data_integrity_issue ? "Waiting for card data"
        : behaviorReady ? "Passed current checks" : "Needs review"}</dd></div>
      <div><dt>Match readiness</dt><dd>{support?.data_integrity_issue ? "Draft saved for review"
        : support?.trusted_only_ready ? "Ready" : "Submitted with review items"}</dd></div>
    </dl>
    {support?.data_integrity_issue && <p>Ask the host to use the Oracle and rulings snapshot for this release, then validate the deck again.</p>}
    {blockers.map((blocker) => <details key={blocker.diagnostic_id} className="deck-data-diagnostic">
      <summary>{blocker.card} uses different {blocker.kind === "rulings_source_mismatch" ? "rulings" : "Oracle"} versions</summary>
      <dl>
        <div><dt>Diagnostic ID</dt><dd><code>{blocker.diagnostic_id}</code></dd></div>
        <div><dt>Oracle ID</dt><dd><code>{blocker.oracle_id}</code></dd></div>
      </dl>
      <ul>{blocker.programs.map((program) => <li key={program.key}>
        <strong>{program.origin}</strong>
        <dl>
          <div><dt>Program key</dt><dd><code>{program.key}</code></dd></div>
          <div><dt>Oracle source hash</dt><dd><code>{program.oracle_hash ?? "Unavailable"}</code></dd></div>
          <div><dt>Rulings source hash</dt><dd><code>{program.rulings_hash ?? "Unavailable"}</code></dd></div>
        </dl>
      </li>)}</ul>
    </details>)}
    {support?.format_verification_pending && !support.data_integrity_issue && <p>Some rules in this format are still awaiting verification.</p>}
    {affected.length > 0 && <details>
      <summary>{cardLabel}</summary>
      <ul>{affected.map((card) => <li key={card.name}>
        <strong>{card.name}</strong> · {card.reasons.join(", ")}
      </li>)}</ul>
      {cardCount > affected.length && <p>Showing {affected.length} of {cardCount} cards that need review.</p>}
    </details>}
  </section>;
}
