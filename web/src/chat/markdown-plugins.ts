import { defaultRemarkPlugins } from "streamdown";
import { remarkStripOutsideMarkers } from "./display-text";

// Passing remarkPlugins replaces Streamdown's defaults, so carry GFM back in.
export const markdownRemarkPlugins = [
  ...Object.values(defaultRemarkPlugins),
  remarkStripOutsideMarkers,
];
