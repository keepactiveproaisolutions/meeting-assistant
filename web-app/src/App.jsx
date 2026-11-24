import { Navigate, Route, Routes } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import Meeting from "./pages/Meeting";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Dashboard />} />
      <Route path="/join" element={<Dashboard />} />
      <Route path="/login" element={<Navigate to="/" replace />} />
      <Route path="/meeting/:meetingId" element={<Meeting />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
