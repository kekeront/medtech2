import { Settings } from 'lucide-react'

export default function SettingsPage() {
  return (
    <div className="p-6 space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">Настройки</h1>
        <p className="text-sm text-muted-foreground mt-1">Конфигурация системы и параметры обработки</p>
      </div>
      <div className="rounded-xl border border-border bg-card p-12 flex flex-col items-center justify-center gap-3">
        <Settings className="h-10 w-10 text-muted-foreground" />
        <p className="text-muted-foreground text-sm">Настройки находятся в разработке</p>
      </div>
    </div>
  )
}
