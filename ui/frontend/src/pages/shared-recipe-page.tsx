import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { useParams } from 'react-router-dom'
import { ChefHat, ExternalLink, Printer } from 'lucide-react'

import { kitchen } from '@/lib/kitchen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Minutes, RecipeImage, errorText } from '@/components/kitchen/bits'
import { IngredientList, NutritionPanel, ServingsStepper, StepList } from '@/components/kitchen/recipe-parts'
import { TimerTray } from '@/components/kitchen/timers'

/** Read-only public view of a recipe shared by link; rendered without the app shell or a session. */
export function SharedRecipePage() {
  const { t } = useTranslation()
  const { token = '' } = useParams()
  const [servings, setServings] = useState<number | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const { data: recipe, error, isLoading } = useQuery({
    queryKey: ['kitchen', 'shared', token, servings],
    queryFn: () => kitchen.shared(token, servings),
    placeholderData: keepPreviousData,
    retry: false,
  })

  if (isLoading) return <p className="p-8 text-center text-muted-foreground">{t('common.loading')}</p>
  if (error || !recipe) {
    return (
      <div className="flex min-h-svh flex-col items-center justify-center gap-3 p-8 text-center">
        <ChefHat className="size-10 text-muted-foreground" />
        <p className="font-medium">{t('share.gone')}</p>
        {error && <p className="text-sm text-muted-foreground">{errorText(error)}</p>}
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-4xl p-4 sm:p-8" lang={recipe.language || undefined}>
      <header className="mb-6 grid gap-4 sm:grid-cols-[1fr_16rem]">
        <div className="grid content-start gap-2">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><ChefHat className="size-4" />{t('share.sharedWithYou')}</p>
          <h1 className="text-2xl font-semibold tracking-tight" dir="auto">{recipe.name}</h1>
          {recipe.description && <p className="text-muted-foreground" dir="auto">{recipe.description}</p>}
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Minutes value={recipe.total_minutes} />
            {recipe.tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}
          </div>
          <div className="no-print flex flex-wrap items-center gap-2">
            {recipe.base_servings && <ServingsStepper value={servings ?? recipe.base_servings} onChange={setServings} />}
            <Button variant="outline" size="sm" onClick={() => window.print()}><Printer className="size-4" />{t('recipe.print')}</Button>
            {recipe.source_url && (
              <Button variant="ghost" size="sm" asChild>
                <a href={recipe.source_url} target="_blank" rel="noopener noreferrer"><ExternalLink className="size-4" />{t('recipe.source')}</a>
              </Button>
            )}
          </div>
        </div>
        {recipe.image_url && <RecipeImage src={recipe.image_url} alt={recipe.name} className="aspect-square w-full rounded-xl" />}
      </header>

      <div className="grid gap-8 md:grid-cols-[18rem_1fr]">
        <section>
          <h2 className="mb-2 font-semibold">{t('recipe.ingredients')}</h2>
          <IngredientList
            ingredients={recipe.ingredients}
            checked={checked}
            onToggle={(p) => setChecked((s) => {
              const next = new Set(s)
              if (next.has(p)) next.delete(p)
              else next.add(p)
              return next
            })}
          />
        </section>
        <section className="grid content-start gap-6">
          <div>
            <h2 className="mb-2 font-semibold">{t('recipe.steps')}</h2>
            <StepList steps={recipe.steps} recipeName={recipe.name} />
          </div>
          {(recipe.nutrition?.usable ?? 0) > 0 && <NutritionPanel nutrition={recipe.nutrition} servings={recipe.servings} />}
        </section>
      </div>
      <TimerTray />
    </div>
  )
}
