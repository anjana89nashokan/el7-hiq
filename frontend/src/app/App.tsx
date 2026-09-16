import { Navigate, Route, Routes } from "react-router-dom";
import { Provider } from "react-redux";
import { store } from "./state/store";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import UploadIdentifier from "./pages/UploadIdentifier";
import ProfilingResult from "./pages/ProfilingResult";
import DefineDataDictionary from "./pages/DefineDataDictionary";
import DefineMetaData from "./pages/DefineMetaData";
import Sessions from "./pages/Sessions";
import Mapping from "./pages/Mapping";
import { ChatProvider } from "./components/ChatContext";
import Extract from "./pages/extract/Extract";
import Documentation from "./pages/Documentation";
import Settings from "./pages/Settings";
import HL7Results from "./pages/HL7Results";
import HL7MappingReview from "./pages/HL7MappingReview";
import MappingsLibrary from "./pages/MappingsLibrary";
import StreamingProfilingResult from "./pages/StreamingProfilingResult";
import { sttmNav } from "./utils/sttmRoutes";


export default function SttmApp() {
  return (
    <Provider store={store}>
      <ChatProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="upload" element={<UploadIdentifier />} />
            <Route path="extract" element={<Extract />} />
            <Route path="profiling" element={<ProfilingResult />} />
            <Route path="dictionary" element={<DefineDataDictionary />} />
            <Route path="metadata" element={<DefineMetaData />} />
            <Route path="sessions" element={<Sessions />} />
            <Route path="mappings-library" element={<MappingsLibrary />} />
            <Route path="mapping" element={<Mapping />} />
            <Route path="documentation" element={<Documentation />} />
            <Route path="settings" element={<Settings />} />
            <Route path="hl7" element={<HL7Results />} />
            <Route path="hl7/:hl7SessionId" element={<HL7Results />} />
            <Route path="hl7/:hl7SessionId/review" element={<HL7MappingReview />} />
            <Route path="streaming-profiling" element={<StreamingProfilingResult />} />
            <Route path="*" element={<Navigate to={sttmNav("/dashboard")} replace />} />
          </Route>
        </Routes>
      </ChatProvider>
    </Provider>
  );
}
