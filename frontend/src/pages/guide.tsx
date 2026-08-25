import { IconBook2, IconCheck, IconCopy, IconKey, IconRobot, IconRoute, IconServer, IconTerminal2 } from "@tabler/icons-react"
import { useQuery } from "@tanstack/react-query"
import { useState, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { toast } from "sonner"
import { api } from "@/lib/api"
import { copyText } from "@/lib/clipboard"
import { PageHeading } from "@/components/page-heading"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"

function CodeBlock({ label, children }: { label: string; children: string }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await copyText(children)
      setCopied(true)
      toast.success(`${label} copied`)
      window.setTimeout(() => setCopied(false), 1800)
    } catch {
      toast.error("Copy failed. Select the command and copy it manually.")
    }
  }
  return <div className="guide-code">
    <div className="guide-code-head"><span>{label}</span><Button type="button" size="sm" variant="ghost" onClick={copy} aria-label={`Copy ${label}`}>{copied ? <IconCheck size={14} /> : <IconCopy size={14} />}{copied ? "Copied" : "Copy"}</Button></div>
    <pre><code>{children}</code></pre>
  </div>
}

function GuideCard({ icon, title, description, children }: { icon: ReactNode; title: string; description: string; children: ReactNode }) {
  return <section className="section guide-card">
    <div className="guide-card-title"><span>{icon}</span><div><h3>{title}</h3><p>{description}</p></div></div>
    {children}
  </section>
}

