import { createRoot } from "react-dom/client";
import App from "./App";
import "mana-font/css/mana.min.css";
import "./styles.css";
import "./table-layout.css";
import "./table-card-layout.css";
import "./table-attachments.css";
import "./choice-surface.css";

createRoot(document.getElementById("root")!).render(<App />);
