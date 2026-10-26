// The editor is a set of ES modules; the rest of the UI (app.js) is a classic script. Its helpers are reachable as
// globals, and this is the single place the editor touches them: if app.js becomes a module, only this file changes.
// `config` and `OPTIONS` are reassigned by app.js, so they are read through functions, never copied.

export const app = {
  $, el, api, toast, fmtDuration, fmtClock, fmtGap, fmtDayHeader,
  config: () => config,
  options: () => OPTIONS,
};

export const mediaUrl = (clip, kind) => `/api/media/${clip.id}/${kind}`;

/** 75.4 -> "1:15", 3725 -> "1:02:05". With `precise`, tenths are added: "1:15.4". */
export function fmtPos(seconds, precise = false) {
  const total = Math.max(0, seconds);
  const whole = Math.floor(precise ? total : Math.round(total));
  const h = Math.floor(whole / 3600), m = Math.floor((whole % 3600) / 60), s = whole % 60;
  const mm = String(m).padStart(h ? 2 : 1, "0"), ss = String(s).padStart(2, "0");
  const tenths = precise ? `.${Math.floor((total - whole) * 10)}` : "";
  return h ? `${h}:${mm}:${ss}${tenths}` : `${mm}:${ss}${tenths}`;
}

/** Time of day a moment of a recording was filmed ("14:03:27"), or "" when the start time is unknown. */
export function wallClock(rec, secondsIntoRecording) {
  if (!rec.start) return "";
  const d = new Date(Date.parse(rec.start) + secondsIntoRecording * 1000);
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map(n => String(n).padStart(2, "0")).join(":");
}
