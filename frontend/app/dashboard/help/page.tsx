import { HelpCircle } from 'lucide-react'

export default function HelpPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Справка</h1>
        <p className="text-sm text-muted-foreground mt-1">Документация и руководство пользователя</p>
      </div>
      <div className="rounded-xl border border-border bg-card p-12 flex flex-col items-center justify-center gap-3">
        <HelpCircle className="h-10 w-10 text-muted-foreground" />
        <p className="text-muted-foreground text-sm">Справочный раздел находится в разработке</p>
      </div>
    </div>
  )
}
