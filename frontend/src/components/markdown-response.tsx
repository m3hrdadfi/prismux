import { IconCheck, IconCopy } from "@tabler/icons-react"
import { Highlight, themes, type Language } from "prism-react-renderer"
import { Children, isValidElement, useEffect, useRef, useState, type ReactNode } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import { useTheme } from "@/components/theme-provider"
import { copyText } from "@/lib/clipboard"

const HIGHLIGHTED_LANGUAGES = new Set([
  "bash", "c", "cpp", "css", "go", "graphql", "java", "javascript", "json", "jsx",
  "kotlin", "markup", "python", "ruby", "rust", "sql", "swift", "tsx", "typescript", "yaml",
])

function useCopyFeedback(value: string) {
  const [copied, setCopied] = useState(false)
  const resetTimer = useRef<number | null>(null)
  useEffect(() => () => { if (resetTimer.current !== null) window.clearTimeout(resetTimer.current) }, [])
  async function copy() {
    try {
      await copyText(value)
      setCopied(true)
      if (resetTimer.current !== null) window.clearTimeout(resetTimer.current)
      resetTimer.current = window.setTimeout(() => setCopied(false), 1600)
    } catch {
      setCopied(false)
    }
  }
  return { copied, copy }
}

function CopyButton({ value, label = "Copy" }: { value: string; label?: string }) {
  const { copied, copy } = useCopyFeedback(value)
  return <Button type="button" size="sm" variant="ghost" className="code-copy" onClick={copy} aria-label={copied ? "Copied" : label}>
    {copied ? <IconCheck size={14} /> : <IconCopy size={14} />}{copied ? "Copied" : label}
  </Button>
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const { theme } = useTheme()
  const highlightedLanguage = (HIGHLIGHTED_LANGUAGES.has(language) ? language : "plain") as Language
  const showLineNumbers = code.split("\n").length > 4
  return <div className="code-block">
    <div className="code-block-head"><span>{language || "text"}</span><CopyButton value={code} label="Copy code" /></div>
    <Highlight theme={theme === "dark" ? themes.vsDark : themes.github} code={code} language={highlightedLanguage}>
      {({ className, style, tokens, getLineProps, getTokenProps }) => <pre className={`${className} code-highlight`} style={style} aria-label={`${language || "text"} code`}>
        {tokens.map((line, index) => {
          const lineProps = getLineProps({ line })
          return <div {...lineProps} className={`${lineProps.className || ""} code-line`} key={index}>
            {showLineNumbers && <span className="code-line-number" aria-hidden="true">{index + 1}</span>}
            <span>{line.map((token, tokenIndex) => <span {...getTokenProps({ token })} key={tokenIndex} />)}</span>
          </div>
        })}
      </pre>}
    </Highlight>
  </div>
}

function MarkdownPre({ children }: { children?: ReactNode }) {
  const child = Children.count(children) === 1 ? Children.only(children) : null
  if (!isValidElement<{ children?: ReactNode; className?: string }>(child)) return <pre>{children}</pre>
  const code = String(child.props.children || "").replace(/\n$/, "")
  const language = child.props.className?.match(/language-([^\s]+)/)?.[1] || ""
  return <CodeBlock code={code} language={language} />
}

export function MarkdownResponse({
  children,
  title = "Assistant response",
  subtitle = "Rendered Markdown",
  copyLabel = "Copy response",
}: {
  children: string
  title?: string
  subtitle?: string
  copyLabel?: string
}) {
  return <article className="markdown-response">
    <header className="markdown-response-head"><div><strong>{title}</strong><span>{subtitle}</span></div><CopyButton value={children} label={copyLabel} /></header>
    <div className="markdown-response-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node: _node, ...props }) => <h4 {...props} />,
          h2: ({ node: _node, ...props }) => <h4 {...props} />,
          h3: ({ node: _node, ...props }) => <h5 {...props} />,
          h4: ({ node: _node, ...props }) => <h5 {...props} />,
          h5: ({ node: _node, ...props }) => <h6 {...props} />,
          h6: ({ node: _node, ...props }) => <h6 {...props} />,
          pre: ({ node: _node, ...props }) => <MarkdownPre {...props} />,
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  </article>
}