export function GuidePage() {
  const providers = useQuery({ queryKey: ["providers"], queryFn: api.providers })
  const origin = window.location.origin
  const enabledProviders = providers.data?.providers.filter((provider) => provider.enabled) || []
  const exampleProvider = enabledProviders[0]?.id || "nvidia"
  const exampleModel = enabledProviders[0]?.default_model || enabledProviders[0]?.models[0] || "your-model-id"
  const routedPython = `from openai import OpenAI

client = OpenAI(
    base_url="${origin}/v1",
    api_key="prismux_live_your_key",
)

response = client.chat.completions.create(
    model="${exampleProvider}::${exampleModel}",
    messages=[{"role": "user", "content": "Hello"}],
)

print(response.choices[0].message.content)`
  const directPython = `from openai import OpenAI

client = OpenAI(
    base_url="${origin}/${exampleProvider}/v1",
    api_key="prismux_live_your_key",
)

response = client.chat.completions.create(
    model="${exampleModel}",
    messages=[{"role": "user", "content": "Hello"}],
)

print(response.choices[0].message.content)`
  const streamingCurl = `curl -N ${origin}/${exampleProvider}/v1/chat/completions \\
  -H 'Authorization: Bearer prismux_live_your_key' \\
  -H 'Content-Type: application/json' \\
  -d '{
    "model": "${exampleModel}",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": true
  }'`
  const hermesRoutedConfig = `# ~/.hermes/config.yaml
model:
  provider: custom
  default: "${exampleProvider}::${exampleModel}"
  base_url: "${origin}/v1"
  api_key: "prismux_live_your_key"`
  const hermesDirectConfig = `# ~/.hermes/config.yaml
model:
  provider: custom
  default: "${exampleModel}"
  base_url: "${origin}/${exampleProvider}/v1"
  api_key: "prismux_live_your_key"`

  return <div className="page guide-page">
    <PageHeading title="Guide" description="Connect any OpenAI-compatible client to PRISMUX, use routing when you want fallbacks, or address one provider directly." actions={<Badge variant="outline"><IconBook2 size={14} />Operator quickstart</Badge>} />

    <section className="guide-hero">
      <div><span className="guide-eyebrow">Connection model</span><h3>One gateway, two ways to call it.</h3><p>Use the routed endpoint for aliases, qualified models, and defaults. Use a provider endpoint when the request must go to one specific upstream.</p></div>
      <div className="guide-endpoints"><div><span>Routed</span><code>{origin}/v1</code></div><div><span>Direct</span><code>{origin}/&#123;provider_id&#125;/v1</code></div></div>
    </section>

    <div className="guide-steps">
      <GuideCard icon={<IconKey size={18} />} title="1. Create a machine key" description="Client applications authenticate with a revocable PRISMUX key, never with a provider credential.">
        <p>Open <Link to="/settings/access">Access → Machine API keys</Link>, create a key, and copy it immediately. The complete secret is shown once.</p>
        <div className="guide-inline-code"><code>prismux_live_…</code><span>Authorization: Bearer</span></div>
      </GuideCard>
      <GuideCard icon={<IconServer size={18} />} title="2. Configure providers" description="PRISMUX stores upstream URLs, encrypted credentials, models, limits, and health independently.">
        <p>Add or verify providers under <Link to="/settings/providers">Settings → Providers</Link>, then refresh their model catalogs.</p>
        <div className="guide-provider-list">{providers.isPending ? <span>Loading providers…</span> : enabledProviders.length ? enabledProviders.map((provider) => <Badge key={provider.id} variant={provider.health === "healthy" ? "success" : "secondary"}>{provider.name}<small>{provider.id}</small></Badge>) : <span>No enabled providers are configured.</span>}</div>
      </GuideCard>
      <GuideCard icon={<IconRoute size={18} />} title="3. Choose a request path" description="The base URL determines whether PRISMUX routes the request or pins it to one provider.">
        <ul className="guide-rules"><li><code>/v1</code><span>Aliases can fall back. Qualified IDs use <strong>provider_id::model_id</strong>. Raw IDs use the default provider.</span></li><li><code>/provider_id/v1</code><span>Always calls that enabled provider. Send its raw upstream model ID.</span></li></ul>
      </GuideCard>
    </div>

    <div className="guide-examples">
      <section>
        <div className="guide-section-title"><IconRoute size={18} /><div><h3>Routed client</h3><p>Best for applications that should use aliases, defaults, or fallback chains.</p></div></div>
        <CodeBlock label="Python · routed endpoint">{routedPython}</CodeBlock>
      </section>
      <section>
        <div className="guide-section-title"><IconServer size={18} /><div><h3>Direct provider</h3><p>Best when the calling application must select the exact upstream.</p></div></div>
        <CodeBlock label={`Python · ${exampleProvider}`}>{directPython}</CodeBlock>
      </section>
    </div>

    <section className="section guide-integration">
      <div className="section-header"><div><h3>Connect Hermes Agent</h3><p>Configure PRISMUX as Hermes Agent’s custom OpenAI-compatible endpoint.</p></div><IconRobot size={18} className="text-muted-foreground" /></div>
      <div className="guide-integration-intro">
        <div>
          <span className="guide-eyebrow">Interactive setup</span>
          <ol>
            <li>Create a machine key under <Link to="/settings/access">Access</Link>.</li>
            <li>Run <code>hermes model</code> and select <strong>Custom endpoint (self-hosted / VLLM / etc.)</strong>.</li>
            <li>Enter the routed or direct base URL below, the PRISMUX machine key, and the matching model selector.</li>
          </ol>
        </div>
        <aside><strong>Running Hermes in Docker?</strong><p><code>localhost</code> refers to its own container. Use a shared-network hostname such as <code>http://proxy:8100/v1</code>, or <code>host.docker.internal:8100</code> when PRISMUX is published on the host.</p></aside>
      </div>
      <div className="guide-examples guide-integration-examples">
        <section>
          <div className="guide-section-title"><IconRoute size={18} /><div><h3>Hermes through routing</h3><p>Use aliases, qualified models, the default provider, and configured fallback chains.</p></div></div>
          <CodeBlock label="Hermes config · routed">{hermesRoutedConfig}</CodeBlock>
        </section>
        <section>
          <div className="guide-section-title"><IconServer size={18} /><div><h3>Hermes pinned to a provider</h3><p>Bypass routing and send every Hermes request to one selected provider.</p></div></div>
          <CodeBlock label={`Hermes config · ${exampleProvider}`}>{hermesDirectConfig}</CodeBlock>
        </section>
      </div>
      <p className="guide-integration-note">Hermes Agent now prefers <code>config.yaml</code> for custom endpoints. Replace the displayed browser origin if Hermes reaches PRISMUX through a different hostname or TLS domain.</p>
    </section>

    <section className="section guide-reference">
      <div className="section-header"><div><h3>Endpoint reference</h3><p>OpenAI-compatible paths exposed by this installation.</p></div><IconTerminal2 size={18} className="text-muted-foreground" /></div>
      <div className="guide-reference-grid">
        <div><code>POST /v1/chat/completions</code><p>Route aliases, qualified models, or the default provider.</p></div>
        <div><code>POST /&#123;provider_id&#125;/v1/chat/completions</code><p>Send a request directly to one provider.</p></div>
        <div><code>GET /v1/models</code><p>List route aliases and provider-qualified cached models.</p></div>
        <div><code>GET /&#123;provider_id&#125;/v1/models</code><p>List the cached raw model IDs for one provider.</p></div>
      </div>
      <CodeBlock label="cURL · streaming response">{streamingCurl}</CodeBlock>
    </section>
  </div>
}
