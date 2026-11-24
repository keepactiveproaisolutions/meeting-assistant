import { useEffect, useState } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { api, auth } from "../services/api";

/** Join with a typed code, or via the QR code (which opens /join?code=XXXXXXXX). */
export default function Join() {
  const [params] = useSearchParams();
  const [code, setCode] = useState((params.get("code") || "").toUpperCase());
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();

  const join = async (c) => {
    setBusy(true); setError("");
    try {
      const meeting = await api.joinMeeting(c.trim());
      navigate(`/meeting/${meeting.id}`, { replace: true });
    } catch (err) {
      setError(err.message);
      setBusy(false);
    }
  };

  // Auto-join when arriving from a QR code / link.
  useEffect(() => {
    if (auth.token && params.get("code")) join(params.get("code"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!auth.token) {
    return <Navigate to="/login" replace state={{ from: `/join${params.get("code") ? `?code=${params.get("code")}` : ""}` }} />;
  }

  return (
    <main className="center">
      <form className="card" onSubmit={(e) => { e.preventDefault(); join(code); }}>
        <h1>Join a meeting</h1>
        <p className="muted">Enter the code shown in the desktop app, or scan its QR code.</p>
        <input className="code" value={code} maxLength={12} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCD2345" autoFocus />
        {error && <p className="error">{error}</p>}
        <button disabled={busy || code.length < 4}>{busy ? "Joining…" : "Join"}</button>
        <button type="button" className="link" onClick={() => { auth.logout(); navigate("/login"); }}>Sign out</button>
      </form>
    </main>
  );
}
