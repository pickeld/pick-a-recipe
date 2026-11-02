import { useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { useSocketEvent } from '@/lib/socket'

const TOPIC_KEYS: Record<string, string[][]> = {
  recipes: [['kitchen', 'recipes'], ['kitchen', 'recipe'], ['kitchen', 'tags'], ['kitchen', 'plan']],
  collections: [['kitchen', 'collections'], ['kitchen', 'recipes']],
  plan: [['kitchen', 'plan']],
  shopping: [['kitchen', 'shopping']],
  pantry: [['kitchen', 'pantry']],
  import: [['kitchen', 'import']],
}

/** Everyone shares one kitchen: refetch whatever another user just changed. */
export function useKitchenLive() {
  const queryClient = useQueryClient()
  useSocketEvent(
    'kitchen_changed',
    useCallback(
      (payload: { topic: string }) => {
        for (const queryKey of TOPIC_KEYS[payload.topic] ?? []) {
          void queryClient.invalidateQueries({ queryKey })
        }
      },
      [queryClient],
    ),
  )
}
