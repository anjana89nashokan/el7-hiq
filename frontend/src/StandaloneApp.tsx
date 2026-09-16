import { Navigate, Route, Routes } from "react-router-dom";

import SttmApp from "./app/App";

export default function StandaloneApp() {
  return (
    <div className="min-h-screen w-full bg-brand-light">
      <Routes>
        <Route path="/login" element={<Navigate to="/dashboard" replace />} />
        <Route path="/*" element={<SttmApp />} />
      </Routes>
    </div>
  );
}
