/**
 * pdfjs uses a Web Worker to parse PDFs off the main thread. Vite
 * needs to know about that worker file at build time so it can be
 * fingerprinted and served from /assets/. The `?url` import gives us
 * the resolved URL string; pdfjs takes it via GlobalWorkerOptions.
 *
 * Imported lazily by the reader page only — the main bundle stays
 * unaffected by the ~250 KB pdfjs payload until the user navigates
 * to /reader/:id.
 */

import * as pdfjs from "pdfjs-dist";
import workerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";

pdfjs.GlobalWorkerOptions.workerSrc = workerSrc;

export { pdfjs };
