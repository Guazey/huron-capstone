import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import { chromePlatform } from "./chromePlatform";
import { setPlatform } from "./platform";
import "./styles.css";

setPlatform(chromePlatform);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
