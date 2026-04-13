import { Link } from "react-router-dom";

export default function BottomBar() {
  return (
    <nav className="oc-bottom-bar" aria-label="Quick actions">
      <Link to="/#directory">FIND SOURCES →</Link>
      <Link to="/#receipt">SHARE RECEIPT ↑</Link>
    </nav>
  );
}
