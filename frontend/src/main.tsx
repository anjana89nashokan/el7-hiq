import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import StandaloneApp from "./StandaloneApp";

import "easymde/dist/easymde.min.css";
import "./app/index.css";
import "./app/App.css";
import "./index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <StandaloneApp />
    </BrowserRouter>
  </StrictMode>,
);
