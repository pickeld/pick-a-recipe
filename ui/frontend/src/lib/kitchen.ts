/**
 * Typed client for the kitchen API (/api/kitchen/*): library, planner,
 * shopping, Sous Chef and importers.
 */
import { ApiError, request } from '@/lib/api'

// ===== Types =====

export type UnitSystem = 'original' | 'metric' | 'us'
export type Slot = 'breakfast' | 'lunch' | 'dinner' | 'snack'
export const SLOTS: Slot[] = ['breakfast', 'lunch', 'dinner', 'snack']

export const NUTRIENT_KEYS = [
  'calories', 'proteinContent', 'fatContent', 'carbohydrateContent',
  'fiberContent', 'sugarContent', 'sodiumContent', 'cholesterolContent',
] as const
export type NutrientKey = (typeof NUTRIENT_KEYS)[number]
export type Nutrients = Partial<Record<NutrientKey, number>>

export interface RecipeSummary {
  id: string
  slug: string
  name: string
  description: string
  servings: number | null
  total_minutes: number | null
  image_url: string | null
  tags: string[]
  favorite: boolean
  rating: number | null
  last_cooked: string | null
  cook_count: number
  calories: number | null
  source_kind: string
  language: string
  created_at: string
}

export interface RecipeList {
  items: RecipeSummary[]
  total: number
  limit: number
  offset: number
}

export interface Ingredient {
  id: number
  position: number
  section: string
  food: string
  quantity: string
  unit: string
  notes: string
  raw: string
  optional: boolean
  scalable: boolean
  amount: number | null
  amount_max: number | null
  display_quantity: string
  display_unit: string
}

export interface StepTimer {
  seconds: number
  max_seconds: number | null
  label: string
}

export interface Step {
  position: number
  section: string
  text: string
  timers: StepTimer[]
  ingredient_refs: number[]
}

export interface Nutrition {
  per_serving: Nutrients
  total: Nutrients
  matched: number
  usable: number
  source: 'auto' | 'manual' | 'source'
  breakdown: { index: number; food: string; matched: boolean; grams: number | null; kcal: number | null }[]
}

export interface UserMeta {
  favorite: boolean
  rating: number | null
  note: string
  cook_count: number
  last_cooked: string | null
}

export interface Recipe {
  id: string
  slug: string
  name: string
  description: string
  servings: number | null
  base_servings: number | null
  yield_text: string
  prep_time: string
  cook_time: string
  total_time: string
  total_minutes: number | null
  source_url: string
  source_kind: string
  language: string
  cuisine: string
  category: string
  tags: string[]
  images: { id: string; url: string }[]
  image_url: string | null
  ingredients: Ingredient[]
  steps: Step[]
  nutrition: Nutrition | null
  collections: { id: number; name: string }[]
  user: UserMeta
  units: UnitSystem
  created_by: string | null
  created_at: string
  updated_at: string
  share_links?: number
}

export type SharedRecipe = Pick<Recipe,
  'name' | 'description' | 'servings' | 'base_servings' | 'yield_text' | 'prep_time' | 'cook_time' |
  'total_time' | 'total_minutes' | 'source_url' | 'language' | 'tags' | 'ingredients' | 'steps' |
  'nutrition' | 'images' | 'image_url'>

export interface RecipeDraft {
  name: string
  description?: string
  servings?: number | string | null
  prep_time?: string
  cook_time?: string
  total_time?: string
  source_url?: string
  tags?: string[]
  ingredients: string[]
  steps: string[]
  source_kind?: 'manual' | 'ai' | 'import'
}

export interface RecipePatch {
  name?: string
  description?: string
  servings?: number | null
  prep_time?: string
  cook_time?: string
  total_time?: string
  source_url?: string
  tags?: string[]
  ingredients?: string[]
  steps?: string[]
}

export interface Collection {
  id: number
  name: string
  description: string
  count: number
  image_url: string | null
}

