import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import IssuePage from "./pages/IssuePage";
import "./styles/globals.css";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/issues/:owner/:repo/:number" element={<IssuePage />} />
      </Routes>
    </BrowserRouter>
  );
}
