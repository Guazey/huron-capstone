import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "../../extension/src/App";
import { setPlatform } from "../../extension/src/platform";
import "../../extension/src/styles.css";
import { tauriPlatform } from "./tauriPlatform";

setPlatform(tauriPlatform);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
