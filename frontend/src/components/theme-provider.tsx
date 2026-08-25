import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

export type Theme = "dark" | "light"
const STORAGE_KEY = "prismux-theme"
const LEGACY_STORAGE_KEY = "rate-limit-proxy-theme"

interface ThemeContextValue { theme: Theme; setTheme: (theme: Theme) => void }
const ThemeContext = createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    const saved = localStorage.getItem(STORAGE_KEY) ?? localStorage.getItem(LEGACY_STORAGE_KEY)
    if (saved === "dark" || saved === "light") return saved
    const resolved = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
    localStorage.setItem(STORAGE_KEY, resolved)
    return resolved
  })

  useEffect(() => {
    const root = document.documentElement
    const dark = theme === "dark"
    root.classList.toggle("dark", dark)
    root.style.colorScheme = dark ? "dark" : "light"
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", dark ? "#10151D" : "#F7F7F8")
    localStorage.setItem(STORAGE_KEY, theme)
  }, [theme])

  const value = useMemo(() => ({ theme, setTheme }), [theme])
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) throw new Error("useTheme must be used inside ThemeProvider")
  return context
}
