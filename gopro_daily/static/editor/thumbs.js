// Recording thumbnails in the footage list: the camera's .THM still, turning into a muted fast-forward preview of
// the recording while hovered.

import { app, mediaUrl } from "./shared.js";

const { el } = app;
const HOVER_SPEED = 8;

export function renderThumb(r, onOpen) {
  const clip = r.clips?.[0];
  if (!clip || r.error) return el("span", { class: "rec-thumb empty", "aria-hidden": "true" });
  const img = el("img", { src: mediaUrl(clip, "thumb"), alt: "", loading: "lazy", decoding: "async" });
  const thumb = onOpen
    ? el("button", { class: "rec-thumb", type: "button", title: "Hover for a quick look, click to open in the editor",
      "aria-label": `Open the recording at ${app.fmtClock(r.start)} in the editor`, onclick: onOpen }, img)
    : el("span", { class: "rec-thumb" }, img);
  let video = null;
  thumb.addEventListener("mouseenter", () => {
    if (video) return;
    video = el("video", { src: mediaUrl(clip, "video"), muted: true, playsinline: true, preload: "auto" });
    video.muted = true;
    video.addEventListener("loadedmetadata", () => {
      video.playbackRate = HOVER_SPEED;
      video.play().catch(() => {});
    }, { once: true });
    thumb.append(video);
  });
  thumb.addEventListener("mouseleave", () => { video?.remove(); video = null; });
  return thumb;
}
