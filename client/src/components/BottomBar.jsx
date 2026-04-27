import { Link } from "react-router-dom";

export default function BottomBar({ variant = "home", onShareReceipt }) {
  if (variant === "senator" || variant === "official") {
    return (
      <nav className="oc-bottom-bar" aria-label="Quick actions">
        <Link to="/">← BACK TO DIRECTORY</Link>
        <button
          type="button"
          className="oc-bottom-link-btn"
          onClick={() => {
            if (onShareReceipt) onShareReceipt();
            else {
              document.getElementById("receipt")?.scrollIntoView({
                behavior: "smooth",
              });
            }
          }}
        >
          SHARE RECEIPT ↑
        </button>
      </nav>
    );
  }

  return (
    <nav className="oc-bottom-bar" aria-label="Quick actions">
      <Link to="/demo">PUBLIC DEMO</Link>
      <Link to="/#directory">FIND SOURCES →</Link>
      <Link to="/#receipt">SHARE RECEIPT ↑</Link>
    </nav>
  );
}
