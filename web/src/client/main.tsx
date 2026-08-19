import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

const root = document.getElementById("root");
if (root === null) {
  throw new Error("index.html carries no #root element");
}

createRoot(root).render(
  <StrictMode>
    <h1>Context dashboard</h1>
  </StrictMode>,
);
