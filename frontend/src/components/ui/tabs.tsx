import * as TabsPrimitive from "@radix-ui/react-tabs"
import { cn } from "@/lib/utils"
export const Tabs = TabsPrimitive.Root
export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) { return <TabsPrimitive.List className={cn("inline-flex h-9 items-center rounded-[8px] bg-muted p-1", className)} {...props} /> }
export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) { return <TabsPrimitive.Trigger className={cn("rounded-md px-3 py-1 text-sm text-muted-foreground transition-colors data-[state=active]:bg-background data-[state=active]:text-foreground data-[state=active]:shadow-xs", className)} {...props} /> }
export const TabsContent = TabsPrimitive.Content
