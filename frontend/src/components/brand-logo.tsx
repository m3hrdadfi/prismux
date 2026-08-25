import { cn } from "@/lib/utils"

export function BrandLogo({ className }: { className?: string }) {
  return <span className={cn("brand-logo", className)} aria-hidden="true">
    <img className="brand-logo-light" src="/brand/prismux-light.png" alt="" />
    <img className="brand-logo-dark" src="/brand/prismux-dark.png" alt="" />
  </span>
}
