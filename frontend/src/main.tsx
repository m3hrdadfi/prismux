import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BrowserRouter } from "react-router-dom"
import { Toaster } from "sonner"
import App from "@/App"
import { ThemeProvider } from "@/components/theme-provider"
import "@/index.css"

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 2, staleTime: 750, refetchOnWindowFocus: true },
    mutations: { retry: false },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <App />
          <Toaster richColors position="bottom-right" theme="system" />
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
