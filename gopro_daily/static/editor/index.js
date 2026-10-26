// Entry point of the footage editor. app.js (a classic script) reaches it only through these two globals.

import { createEditor } from "./editor.js";
import { renderThumb } from "./thumbs.js";

const editor = createEditor(document.querySelector("#editor-dialog"));

window.openEditor = (group, recIndex) => editor.open(group, recIndex);
window.renderThumb = renderThumb;
