import { type ReactNode, useEffect } from "react"

interface SheetProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
}

export function Sheet({ open, onOpenChange, children }: SheetProps) {
  // Close on ESC
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false)
    }
    document.addEventListener("keydown", handleKeyDown)
    return () => document.removeEventListener("keydown", handleKeyDown)
  }, [onOpenChange])

  return (
    <>
      {/* Overlay */}
      {open && (
        <div
          className="fixed inset-0 bg-black/50 z-40"
          onClick={() => onOpenChange(false)}
        />
      )}

      {/* Sheet Content */}
      <div
        className={`fixed top-0 right-0 h-full w-[400px] sm:w-[480px] bg-white shadow-xl z-50 transform transition-transform duration-300 ${
          open ? "translate-x-0" : "translate-x-full"
        }`}
      >
        {children}
      </div>
    </>
  )
}

export function SheetContent({
  children,
  className = "",
}: {
  children: ReactNode
  className?: string
}) {
  return <div className={`flex flex-col h-full ${className}`}>{children}</div>
}

export function SheetHeader({ children }: { children: ReactNode }) {
  return <div className="border-b p-4 flex items-center justify-between">{children}</div>
}

export function SheetTitle({ children }: { children: ReactNode }) {
  return <h2 className="text-lg font-semibold">{children}</h2>
}

export function SheetTrigger({
  children,
  onClick,
}: {
  children: ReactNode
  onClick?: () => void
}) {
  return (
    <div onClick={onClick} className="cursor-pointer">
      {children}
    </div>
  )
}

export function SheetClose({
  children,
  onClick,
}: {
  children: ReactNode
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className="cursor-pointer p-1 rounded hover:bg-gray-100"
    >
      {children}
    </div>
  )
}
