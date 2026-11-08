import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@radix-ui/themes";
import "@radix-ui/themes/styles.css";
import { App } from "./App";
import "./styles.css";
import "./aui.css";
import "./shell.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Theme appearance="dark" accentColor="lime" grayColor="olive" radius="large" hasBackground={false}>
      <App />
    </Theme>
  </StrictMode>,
);
