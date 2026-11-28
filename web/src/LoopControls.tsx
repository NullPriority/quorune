import { useRef, useState } from "react";
import type { useTableLoop } from "./useTableLoop";

export function LoopControls({ controller }: { controller: ReturnType<typeof useTableLoop> }) {
  const { loop, canRecord, startRecording, repeat, holdNow, stop, waitingForPriority, holdingPriority } = controller;
  const [count, setCount] = useState("1");
  const [hold, setHold] = useState(true);
  const menu = useRef<HTMLDetailsElement>(null);
  const status = { off: "", recording: "Recording", ready: "Ready", running: "Running", held: "Hold" }[loop.kind];
  const enabled = Number.isSafeInteger(Number(count)) && Number(count) > 0;
  return <details className="loop-controls" ref={menu}>
    <summary>Loop shortcut{status ? ` · ${status}` : ""}</summary>
    <div className="priority-controls-panel">
      {loop.kind === "off" && <>
        {loop.message && <p role="status">{loop.message}</p>}
        <p>Record your actions until the same visible table and priority window return. Pass priority yourself when the sequence calls for it.</p>
        <button type="button" disabled={!canRecord} onClick={() => {
          startRecording();
          if (menu.current) menu.current.open = false;
        }}>Record loop</button>
      </>}
      {loop.kind === "recording" && <>
        <p role="status">Recording · {loop.steps.length} accepted actions</p>
        <button type="button" className="secondary-button" onClick={stop}>Cancel recording</button>
      </>}
      {loop.kind === "ready" && <>
        <label>Repeat
          <input type="number" min={1} step={1} value={count} aria-label="Loop repeats"
            onChange={(event) => setCount(event.target.value)} />
        </label>
        <p>Each player keeps their usual response windows. The shortcut submits only your recorded actions and pauses if another player responds or a required choice changes the sequence.</p>
        <label>After these repeats
          <select value={hold ? "hold" : "resume"} onChange={(event) => setHold(event.target.value === "hold")}>
            <option value="hold">Hold my next priority</option>
            <option value="resume">Resume my usual priority settings</option>
          </select>
        </label>
        <button type="button" disabled={!enabled} onClick={() => repeat(Number(count), hold)}>Repeat recorded loop</button>
        <button type="button" className="secondary-button" onClick={holdNow}>Hold priority now</button>
        <button type="button" className="secondary-button" onClick={stop}>Discard recording</button>
      </>}
      {loop.kind === "running" && <>
        <p role="status">{waitingForPriority ? "Waiting for priority to return. " : ""}
          {loop.remaining} repeat{loop.remaining === 1 ? "" : "s"} left</p>
        <button type="button" className="secondary-button" onClick={stop}>Stop after the current action</button>
      </>}
      {loop.kind === "held" && <>
        {loop.message && <p>{loop.message}</p>}
        <p role="status">{holdingPriority ? "Holding priority" : "Hold at your next priority window"}</p>
        <button type="button" onClick={stop}>Release loop hold</button>
      </>}
    </div>
  </details>;
}
