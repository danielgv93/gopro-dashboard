// Clip strip: one card per recording, in film order. Click to jump to it; the switch keeps it in the film or leaves
// it out whole. Stateless like the timeline: the editor calls render() and receives gestures through callbacks.

import { cutsOf } from "./film.js";
import { app, fmtPos, mediaUrl } from "./shared.js";

const { el } = app;

export class ClipsView {
  /** @param handlers { jump(entry), toggle(entry) } */
  constructor(root, handlers) {
    this.root = root;
    this.handlers = handlers;
    this.cards = new Map();
  }

  setSource(source) {
    this.cards = new Map(source.recordings.map((entry, i) => [entry.key, this.#card(entry, i + 1)]));
    this.root.replaceChildren(...this.cards.values());
  }

  render(cuts, currentKey) {
    for (const [key, card] of this.cards) {
      const own = cutsOf(cuts, key);
      const kept = own.reduce((sum, c) => sum + c.end - c.start, 0);
      const duration = Number(card.dataset.duration);
      const state = !own.length ? "removed" : kept < duration - 0.05 ? "trimmed" : "whole";
      card.dataset.state = state;
      card.classList.toggle("current", key === currentKey);
      card.querySelector(".clip-kept").textContent =
        state === "removed" ? "Left out" : state === "trimmed" ? `Keeps ${fmtPos(kept)} of ${fmtPos(duration)}` : fmtPos(duration);
      const toggle = card.querySelector("input");
      toggle.checked = own.length > 0;
      if (key === currentKey && card.dataset.current !== "1") card.scrollIntoView({ block: "nearest", inline: "nearest" });
      card.dataset.current = key === currentKey ? "1" : "";
    }
  }

  #card(entry, number) {
    const r = entry.rec;
    const label = `${app.fmtClock(r.start)}${r.chapter_count > 1 ? ` · ${r.chapter_count} files` : ""}`;
    return el("div", { class: "clip-card", "data-duration": entry.duration },
      el("button", { type: "button", class: "clip-jump", "aria-label": `Go to clip ${number}, filmed at ${app.fmtClock(r.start)}`,
        onclick: () => this.handlers.jump(entry) },
      el("img", { src: mediaUrl(r.clips[0], "thumb"), alt: "", loading: "lazy" }),
      el("span", { class: "clip-number" }, String(number))),
      el("div", { class: "clip-info" },
        el("span", { class: "clip-time" }, label),
        el("span", { class: "clip-kept" })),
      el("input", { type: "checkbox", role: "switch", class: "ess-switch small clip-switch", title: "Keep this clip in the film",
        "aria-label": `Keep clip ${number} in the film`, onchange: () => this.handlers.toggle(entry) }));
  }
}
