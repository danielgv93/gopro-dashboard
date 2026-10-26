// Inline SVG icons for the editor (24×24, drawn with currentColor so they follow the theme and button states).
// Unicode symbols render differently in every font and several have no obvious meaning; these are drawn for the job.

const PATHS = {
  play: '<path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none"/>',
  pause: '<rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none"/>',
  "prev-clip": '<path d="M18 6 9 12l9 6z" fill="currentColor"/><path d="M6 5v14"/>',
  "next-clip": '<path d="m6 6 9 6-9 6z" fill="currentColor"/><path d="M18 5v14"/>',
  split: '<circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M20 4 8.12 15.88M14.47 14.48 20 20M8.12 8.12 12 12"/>',
  "mark-in": '<path d="M9 4H5v16h4"/><path d="M10 12h10M16 8l4 4-4 4"/>',
  "mark-out": '<path d="M15 4h4v16h-4"/><path d="M14 12H4M8 8l-4 4 4 4"/>',
  remove: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6M10 11v6M14 11v6"/>',
  restore: '<rect x="3" y="5" width="18" height="14" rx="2" stroke-dasharray="3 2.5"/><path d="M12 9v6M9 12h6"/>',
  undo: '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
  redo: '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
  "zoom-in": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6M11 8v6"/>',
  "zoom-out": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5M8 11h6"/>',
  "zoom-fit": '<path d="M3 5v14M21 5v14M7 12h10M10 9l-3 3 3 3M14 9l3 3-3 3"/>',
  sound: '<path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor"/><path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13"/>',
  muted: '<path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor"/><path d="m16 9 5 6M21 9l-5 6"/>',
};

/** An <svg> element for an icon name (see PATHS). Decorative: the button carrying it provides the accessible name. */
export function icon(name) {
  const template = document.createElement("template");
  template.innerHTML = `<svg class="ed-svg" viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${PATHS[name] || ""}</svg>`;
  return template.content.firstElementChild;
}
