import type { ReactNode } from "react"
export function PageHeading({ title, description, actions }: { title: string; description: string; actions?: ReactNode }) {
  return <div className="page-heading"><div><h2>{title}</h2><p>{description}</p></div>{actions && <div className="page-heading-actions">{actions}</div>}</div>
}
