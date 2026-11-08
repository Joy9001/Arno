import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type PointerEvent } from "react";
import { formatDate, formatPaise, isoDate } from "../format";
import type { ProjectionPayload } from "../projection";
import { daysAgo, useExpenses } from "../spending";
import type { Upcoming } from "../types";
import { useApi } from "../useApi";

const HEIGHT = 300;
const PAST_DAYS = 30;
const PAD_TOP = 26;
const PAD_BOTTOM = 34;

function shortDate(value: string): string {
  const date = new Date(`${value}T00:00:00`);
  return date.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
}

function dayIndex(from: string, to: string): number {
  return Math.round(
    (new Date(`${to}T00:00:00`).getTime() - new Date(`${from}T00:00:00`).getTime()) / 86_400_000,
  );
}

/**
 * Money on one axis: the ledger's daily spending behind today and the projection ahead of it
 * (median and P10–P90 band), with the floor and dated schedule events. Drag or use the arrow
 * keys to read any future day. Every figure is a ledger sum or a projection return.
 */
export function Timeline({ payload }: { payload: ProjectionPayload }) {
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(960);
  const today = isoDate(new Date());
  const past = useExpenses(daysAgo(PAST_DAYS - 1), today);
  const upcoming = useApi<Upcoming>(
    `/api/schedules/upcoming?days=${Math.min(payload.horizon_days, 730)}`,
  );
  const curve = payload.curve;
  const lowIndex = Math.max(
    0,
    curve.findIndex((point) => point.date === payload.median_low.date),
  );
  const [scrub, setScrub] = useState(lowIndex);

  useEffect(() => {
    const element = box.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const floorPaise = payload.floor?.paise ?? payload.floor_paise ?? null;
  const floorSet = payload.floor_set ?? (floorPaise ?? 0) > 0;

  const geometry = useMemo(() => {
    const pastWidth = Math.round(width * (width < 640 ? 0.22 : 0.32));
    const futureWidth = Math.max(1, width - pastWidth);
    const span = Math.max(1, curve.length - 1);
    const values = curve.flatMap((point) => [point.p10_paise, point.p90_paise]);
    if (floorSet && floorPaise !== null) values.push(floorPaise);
    const top = Math.max(...values, 1);
    const bottom = Math.min(...values, 0);
    const range = Math.max(1, top - bottom);
    const y = (paise: number) =>
      PAD_TOP + (1 - (paise - bottom) / range) * (HEIGHT - PAD_TOP - PAD_BOTTOM);
    const x = (index: number) => pastWidth + (index / span) * futureWidth;
    const line = curve
      .map((point, index) => `${index ? "L" : "M"}${x(index).toFixed(1)},${y(point.median_paise).toFixed(1)}`)
      .join(" ");
    const upper = curve.map((point, index) => `${x(index).toFixed(1)},${y(point.p90_paise).toFixed(1)}`);
    const lower = curve
      .map((point, index) => `${x(index).toFixed(1)},${y(point.p10_paise).toFixed(1)}`)
      .reverse();
    const band = curve.length ? `M${upper.join(" L")} L${lower.join(" L")} Z` : "";
    return { pastWidth, futureWidth, x, y, line, band, span };
  }, [curve, floorPaise, floorSet, width]);

  const bars = useMemo(() => {
    const byDay = new Map<string, number>();
    for (const movement of past.data?.rows ?? []) {
      byDay.set(movement.date, (byDay.get(movement.date) ?? 0) + movement.amount_paise);
    }
    const peak = Math.max(...byDay.values(), 1);
    const slot = geometry.pastWidth / PAST_DAYS;
    return Array.from({ length: PAST_DAYS }, (_, offset) => {
      const date = daysAgo(PAST_DAYS - 1 - offset);
      const paise = byDay.get(date) ?? 0;
      const height = (paise / peak) * (HEIGHT - PAD_TOP - PAD_BOTTOM) * 0.62;
      return { date, paise, x: offset * slot + slot * 0.22, w: Math.max(2, slot * 0.56), height };
    });
  }, [geometry.pastWidth, past.data]);

  const pastTotal = bars.reduce((sum, bar) => sum + bar.paise, 0);
  const events = (upcoming.data?.events ?? []).filter(
    (event) => event.amount_paise !== null && dayIndex(curve[0]?.date ?? today, event.date) >= 0,
  );
  const point = curve[Math.min(scrub, curve.length - 1)];

  function pick(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const offset = event.clientX - rect.left - geometry.pastWidth;
    if (offset < 0) return;
    setScrub(Math.round((offset / geometry.futureWidth) * geometry.span));
  }

  function step(event: KeyboardEvent<HTMLDivElement>) {
    const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    event.preventDefault();
    setScrub((value) => Math.min(curve.length - 1, Math.max(0, value + delta * (event.shiftKey ? 7 : 1))));
  }

  if (!curve.length) return null;
  const scrubX = geometry.x(Math.min(scrub, curve.length - 1));
  const popLeft = Math.min(Math.max(scrubX + 14, geometry.pastWidth), width - 250);

  return (
    <section className="timeline" aria-label="Money timeline">
      <div className="timeline-labels">
        <span>← What happened · ledger</span>
        <span>
          What could happen · {payload.runs ? `${payload.runs.toLocaleString("en-IN")} runs` : "projection"} →
        </span>
      </div>
      <div
        ref={box}
        className="timeline-plot"
        style={{ height: HEIGHT }}
        role="slider"
        tabIndex={0}
        aria-label="Day on the projection"
        aria-valuemin={0}
        aria-valuemax={curve.length - 1}
        aria-valuenow={scrub}
        aria-valuetext={
          point
            ? `${formatDate(point.date)}: median ${formatPaise(point.median_paise)}, range ${formatPaise(
                point.p10_paise,
              )} to ${formatPaise(point.p90_paise)}`
            : undefined
        }
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          pick(event);
        }}
        onPointerMove={(event) => {
          if (event.buttons) pick(event);
        }}
        onKeyDown={step}
      >
        <svg width={width} height={HEIGHT} aria-hidden="true">
          <defs>
            <linearGradient id="tl-band" x1="0" x2="1">
              <stop offset="0" stopColor="#1ec8a5" stopOpacity="0.38" />
              <stop offset="1" stopColor="#2b59ff" stopOpacity="0.12" />
            </linearGradient>
            <linearGradient id="tl-line" x1="0" x2="1">
              <stop offset="0" stopColor="#d4ff3a" />
              <stop offset="0.6" stopColor="#1ec8a5" />
              <stop offset="1" stopColor="#2b59ff" />
            </linearGradient>
          </defs>
          <rect x="0" y="0" width={geometry.pastWidth} height={HEIGHT} className="tl-past-wash" />
          {bars.map((bar) => (
            <rect
              key={bar.date}
              x={bar.x}
              y={HEIGHT - PAD_BOTTOM - bar.height}
              width={bar.w}
              height={bar.height}
              rx={Math.min(3, bar.w / 2)}
              className="tl-bar"
            />
          ))}
          <path d={geometry.band} fill="url(#tl-band)" className="tl-band" />
          <path d={geometry.line} fill="none" stroke="url(#tl-line)" strokeWidth="3.5" className="tl-line" pathLength={1} />
          {!floorSet && (
            <line
              x1={geometry.pastWidth}
              x2={width}
              y1={geometry.y(0)}
              y2={geometry.y(0)}
              className="tl-zero"
            />
          )}
          {floorSet && floorPaise !== null && (
            <line
              x1={geometry.pastWidth}
              x2={width}
              y1={geometry.y(floorPaise)}
              y2={geometry.y(floorPaise)}
              className="tl-floor"
            />
          )}
          <line x1={geometry.pastWidth} x2={geometry.pastWidth} y1={8} y2={HEIGHT - PAD_BOTTOM + 6} className="tl-today" />
          {events.slice(0, 40).map((event) => {
            const index = dayIndex(curve[0].date, event.date);
            if (index > geometry.span) return null;
            const income = event.kind === "income";
            return (
              <circle
                key={`${event.date}-${event.name}`}
                cx={geometry.x(index)}
                cy={HEIGHT - PAD_BOTTOM + 14}
                r="4.5"
                className={income ? "tl-event in" : event.kind === "card_due" ? "tl-event due" : "tl-event"}
              >
                <title>
                  {`${shortDate(event.date)} · ${event.name} · ${formatPaise(event.amount_paise)}`}
                </title>
              </circle>
            );
          })}
          <line x1={scrubX} x2={scrubX} y1={8} y2={HEIGHT - PAD_BOTTOM} className="tl-scrub" />
          {point && <circle cx={scrubX} cy={geometry.y(point.median_paise)} r="6" className="tl-dot" />}
        </svg>
        <span className="tl-today-tag" style={{ left: geometry.pastWidth + 8 }}>
          Today
        </span>
        {point && (
          <div className="tl-pop" style={{ left: popLeft }}>
            <span className="tl-pop-k">
              {formatDate(point.date)}
              {point.date === payload.median_low.date ? " · tightest day" : ""}
            </span>
            <strong>{formatPaise(point.median_paise)}</strong>
            <span>
              Median · {formatPaise(point.p10_paise)} – {formatPaise(point.p90_paise)}
            </span>
          </div>
        )}
        <div className="tl-past-tag">
          <span>Last {PAST_DAYS} days</span>
          <strong>{past.loading && !past.data ? "…" : formatPaise(pastTotal)}</strong>
          <span>
            {past.error
              ? "Spending unavailable"
              : `spent · ${past.data?.rows.length ?? 0} rows${past.data?.truncated ? " read so far" : ""}`}
          </span>
        </div>
      </div>
      <div className="timeline-axis" aria-hidden="true">
        <span style={{ left: 0 }}>{shortDate(daysAgo(PAST_DAYS - 1))}</span>
        <span style={{ left: geometry.pastWidth }}>{shortDate(curve[0].date)}</span>
        <span style={{ right: 0 }}>{shortDate(curve[curve.length - 1].date)}</span>
      </div>
      {events.length > 0 && (
        <ol className="timeline-events" aria-label="Coming up">
          {events.slice(0, 6).map((event) => (
            <li key={`${event.date}-${event.name}`} className={event.kind === "income" ? "in" : event.kind === "card_due" ? "due" : ""}>
              <span className="d">{shortDate(event.date)}</span>
              <span className="n">{event.name}</span>
              <strong>
                {event.kind === "income" ? "+" : ""}
                {formatPaise(event.amount_paise)}
              </strong>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
