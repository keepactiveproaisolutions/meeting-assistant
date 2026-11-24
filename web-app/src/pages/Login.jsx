import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { api, auth } from "../services/api";

export default function Login() {
  const [mode, setMode] = useState("login");
  const [form, setForm] = useState({ email: "", password: "", full_name: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const redirect = location.state?.from || "/join";

  if (auth.token) return <Navigate to={redirect} replace />;

  const set = (k) => (e) => setForm({ ...form, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setError("");
    try {
      const res = mode === "login" ? await api.login(form.email, form.password) : await api.register(form);
      auth.token = res.access_token;
      navigate(redirect, { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="center">
      <form className="card" onSubmit={submit}>
        <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
        {mode === "register" && <input placeholder="Full name" value={form.full_name} onChange={set("full_name")} required />}
        <input type="email" placeholder="Email" value={form.email} onChange={set("email")} required />
        <input type="password" placeholder="Password (8+ chars)" minLength={8} value={form.password} onChange={set("password")} required />
        {error && <p className="error">{error}</p>}
        <button disabled={busy}>{mode === "login" ? "Sign in" : "Register"}</button>
        <button type="button" className="link" onClick={() => setMode(mode === "login" ? "register" : "login")}>
          {mode === "login" ? "Need an account? Register" : "Have an account? Sign in"}
        </button>
      </form>
    </main>
  );
}
