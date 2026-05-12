'use client'

import { useEffect } from 'react'

/**
 * Sets the browser tab title. Appends " · KPI Creatives" suffix.
 * Pass null/undefined to reset to default "KPI Creatives".
 */
export function usePageTitle(title: string | null | undefined) {
  useEffect(() => {
    document.title = title ? `${title} – FreeFrame` : 'KPI Creatives'
    return () => { document.title = 'KPI Creatives' }
  }, [title])
}
