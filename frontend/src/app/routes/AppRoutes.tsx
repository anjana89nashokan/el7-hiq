import { BrowserRouter, Routes, Route } from "react-router-dom";
import Layout from "../components/Layout";
import Dashboard from "../pages/Dashboard";
import UploadIdentifier from "../pages/UploadIdentifier";
import DefineDataDictionary from "../pages/DefineDataDictionary";
import DefineMetaData from "../pages/DefineMetaData";
import Extract from "../pages/extract/Extract";

export default function AppRoutes() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="dashboard" element={<Dashboard />} />
          <Route path="upload" element={<UploadIdentifier />} />
          <Route path="extract" element={<Extract />} />
          <Route path="dictionary" element={<DefineDataDictionary />} />
          <Route path="metadata" element={<DefineMetaData />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