export interface CookEntry {
  id: number
  recipe_id: string
  name: string
  cooked_on: string
  servings: number | null
  note: string
}

export interface CookStats {
  year: number
  times_cooked: number
  distinct_recipes: number
  top: { recipe_id: string; name: string; times: number }[]
  by_month: number[]
  library_size: number
  added_this_year: number
}

export interface PlanEntry {
  id: number
  date: string
  slot: Slot
  position: number
  recipe_id: string | null
  title: string
  custom_title: string
  servings: number | null
  recipe_servings: number | null
  total_minutes: number | null
  image_url: string | null
  leftover_of: number | null
  note: string
  per_serving: Nutrients | null
  created_by: string | null
}

export interface PlanDay {
  meals: number
  covered: number
  totals: Nutrients
}

export interface PlanResponse {
  entries: PlanEntry[]
  nutrition: Record<string, PlanDay>
}

export interface ShoppingItem {
  id: number
  list_id: number
  food: string
  quantity: number | null
  unit: string
  aisle: string
  checked: boolean
  excluded: boolean
  position: number
  note: string
  sources: { id: string; name: string }[]
  manual: boolean
  updated_at: string
}

export interface ShoppingPayload {
  list_id: number
  groups: { aisle: string; items: ShoppingItem[] }[]
  checked: ShoppingItem[]
  excluded: ShoppingItem[]
  aisles: string[]
}

export interface ShoppingList {
  id: number
  name: string
  open_items: number | null
}

export interface Candidate {
  id: string
  name: string
  total_minutes: number | null
  tags: string[]
  image_url: string | null
  coverage: number
  have: string[]
  missing: string[]
  mentioned?: string[]
  score: number
  favorite: boolean
  last_cooked: string | null
}

export interface SousChefPick {
  recipe: Candidate
  why: string
  substitutions: { missing: string; use: string }[]
}

export interface SuggestResult {
  candidates: Candidate[]
  picks: SousChefPick[]
  reply: string
  ai: boolean
  error?: string
}

export interface ChatTurn {
  role: 'user' | 'assistant'
  text: string
}

export interface SchemaRecipe {
  name: string
  description?: string
  recipeYield?: string
  recipeIngredient?: string[]
  recipeIngredients?: { food: string; quantity: string; unit: string; notes: string; raw: string }[]
  recipeInstructions?: { text: string }[]
  [key: string]: unknown
}

export interface WeekPlanResult {
  summary: string
  assignments: { date: string; slot: Slot; recipe_id: string; name: string; image_url: string | null; why: string }[]
}

export interface ImportPreview {
  index: number
  name: string
  ingredients: number
  steps: number
  url: string
  duplicate_of: string | null
}

export interface StagedImport {
  token: string
  kind: string
  count: number
  recipes: ImportPreview[]
  draft?: SchemaRecipe
  has_image?: boolean
}

export interface ImportJob {
  id: string
  kind: string
  status: 'running' | 'done' | 'failed' | 'cancelled'
  total: number
  done: number
  imported: number
  skipped: number
  failed: { id: string; error: string }[]
  error?: string
}

export type OnDuplicate = 'skip' | 'replace' | 'keep_both'

export interface ListParams {
  q?: string
  tags?: string[]
  exclude_tags?: string[]
  exclude_ingredients?: string[]
  include_ingredients?: string[]
  max_time?: number
  favorites?: boolean
  collection?: number
  sort?: string
  limit?: number
  offset?: number
}

// ===== Helpers =====

const K = '/api/kitchen'

function qs(params: Record<string, unknown>): string {
  const sp = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '' || value === false) continue
    if (Array.isArray(value)) {
      if (value.length) sp.set(key, value.join(','))
    } else {
      sp.set(key, value === true ? '1' : String(value))
    }
  }
  const s = sp.toString()
  return s ? `?${s}` : ''
}

async function upload<T>(path: string, form: FormData): Promise<T> {
  return request<T>(path, { method: 'POST', body: form })
}

