import type { CardDataMismatch, CardSourceExcerpt, Seat } from "./api";

function changeSummary(comparison: CardDataMismatch["comparison"]): string {
  if (!comparison) return "Earlier text unavailable";
  const { changes } = comparison;
  if (changes.length === 0) return "No text differences";
  if (changes.every((change) => change.kind === "formatting")) return "Formatting only";
  return (["added", "removed", "changed", "formatting"] as const).flatMap((kind) => {
    const count = changes.filter((change) => change.kind === kind).length;
    const label = kind === "formatting" ? (count === 1 ? "formatting edit" : "formatting edits") : kind;
    return count ? [`${count} ${label}`] : [];
  }).join(" · ");
}

function SourceExcerpt({ label, excerpt }: { label: string; excerpt: CardSourceExcerpt }) {
  const publisher = excerpt.source === "wotc" ? "Wizards of the Coast"
    : excerpt.source === "scryfall" ? "Scryfall" : undefined;
  return <figure className="card-source-excerpt">
    <figcaption>
      <strong>{label}</strong>
      {publisher && <span>{publisher}</span>}
      {excerpt.published_at && <time dateTime={excerpt.published_at}>{excerpt.published_at}</time>}
    </figcaption>
    <blockquote>{excerpt.text}</blockquote>
  </figure>;
}

function CardDataReview({ mismatch }: { mismatch: CardDataMismatch }) {
  const { comparison } = mismatch;
  const source = mismatch.kind === "rulings_source_mismatch" ? "Card rulings" : "Oracle text";
  return <details className="deck-data-mismatch">
    <summary>
      <strong>{mismatch.card}</strong>
      <span>{source} · {changeSummary(comparison)}</span>
    </summary>
    <div className="deck-data-comparison">
      {comparison ? <>
        {(comparison.reviewed_date || comparison.current_date) && <p>
          {comparison.reviewed_date && <>Reviewed data {comparison.reviewed_date.slice(0, 10)}</>}
          {comparison.reviewed_date && comparison.current_date && " · "}
          {comparison.current_date && <>Current data {comparison.current_date.slice(0, 10)}</>}
        </p>}
        {comparison.changes.map((change, index) => <div className="card-source-change" key={index}>
          {change.before && <SourceExcerpt label={change.kind === "removed" ? "Removed" : "Earlier text"} excerpt={change.before} />}
          {change.after && <SourceExcerpt label={change.kind === "added" ? "Added" : "Current text"} excerpt={change.after} />}
        </div>)}
      </> : <p>The host has no retained copy that matches the earlier implementation for this card.</p>}
      <details className="deck-data-diagnostic">
        <summary>Technical details</summary>
        <dl>
          <div><dt>Diagnostic ID</dt><dd><code>{mismatch.diagnostic_id}</code></dd></div>
          <div><dt>Oracle ID</dt><dd><code>{mismatch.oracle_id}</code></dd></div>
        </dl>
        <ul>{mismatch.programs.map((program) => <li key={program.key}>
          <strong>{program.origin}</strong>
          <dl>
            <div><dt>Program key</dt><dd><code>{program.key}</code></dd></div>
            <div><dt>Oracle source hash</dt><dd><code>{program.oracle_hash ?? "Unavailable"}</code></dd></div>
            <div><dt>Rulings source hash</dt><dd><code>{program.rulings_hash ?? "Unavailable"}</code></dd></div>
          </dl>
        </li>)}</ul>
      </details>
    </div>
  </details>;
}

export function DeckReadiness({ deck }: { deck: NonNullable<Seat["deck"]> }) {
  const support = deck.preflight;
  const status = deck.format_legality.status;
  const preview = status === "preview_override_confirmed";
  const cardCount = support?.affected_card_count ?? 0;
  const affected = support?.affected_cards ?? [];
  const blockers = support?.data_integrity_blockers ?? [];
  const mismatchCount = new Set(blockers.map(({ oracle_id }) => oracle_id)).size;
  const dataIssue = support?.data_integrity_issue;
  const cardLabel = `${cardCount} ${cardCount === 1 ? "card needs" : "cards need"} review`;
  const behaviorReady = support?.card_behavior_ready ?? support?.trusted_only_ready;
  return <section className="deck-review-result" aria-label="Your deck checks">
    <dl className="deck-review-summary">
      <div><dt>Commander legality</dt><dd>{preview ? "Preview cards approved for this table"
        : status === "legal" ? "Legal" : "Validate this list again to check"}</dd></div>
      {dataIssue ? <div><dt>Match readiness</dt><dd>Draft saved for review</dd></div> : <>
        <div><dt>Card data</dt><dd>{support ? "Passed deck data check" : "Run the deck checks"}</dd></div>
        <div><dt>Card behavior</dt><dd>{behaviorReady ? "Passed current checks" : "Needs review"}</dd></div>
        <div><dt>Match readiness</dt><dd>{support?.trusted_only_ready ? "Ready" : "Submitted with review items"}</dd></div>
      </>}
    </dl>
    {dataIssue && <div className="deck-data-review">
      <h3>{mismatchCount ? `${mismatchCount} ${mismatchCount === 1 ? "card needs" : "cards need"} a card data review`
        : "Card data needs review"}</h3>
      <p>The current card data differs from the data used to verify these cards.</p>
      {blockers.map((blocker) => <CardDataReview key={blocker.diagnostic_id} mismatch={blocker} />)}
    </div>}
    {support?.format_verification_pending && !dataIssue && <p>Some rules in this format are still awaiting verification.</p>}
    {affected.length > 0 && <details>
      <summary>{cardLabel}</summary>
      <ul>{affected.map((card) => <li key={card.name}>
        <strong>{card.name}</strong> · {card.reasons.join(", ")}
      </li>)}</ul>
      {cardCount > affected.length && <p>Showing {affected.length} of {cardCount} cards that need review.</p>}
    </details>}
  </section>;
}
