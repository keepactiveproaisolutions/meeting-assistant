import React from "react";

export class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null, info: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, info) {
    console.error("UI Error Caught by ErrorBoundary:", error, info);
    this.setState({ info });
  }

  render() {
    if (this.state.hasError) {
      return (
        <div style={{ padding: 40, fontFamily: "sans-serif", maxWidth: 600, margin: "40px auto", background: "#fff", borderRadius: 8, boxShadow: "0 2px 8px rgba(0,0,0,0.1)" }}>
          <h2 style={{ color: "#dc2626", marginTop: 0 }}>Something went wrong in the application</h2>
          <p style={{ color: "#475569" }}>{this.state.error?.toString()}</p>
          <pre style={{ background: "#f1f5f9", padding: 12, borderRadius: 6, fontSize: 12, overflow: "auto", maxHeight: 200 }}>
            {this.state.info?.componentStack || this.state.error?.stack}
          </pre>
          <button
            onClick={() => { window.location.href = "/"; }}
            style={{ background: "#2563eb", color: "#fff", border: "none", padding: "8px 16px", borderRadius: 6, cursor: "pointer", fontWeight: 600 }}
          >
            Return to Dashboard
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