export function isUnavailable(err: unknown): boolean {
  return err instanceof ApiError && err.status === 503
}

// ===== API =====

export const kitchen = {
  // Library
  listRecipes: (p: ListParams = {}) => request<RecipeList>(`${K}/recipes${qs({ ...p })}`),
  getRecipe: (id: string, opts: { servings?: number | null; units?: UnitSystem } = {}) =>
    request<Recipe>(`${K}/recipes/${encodeURIComponent(id)}${qs({
      servings: opts.servings ?? undefined,
      units: opts.units && opts.units !== 'original' ? opts.units : undefined,
    })}`),
  createRecipe: (draft: RecipeDraft) => request<Recipe>(`${K}/recipes`, { method: 'POST', json: draft }),
  updateRecipe: (id: string, patch: RecipePatch) =>
    request<Recipe>(`${K}/recipes/${encodeURIComponent(id)}`, { method: 'PATCH', json: patch }),
  deleteRecipe: (id: string) => request<{ status: string }>(`${K}/recipes/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  setNutrition: (id: string, perServing: Nutrients) =>
    request<Recipe>(`${K}/recipes/${encodeURIComponent(id)}/nutrition`, { method: 'PUT', json: { per_serving: perServing } }),
  resetNutrition: (id: string) =>
    request<Recipe>(`${K}/recipes/${encodeURIComponent(id)}/nutrition`, { method: 'DELETE' }),
  setMeta: (id: string, meta: Partial<Pick<UserMeta, 'favorite' | 'rating' | 'note'>>) =>
    request<UserMeta>(`${K}/recipes/${encodeURIComponent(id)}/me`, { method: 'PUT', json: meta }),
  markCooked: (id: string, body: { servings?: number | null; note?: string; cooked_on?: string }) =>
    request<{ id: number }>(`${K}/recipes/${encodeURIComponent(id)}/cooked`, { method: 'POST', json: body }),
  cookLog: (recipeId?: string) => request<{ entries: CookEntry[] }>(`${K}/cook-log${qs({ recipe: recipeId })}`),
  deleteCookEntry: (id: number) => request<{ status: string }>(`${K}/cook-log/${id}`, { method: 'DELETE' }),
  stats: (year?: number) => request<CookStats>(`${K}/stats${qs({ year })}`),
  tags: () => request<{ tags: { name: string; count: number }[] }>(`${K}/tags`),

  uploadImage: (id: string, file: File, cover = false) => {
    const form = new FormData()
    form.append('image', file)
    if (cover) form.append('cover', '1')
    return upload<{ id: string; url: string }>(`${K}/recipes/${encodeURIComponent(id)}/images`, form)
  },
  deleteImage: (id: string, imageId: string) =>
    request<{ status: string }>(`${K}/recipes/${encodeURIComponent(id)}/images/${encodeURIComponent(imageId)}`, { method: 'DELETE' }),
  setCover: (id: string, imageId: string) =>
    request<{ status: string }>(`${K}/recipes/${encodeURIComponent(id)}/images/${encodeURIComponent(imageId)}/cover`, { method: 'POST' }),

  collections: () => request<{ collections: Collection[] }>(`${K}/collections`),
  createCollection: (name: string, description = '') =>
    request<{ id: number }>(`${K}/collections`, { method: 'POST', json: { name, description } }),
  updateCollection: (id: number, patch: { name?: string; description?: string }) =>
    request<{ status: string }>(`${K}/collections/${id}`, { method: 'PATCH', json: patch }),
  deleteCollection: (id: number) => request<{ status: string }>(`${K}/collections/${id}`, { method: 'DELETE' }),
  setMembership: (collectionId: number, recipeId: string, member: boolean) =>
    request<{ status: string }>(`${K}/collections/${collectionId}/recipes/${encodeURIComponent(recipeId)}`, {
      method: member ? 'PUT' : 'DELETE',
    }),

  exportUrl: (id: string) => `${K}/recipes/${encodeURIComponent(id)}/export`,
  libraryExportUrl: () => `${K}/export`,
  share: (id: string) => request<{ url: string; token: string }>(`${K}/recipes/${encodeURIComponent(id)}/share`, { method: 'POST' }),
  unshare: (id: string) => request<{ revoked: number }>(`${K}/recipes/${encodeURIComponent(id)}/share`, { method: 'DELETE' }),
  shared: (token: string, servings?: number | null) =>
    request<SharedRecipe>(`/api/public/share/${encodeURIComponent(token)}${qs({ servings: servings ?? undefined })}`, { handle401: true }),

  // Planner
  plan: (start: string, end: string) => request<PlanResponse>(`${K}/plan${qs({ start, end })}`),
  addPlanEntry: (entry: { date: string; slot: Slot; recipe_id?: string | null; title?: string; servings?: number | null; leftover_of?: number | null; note?: string }) =>
    request<{ id: number }>(`${K}/plan`, { method: 'POST', json: entry }),
  updatePlanEntry: (id: number, patch: Partial<{ date: string; slot: Slot; servings: number | null; title: string; note: string; position: number }>) =>
    request<{ status: string }>(`${K}/plan/${id}`, { method: 'PATCH', json: patch }),
  deletePlanEntry: (id: number) => request<{ status: string }>(`${K}/plan/${id}`, { method: 'DELETE' }),
  copyPlan: (start: string, end: string, destStart: string) =>
    request<{ copied: number }>(`${K}/plan/copy`, { method: 'POST', json: { start, end, dest_start: destStart } }),
  clearPlan: (start: string, end: string) =>
    request<{ removed: number }>(`${K}/plan/clear`, { method: 'POST', json: { start, end } }),
  icsStatus: () => request<{ enabled: boolean }>(`${K}/plan/ics`),
  icsRotate: () => request<{ url: string }>(`${K}/plan/ics`, { method: 'POST' }),
  icsRevoke: () => request<{ enabled: boolean }>(`${K}/plan/ics`, { method: 'DELETE' }),

  // Shopping
  shoppingLists: () => request<{ lists: ShoppingList[] }>(`${K}/shopping/lists`),
  createShoppingList: (name: string) => request<{ id: number }>(`${K}/shopping/lists`, { method: 'POST', json: { name } }),
  renameShoppingList: (id: number, name: string) =>
    request<{ status: string }>(`${K}/shopping/lists/${id}`, { method: 'PATCH', json: { name } }),
  deleteShoppingList: (id: number) => request<{ status: string }>(`${K}/shopping/lists/${id}`, { method: 'DELETE' }),
  shopping: (listId?: number) => request<ShoppingPayload>(`${K}/shopping${qs({ list: listId })}`),
  addShoppingItem: (item: { list_id?: number; food: string; quantity?: string; unit?: string }) =>
    request<{ id: number }>(`${K}/shopping/items`, { method: 'POST', json: item }),
  updateShoppingItem: (id: number, patch: Record<string, unknown>) =>
    request<ShoppingItem & { stale?: boolean }>(`${K}/shopping/items/${id}`, { method: 'PATCH', json: patch }),
  deleteShoppingItem: (id: number) => request<{ status: string }>(`${K}/shopping/items/${id}`, { method: 'DELETE' }),
  syncShopping: (ops: unknown[]) =>
    request<{ results: { id?: number; temp_id?: string; ok: boolean; stale?: boolean; error?: string }[] }>(
      `${K}/shopping/sync`, { method: 'POST', json: { ops } }),
  generateShopping: (body: { list_id?: number; recipe_id?: string; servings?: number | null; only_foods?: string[]; start?: string; end?: string }) =>
    request<ShoppingPayload & { added: number; recipes?: number }>(`${K}/shopping/generate`, { method: 'POST', json: body }),
  clearShopping: (listId: number, all = false) =>
    request<{ removed: number }>(`${K}/shopping/clear`, { method: 'POST', json: { list_id: listId, all } }),

  // Sous Chef
  diets: () => request<{ diets: string[] }>(`${K}/souschef/diets`),
  suggest: (body: { ingredients: string[]; message?: string; history?: ChatTurn[]; max_minutes?: number | null; diets?: string[]; tags?: string[]; ai?: boolean; lang?: string }) =>
    request<SuggestResult>(`${K}/souschef/suggest`, { method: 'POST', json: body }),
  invent: (body: { ingredients: string[]; message?: string; diets?: string[]; max_minutes?: number | null }) =>
    request<{ draft: SchemaRecipe }>(`${K}/souschef/invent`, { method: 'POST', json: body }),
  saveDraft: (draft: SchemaRecipe) => request<{ id: string }>(`${K}/souschef/save-draft`, { method: 'POST', json: { draft } }),
  substitute: (food: string, recipeId?: string) =>
    request<{ options: { use: string; amount: string; notes: string }[] }>(`${K}/souschef/substitute`, {
      method: 'POST', json: { food, recipe_id: recipeId },
    }),
  planWeek: (body: { start: string; end: string; slots: Slot[]; message?: string }) =>
    request<WeekPlanResult>(`${K}/souschef/plan-week`, { method: 'POST', json: body }),
  applyPlan: (assignments: WeekPlanResult['assignments'], servings?: number | null) =>
    request<{ added: number }>(`${K}/souschef/plan-week/apply`, { method: 'POST', json: { assignments, servings } }),
  review: (id: string) =>
    request<{ summary: string; issues: { kind: string; detail: string; suggestion: string }[] }>(
      `${K}/recipes/${encodeURIComponent(id)}/ai/review`, { method: 'POST' }),
  suggestTags: (id: string, apply = false) =>
    request<{ tags: string[] }>(`${K}/recipes/${encodeURIComponent(id)}/ai/tags`, { method: 'POST', json: { apply } }),
  linkSteps: (id: string) =>
    request<{ updated: number }>(`${K}/recipes/${encodeURIComponent(id)}/ai/link-steps`, { method: 'POST' }),

  // Import
  importSources: () => request<{ mealie: boolean; tandoor: boolean; admin: boolean }>(`${K}/import/sources`),
  importFile: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return upload<StagedImport>(`${K}/import/file`, form)
  },
  importPhotos: (files: File[]) => {
    const form = new FormData()
    for (const f of files) form.append('photos', f)
    return upload<StagedImport>(`${K}/import/photos`, form)
  },
  importSchema: (json: string) => request<StagedImport>(`${K}/import/schema`, { method: 'POST', json: { json } }),
  importUrl: (url: string) =>
    request<StagedImport | { pipeline: true; duplicate_of: string | null }>(`${K}/import/url`, { method: 'POST', json: { url } }),
  commitImport: (body: { token: string; indexes?: number[]; on_duplicate?: OnDuplicate; overrides?: Record<string, SchemaRecipe>; use_image?: boolean }) =>
    request<{ imported: number; ids: string[]; skipped: number; failed: { index: number; name: string; error: string }[] }>(
      `${K}/import/commit`, { method: 'POST', json: body }),
  remoteList: (kind: 'mealie' | 'tandoor') =>
    request<{ recipes: { source_id: string; name: string; url: string; duplicate_of: string | null }[] }>(`${K}/import/remote/${kind}`),
  remoteImport: (kind: 'mealie' | 'tandoor', body: { ids: string[] | 'all'; on_duplicate: OnDuplicate }) =>
    request<{ job_id: string }>(`${K}/import/remote/${kind}`, { method: 'POST', json: body }),
  importJob: (id: string) => request<ImportJob>(`${K}/import/jobs/${encodeURIComponent(id)}`),
  cancelImportJob: (id: string) => request<{ status: string }>(`${K}/import/jobs/${encodeURIComponent(id)}`, { method: 'DELETE' }),
}
