import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./index.css";
import { initializeTheme } from "@/hooks/useTheme";
import DesktopBootstrap from "@/components/DesktopBootstrap";
import DesktopTitleBar from "@/components/Layout/DesktopTitleBar";

initializeTheme();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <DesktopTitleBar />
      <DesktopBootstrap>
        <App />
      </DesktopBootstrap>
    </BrowserRouter>
  </React.StrictMode>
);
