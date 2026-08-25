import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { RequestTable } from "@/components/request-table"
import type { RequestRow } from "@/lib/types"

const row: RequestRow = {
  id: 42,
  timestamp: "2026-08-25T08:00:00Z",
  model: "gpt-5-mini",
  provider_id: "provider-with-a-long-identifier",
  wait_ms: 3,
  model_response_ms: 125,
  status: "success",
  input_cost: 0.00125,
  output_cost: 0.0045,
  estimated_cost: 0.00575,
}

describe("RequestTable", () => {
  it("shows the persisted input, output, and total cost breakdown", () => {
    render(<RequestTable rows={[row]} expanded={new Set()} onToggle={vi.fn()} />)

    expect(screen.getByRole("columnheader", { name: "Input cost" })).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Output cost" })).toBeInTheDocument()
    expect(screen.getByRole("columnheader", { name: "Total cost" })).toBeInTheDocument()
    expect(screen.getByText("$0.00125")).toBeInTheDocument()
    expect(screen.getByText("$0.00450")).toBeInTheDocument()
    expect(screen.getByText("$0.00575")).toBeInTheDocument()
    expect(screen.getByTitle("provider-with-a-long-identifier")).toHaveClass("whitespace-nowrap")
  })
})
