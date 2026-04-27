import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
import HomePage from "./pages/HomePage.jsx";
import DemoPage from "./pages/DemoPage.jsx";
import DemoFigurePage from "./pages/DemoFigurePage.jsx";
import OfficialPage from "./pages/OfficialPage.jsx";
import VerifyPage from "./pages/VerifyPage.jsx";

const basename =
  import.meta.env.BASE_URL.replace(/\/$/, "") || undefined;

function LegacySenatorRedirect() {
  const { bioguide_id } = useParams();
  return <Navigate to={`/official/${bioguide_id}`} replace />;
}

export default function App() {
  return (
    <BrowserRouter basename={basename}>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="/demo" element={<DemoPage />} />
        <Route path="/demo/figure/:figureId" element={<DemoFigurePage />} />
        <Route path="/official/:id" element={<OfficialPage />} />
        <Route path="/senator/:bioguide_id" element={<LegacySenatorRedirect />} />
        <Route path="/verify/:dossier_id" element={<VerifyPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
