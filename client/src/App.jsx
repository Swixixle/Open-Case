import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import HomePage from "./pages/HomePage.jsx";
import SenatorPage from "./pages/SenatorPage.jsx";
import VerifyPage from "./pages/VerifyPage.jsx";

const basename =
  import.meta.env.BASE_URL.replace(/\/$/, "") || undefined;

export default function App() {
  return (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/senator/:bioguide_id" element={<SenatorPage />} />
        <Route path="/verify/:dossier_id" element={<VerifyPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
