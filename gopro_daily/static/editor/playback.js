// Plays a film's source (every chapter file back to back) in one <video>, addressed by source position.
// It knows nothing about cuts: the editor listens to "tick" and decides what to skip. Chapter files are the camera's
// low-res .LRV proxies when present (see /api/media), so switching files is cheap.

import { clipList, locate } from "./film.js";
import { mediaUrl } from "./shared.js";

export class Playback extends EventTarget {
  constructor(video) {
    super();
    this.video = video;
    this.clips = [];
    this.index = -1;
    this.rate = 1;
    this.pending = null;  // { time, autoplay } applied once the new file's metadata is loaded
    this.frame = 0;

    video.addEventListener("loadedmetadata", () => {
      video.playbackRate = this.rate;
      const pending = this.pending;
      this.pending = null;
      if (pending?.time) video.currentTime = Math.min(pending.time, Math.max(0, video.duration - 0.05));
      if (pending?.autoplay) this.#play();
      this.#emit();
    });
    video.addEventListener("play", () => { this.#loop(); this.#emit(); });
    video.addEventListener("pause", () => { cancelAnimationFrame(this.frame); this.#emit(); });
    video.addEventListener("seeked", () => this.#emit());
    video.addEventListener("ended", () => {
      if (this.index < this.clips.length - 1) this.#load(this.index + 1, 0, true);
      else this.dispatchEvent(new Event("end"));
    });
  }

  /** Starts a new film; nothing plays until `seek`. */
  setSource(source) {
    this.source = source;
    this.clips = clipList(source);
    this.index = -1;
  }

  get paused() { return this.video.paused; }

  /** Current source position in seconds. */
  get position() {
    const clip = this.clips[this.index];
    if (!clip) return 0;
    return clip.offset + Math.min(this.video.currentTime || 0, clip.clip.duration);
  }

  /** Where the playhead is: recording, chapter file and times (see film.locate). */
  get location() { return locate(this.source, this.position); }

  seek(position, autoplay = !this.video.paused) {
    if (!this.clips.length) return;
    const { entry, clipIndex, clipTime } = locate(this.source, position);
    const index = this.clips.findIndex(c => c.entry === entry && c.clip === entry.clips[clipIndex].clip);
    this.#load(index, clipTime, autoplay);
  }

  play() { this.#play(); }
  pause() { this.video.pause(); }
  toggle() { if (this.video.paused) this.#play(); else this.video.pause(); }

  setRate(rate) {
    this.rate = rate;
    this.video.defaultPlaybackRate = rate;
    this.video.playbackRate = rate;
  }

  /** Releases the file so the browser stops buffering it. */
  unload() {
    cancelAnimationFrame(this.frame);
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    this.index = -1;
  }

  #load(index, time, autoplay) {
    const clip = this.clips[index];
    if (!clip) return;
    if (index !== this.index) {
      this.index = index;
      this.pending = { time, autoplay };
      this.video.src = mediaUrl(clip.clip, "video");
      this.video.load();
    } else {
      this.video.currentTime = time;
      if (autoplay) this.#play();
    }
    this.#emit();
  }

  #play() { this.video.play().catch(() => {}); }

  // timeupdate fires only ~4 times a second: too coarse for a playhead or for skipping removed parts at 16x.
  #loop() {
    cancelAnimationFrame(this.frame);
    const step = () => {
      this.#emit();
      if (!this.video.paused) this.frame = requestAnimationFrame(step);
    };
    this.frame = requestAnimationFrame(step);
  }

  #emit() { this.dispatchEvent(new Event("tick")); }
}
