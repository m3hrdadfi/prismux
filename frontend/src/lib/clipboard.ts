export async function copyText(value: string): Promise<void> {
  if (!value) throw new Error("There is nothing to copy")

  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(value)
      return
    } catch {
      // Plain-HTTP deployments and restrictive browser policies can reject the
      // modern Clipboard API. Fall through to the selection-based copy path.
    }
  }

  const textarea = document.createElement("textarea")
  textarea.value = value
  textarea.readOnly = true
  textarea.setAttribute("aria-hidden", "true")
  textarea.style.position = "fixed"
  textarea.style.inset = "0 auto auto -10000px"
  textarea.style.opacity = "0"
  document.body.appendChild(textarea)
  textarea.focus()
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)

  let clipboardEventHandled = false
  const handleCopy = (event: ClipboardEvent) => {
    if (!event.clipboardData) return
    event.clipboardData.clearData()
    event.clipboardData.setData("text/plain", value)
    event.preventDefault()
    clipboardEventHandled = true
  }
  document.addEventListener("copy", handleCopy, true)
  try {
    const commandAccepted = document.execCommand("copy")
    if (!commandAccepted || !clipboardEventHandled) throw new Error("The browser rejected the copy command")
  } finally {
    document.removeEventListener("copy", handleCopy, true)
    textarea.remove()
  }
}
