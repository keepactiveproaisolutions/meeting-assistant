import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../services/api";

export default function Dashboard() {
  const [params] = useSearchParams();
  const [joinCode, setJoinCode] = useState((params.get("code") || "").toUpperCase());
  const [meetingTitle, setMeetingTitle] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const handleJoin = async (codeToJoin) => {
    const cleanCode = (codeToJoin || joinCode).trim();
    if (!cleanCode) return;
    setBusy(true);
    setError("");
    try {
      const meeting = await api.joinMeeting(cleanCode);
      navigate(`/meeting/${meeting.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  const handleCreate = async () => {
    setBusy(true);
    setError("");
    try {
      const meeting = await api.createMeeting(meetingTitle.trim() || "Live Meeting");
      navigate(`/meeting/${meeting.id}`);
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  // If a meeting code is provided in URL (?code=XYZ), join directly
  useEffect(() => {
    const urlCode = params.get("code");
    if (urlCode) {
      handleJoin(urlCode);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="dev-container">
      <main className="dashboard-content">
        <header className="dashboard-header">
          <h1>Real-Time AI Meeting Assistant</h1>
          <p className="muted">Direct Meeting Dashboard</p>
        </header>

        {error && (
          <div className="error-box" role="alert">
            <div style={{ flex: 1 }}>
              <strong>Connection / Join Notice:</strong> {error}
            </div>
            <button
              type="button"
              className="small btn-retry"
              onClick={() => (joinCode.trim() ? handleJoin(joinCode) : handleCreate())}
              disabled={busy}
            >
              🔄 Retry
            </button>
          </div>
        )}

        <div className="dashboard-grid">
          {/* Option 1: Create Meeting */}
          <div className="card">
            <h2>Create Meeting</h2>
            <p className="muted">Start a new meeting session in the development workspace.</p>
            <input
              type="text"
              placeholder="Meeting title (optional)"
              value={meetingTitle}
              onChange={(e) => setMeetingTitle(e.target.value)}
              disabled={busy}
            />
            <button
              onClick={handleCreate}
              disabled={busy}
              className="primary-btn"
            >
              {busy ? "Starting..." : "Create Meeting"}
            </button>
          </div>

          {/* Option 2: Join Meeting */}
          <div className="card">
            <h2>Join Meeting</h2>
            <p className="muted">Enter a meeting code or scan a QR code to join an active session.</p>
            <form onSubmit={(e) => { e.preventDefault(); handleJoin(); }}>
              <input
                className="code-input"
                type="text"
                maxLength={12}
                placeholder="e.g. ABCD2345"
                value={joinCode}
                onChange={(e) => setJoinCode(e.target.value.toUpperCase())}
                disabled={busy}
              />
              <button
                type="submit"
                disabled={busy || joinCode.length < 4}
                className="primary-btn"
              >
                {busy ? "Joining..." : "Join Meeting"}
              </button>
            </form>
          </div>
        </div>
      </main>
    </div>
  );
}
