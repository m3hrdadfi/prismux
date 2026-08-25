import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu"
import { cn } from "@/lib/utils"

export const DropdownMenu = DropdownMenuPrimitive.Root
export const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger
export function DropdownMenuContent({ className, sideOffset = 6, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Content>) {
  return <DropdownMenuPrimitive.Portal><DropdownMenuPrimitive.Content sideOffset={sideOffset} className={cn("z-50 min-w-44 rounded-[8px] border border-border bg-popover p-1 text-popover-foreground shadow-lg", className)} {...props} /></DropdownMenuPrimitive.Portal>
}
export function DropdownMenuItem({ className, ...props }: React.ComponentProps<typeof DropdownMenuPrimitive.Item>) {
  return <DropdownMenuPrimitive.Item className={cn("flex cursor-default select-none items-center gap-2 rounded-md px-2.5 py-2 text-sm outline-none focus:bg-muted data-[disabled]:opacity-50", className)} {...props} />
}
export const DropdownMenuSeparator = DropdownMenuPrimitive.Separator
