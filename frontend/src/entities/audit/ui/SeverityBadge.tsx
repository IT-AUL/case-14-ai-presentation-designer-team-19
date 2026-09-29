import { Badge } from '@/shared/ui'
import { SEVERITY } from '../model/severity'
import type { Severity } from '../model/types'

export function SeverityBadge({ severity, short }: { severity: Severity; short?: boolean }) {
  const meta = SEVERITY[severity]
  return (
    <Badge tone={meta.tone} shape="tag">
      {short ? meta.shortLabel : meta.label}
    </Badge>
  )
}
