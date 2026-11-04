import { textDirection } from '@/lib/kitchen-format'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import type {
  RecipeData,
  Instruction,
  HowToStep,
  HowToSection,
} from '@/types'

function parseDuration(iso: string | null | undefined): string {
  if (!iso) return ''
  const m = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/)
  if (!m) return iso
  const h = parseInt(m[1] ?? '0')
  const min = parseInt(m[2] ?? '0')
  const parts: string[] = []
  if (h) parts.push(`${h} hr`)
  if (min) parts.push(`${min} min`)
  return parts.join(' ') || iso
}

function toStringArray(val: string[] | string | null | undefined): string[] {
  if (!val) return []
  if (Array.isArray(val)) return val
  return [val]
}

function isHowToSection(inst: Instruction): inst is HowToSection {
  return typeof inst === 'object' && inst['@type'] === 'HowToSection'
}

function isHowToStep(inst: Instruction): inst is HowToStep {
  return typeof inst === 'object' && inst['@type'] === 'HowToStep'
}

interface StepListProps {
  instructions: Instruction[]
  dir?: 'rtl' | 'ltr'
}

function instructionTexts(instructions: Instruction[] | undefined): string[] {
  return (instructions ?? []).flatMap((inst) => {
    if (isHowToSection(inst)) return inst.itemListElement.map((s) => s.text)
    return [isHowToStep(inst) ? inst.text : inst]
  })
}

function StepList({ instructions, dir }: StepListProps) {
  let counter = 0
  return (
    <ol className="space-y-2" dir={dir}>
      {instructions.map((inst, i) => {
        if (isHowToSection(inst)) {
          return (
            <li key={i}>
              <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                {inst.name}
              </p>
              <ol className="space-y-1.5 ps-2">
                {inst.itemListElement.map((step, j) => {
                  counter++
                  const n = counter
                  return (
                    <li key={j} className="flex gap-2">
                      <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
                        {n}
                      </span>
                      <span>{step.text}</span>
                    </li>
                  )
                })}
              </ol>
            </li>
          )
        }
        counter++
        const n = counter
        const text = isHowToStep(inst) ? inst.text : inst
        return (
          <li key={i} className="flex gap-2">
            <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-medium text-primary">
              {n}
            </span>
            <span>{text}</span>
          </li>
        )
      })}
    </ol>
  )
}

interface RecipeViewProps {
  recipe: RecipeData
}

export function RecipeView({ recipe }: RecipeViewProps) {
  const prepTime = parseDuration(recipe.prepTime)
  const cookTime = parseDuration(recipe.cookTime)
  const totalTime = parseDuration(recipe.totalTime)

  const structuredIngredients = recipe.recipeIngredients ?? []
  const flatIngredients = recipe.recipeIngredient ?? []
  const hasStructured = structuredIngredients.length > 0
  const contentDir = textDirection(
    typeof recipe.inLanguage === 'string' ? recipe.inLanguage : '',
    recipe.name,
    ...flatIngredients.slice(0, 5),
    ...structuredIngredients.slice(0, 5).map((i) => i.food),
    ...instructionTexts(recipe.recipeInstructions).slice(0, 3),
  )

  const categories = toStringArray(recipe.recipeCategory)
  const cuisines = toStringArray(recipe.recipeCuisine)
  const keywords = toStringArray(recipe.keywords)
    .flatMap((k) => k.split(',').map((s) => s.trim()))
    .filter(Boolean)

  const nutrition = recipe.nutrition
  const nutritionChips: { label: string; value: string }[] = []
  if (nutrition) {
    if (nutrition.calories) nutritionChips.push({ label: 'Calories', value: nutrition.calories })
    if (nutrition.proteinContent) nutritionChips.push({ label: 'Protein', value: nutrition.proteinContent })
    if (nutrition.fatContent) nutritionChips.push({ label: 'Fat', value: nutrition.fatContent })
    if (nutrition.carbohydrateContent) nutritionChips.push({ label: 'Carbs', value: nutrition.carbohydrateContent })
    if (nutrition.fiberContent) nutritionChips.push({ label: 'Fiber', value: nutrition.fiberContent })
    if (nutrition.sugarContent) nutritionChips.push({ label: 'Sugar', value: nutrition.sugarContent })
    if (nutrition.sodiumContent) nutritionChips.push({ label: 'Sodium', value: nutrition.sodiumContent })
  }

  return (
    <ScrollArea className="max-h-[60vh]">
      <div className="space-y-4 pe-2">
        <div dir={contentDir}>
          <h2 className="text-lg font-semibold">{recipe.name}</h2>
          {recipe.description && (
            <p className="mt-1 text-sm text-muted-foreground">{recipe.description}</p>
          )}
        </div>

        {(recipe.recipeYield || prepTime || cookTime || totalTime) && (
          <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
            {recipe.recipeYield && (
              <span>
                <span className="font-medium text-foreground">Yield</span>{' '}
                {recipe.recipeYield}
              </span>
            )}
            {prepTime && (
              <span>
                <span className="font-medium text-foreground">Prep</span>{' '}
                {prepTime}
              </span>
            )}
            {cookTime && (
              <span>
                <span className="font-medium text-foreground">Cook</span>{' '}
                {cookTime}
              </span>
            )}
            {totalTime && (
              <span>
                <span className="font-medium text-foreground">Total</span>{' '}
                {totalTime}
              </span>
            )}
          </div>
        )}

        {(categories.length > 0 || cuisines.length > 0 || keywords.length > 0) && (
          <div className="flex flex-wrap gap-1">
            {categories.map((c) => (
              <Badge key={c} variant="secondary">{c}</Badge>
            ))}
            {cuisines.map((c) => (
              <Badge key={c} variant="outline">{c}</Badge>
            ))}
            {keywords.map((k) => (
              <Badge key={k} variant="ghost">{k}</Badge>
            ))}
          </div>
        )}

        <Separator />

        {(hasStructured || flatIngredients.length > 0) && (
          <div>
            <h3 className="mb-2 text-sm font-semibold">Ingredients</h3>
            {hasStructured ? (
              <div className="grid grid-cols-1 gap-1 sm:grid-cols-2" dir={contentDir}>
                {structuredIngredients.map((ing, i) => (
                  <div key={i} className="flex gap-1 text-sm">
                    <span className="font-medium text-foreground">
                      {[ing.quantity, ing.unit].filter(Boolean).join('\u00a0')}
                    </span>
                    <span className="text-muted-foreground">
                      {ing.food}
                      {ing.notes ? ` (${ing.notes})` : ''}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-1 sm:grid-cols-2" dir={contentDir}>
                {flatIngredients.map((ing, i) => (
                  <p key={i} className="text-sm">{ing}</p>
                ))}
              </div>
            )}
          </div>
        )}

        {recipe.recipeInstructions && recipe.recipeInstructions.length > 0 && (
          <>
            <Separator />
            <div>
              <h3 className="mb-2 text-sm font-semibold">Instructions</h3>
              <StepList instructions={recipe.recipeInstructions} dir={contentDir} />
            </div>
          </>
        )}

        {nutritionChips.length > 0 && (
          <>
            <Separator />
            <div>
              <h3 className="mb-2 text-sm font-semibold">Nutrition</h3>
              <div className="flex flex-wrap gap-2">
                {nutritionChips.map(({ label, value }) => (
                  <div
                    key={label}
                    className="rounded-lg bg-muted px-2 py-1 text-xs"
                  >
                    <span className="text-muted-foreground">{label}: </span>
                    <span className="font-medium">{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </ScrollArea>
  )
}
