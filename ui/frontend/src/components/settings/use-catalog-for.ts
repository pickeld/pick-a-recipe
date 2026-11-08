import { useQuery } from '@tanstack/react-query'

import { api } from '@/lib/api'
import type { AiProvider } from '@/types'

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/** Which model catalog to suggest from for a provider, if any. */
export function useCatalogFor() {
  const { data } = useQuery({
    queryKey: ['aiProviders'],
    queryFn: api.aiProviders,
    staleTime: 30_000,
  })
  return (provider: AiProvider | undefined): string => {
    if (!provider) return ''
    const spec = data?.api_types[provider.api_type]
    if (spec?.catalog) return spec.catalog
    return data?.compatible_catalogs[hostOf(provider.base_url)] ?? ''
  }
}
