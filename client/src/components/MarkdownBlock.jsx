import ReactMarkdown from "react-markdown";

const components = {
  a({ href, children, ...rest }) {
    return (
      <a href={href} target="_blank" rel="noopener noreferrer" {...rest}>
        {children}
      </a>
    );
  },
};

/**
 * Renders Markdown to HTML. Safe by default (no raw HTML). External links open in a new tab.
 */
export default function MarkdownBlock({ children, className = "", style }) {
  const raw = children == null ? "" : String(children);
  if (!raw.trim()) return null;
  return (
    <div className={`oc-markdown ${className}`.trim()} style={style}>
      <ReactMarkdown components={components}>{raw}</ReactMarkdown>
    </div>
  );
}
