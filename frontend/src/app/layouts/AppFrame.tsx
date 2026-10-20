import { useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { AboutPanel } from '@/widgets/about-panel'
import { AppHeader, type RunStatus } from '@/widgets/app-header'
import { ProviderPanel } from '@/widgets/provider-panel'
import styles from './AppFrame.module.css'

type Panel = 'provider' | 'about' | null

interface AppFrameProps {
  projectName?: ReactNode
  status?: RunStatus
  rail?: ReactNode
  top?: ReactNode
  children: ReactNode
}

export function AppFrame({ projectName, status, rail, top, children }: AppFrameProps) {
  const [panel, setPanel] = useState<Panel>(null)
  const [params, setParams] = useSearchParams()
  const providerFromLink = params.get('panel') === 'provider'
  const providerSession = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const closeWhen = (open: boolean) => {
    if (!open) {
      setPanel(null)
      if (providerFromLink) setParams((prev) => { const next = new URLSearchParams(prev); next.delete('panel'); return next }, { replace: true })
    }
  }

  return (
    <div className={styles.frame}>
      <AppHeader
        projectName={projectName}
        status={status}
        providerConnected={Boolean(providerSession) || modelAuto}
        onOpenProvider={() => setPanel('provider')}
        onOpenAbout={() => setPanel('about')}
      />
      {top}
      <div className={styles.body}>
        {rail}
        <main className={styles.main}>{children}</main>
      </div>
      <ProviderPanel open={panel === 'provider' || providerFromLink} onOpenChange={closeWhen} />
      <AboutPanel open={panel === 'about'} onOpenChange={closeWhen} />
    </div>
  )
}
