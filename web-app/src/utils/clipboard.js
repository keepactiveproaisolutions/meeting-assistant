// Copy text on any page. The async Clipboard API only exists on secure origins (HTTPS / localhost);
// phones open the app over plain http://<LAN-IP>, so fall back to a hidden textarea + execCommand('copy').
export async function copyText(text) {
  const value = String(text ?? "");
  if (typeof navigator !== "undefined" && navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      /* fall through to the legacy path */
    }
  }
  const ta = document.createElement("textarea");
  ta.value = value;
  ta.setAttribute("readonly", "");
  ta.style.position = "fixed";
  ta.style.top = "0";
  ta.style.left = "0";
  ta.style.opacity = "0";
  ta.style.fontSize = "16px"; // prevents iOS zoom on focus
  document.body.appendChild(ta);
  const selection = document.getSelection();
  const previous = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
  ta.focus();
  ta.select();
  ta.setSelectionRange(0, value.length); // iOS Safari needs an explicit range
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(ta);
  if (previous && selection) {
    selection.removeAllRanges();
    selection.addRange(previous);
  }
  return ok;
}
