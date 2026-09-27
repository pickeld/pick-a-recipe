import { useEffect, useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PlusIcon, ServerIcon, Trash2Icon } from 'lucide-react'

import { api } from '@/lib/api'
import type { AiProvider, ApiTypeSpec } from '@/types'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

function slugify(name: string): string {
  return (
    name
      .trim()
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 48) || `provider-${Math.random().toString(36).slice(2, 10)}`
  )
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.toLowerCase()
  } catch {
    return ''
  }
}

/**
 * Model names are always free text. The datalist only saves people from having
 * to look an id up, so a gateway models.dev has never heard of still works.
 */
function ModelField({
  catalog,
  value,
  onChange,
  placeholder,
}: {
  catalog: string
  value: string
  onChange: (value: string) => void
  placeholder: string
}) {
  const listId = useId()
  const { data } = useQuery({
    queryKey: ['modelCatalog', catalog],
    queryFn: () => api.modelCatalog(catalog),
    enabled: Boolean(catalog),
    staleTime: 6 * 60 * 60 * 1000,
    retry: false,
  })

  return (
    <div className="flex flex-col gap-1.5">
      <Label>Model</Label>
      <Input
        type="text"
        list={listId}
        placeholder={placeholder}
        autoComplete="off"
        autoCapitalize="none"
        spellCheck={false}
        value={value}
        onChange={e => onChange(e.target.value)}
      />
      <datalist id={listId}>
        {(data?.models ?? []).map(model => (
          <option key={model.id} value={model.id} label={model.name} />
        ))}
      </datalist>
      <p className="text-xs text-muted-foreground">
        Any model name works. Suggestions come from models.dev.
      </p>
    </div>
  )
}

export function AiProvidersCard({
  providers,
  onChange,
}: {
  providers: AiProvider[] | null
  onChange: (providers: AiProvider[]) => void
}) {
  const { data } = useQuery({
    queryKey: ['aiProviders'],
    queryFn: api.aiProviders,
    staleTime: Infinity,
  })

  // The server does the legacy-settings migration, so the first render adopts
  // whatever it returns rather than guessing here.
  useEffect(() => {
    if (providers === null && data) onChange(data.providers)
  }, [providers, data, onChange])

  const apiTypes: Record<string, ApiTypeSpec> = data?.api_types ?? {}
  const catalogs: Record<string, string> = data?.compatible_catalogs ?? {}
  const rows = providers ?? data?.providers ?? []

  function update(index: number, patch: Partial<AiProvider>) {
    onChange(rows.map((row, i) => (i === index ? { ...row, ...patch } : row)))
  }

  function catalogFor(provider: AiProvider): string {
    const spec = apiTypes[provider.api_type]
    if (spec?.catalog) return spec.catalog
    return catalogs[hostOf(provider.base_url)] ?? ''
  }

  function add() {
    const firstType = Object.keys(apiTypes)[0] ?? 'openai'
    onChange([
      ...rows,
      { id: '', name: '', api_type: firstType, api_key: '', base_url: '', model: '' },
    ])
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <ServerIcon className="size-4" />
          AI Providers
        </CardTitle>
        <CardDescription>
          The models this instance can reach. A provider is an API dialect, a key, a
          model name and — for OpenAI-compatible endpoints — a base URL, which between
          them cover OpenAI, Gemini, Claude, OpenRouter, Groq, a local Ollama and
          anything else speaking one of those dialects.
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {rows.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No providers yet. Add one to start extracting recipes.
          </p>
        )}

        {rows.map((provider, index) => {
          const spec = apiTypes[provider.api_type]
          return (
            <div
              key={index}
              className="flex flex-col gap-4 rounded-lg border border-border p-4"
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="flex flex-col gap-1.5">
                  <Label>Name</Label>
                  <Input
                    type="text"
                    placeholder="OpenAI"
                    autoComplete="off"
                    value={provider.name}
                    onChange={e =>
                      update(index, {
                        name: e.target.value,
                        // Ids follow the name until the record has been saved
                        // once; renaming a saved provider must not orphan the
                        // sections pointing at it.
                        id: provider.id && data?.providers.some(p => p.id === provider.id)
                          ? provider.id
                          : slugify(e.target.value),
                      })
                    }
                  />
                </div>
                <div className="flex flex-col gap-1.5">
                  <Label>API type</Label>
                  <Select
                    value={provider.api_type}
                    onValueChange={v => update(index, { api_type: v })}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(apiTypes).map(([key, value]) => (
                        <SelectItem key={key} value={key}>
                          {value.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="flex flex-col gap-1.5">
                  <Label>API key</Label>
                  <Input
                    type="password"
                    placeholder="sk-..."
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={provider.api_key}
                    onChange={e => update(index, { api_key: e.target.value })}
                  />
                </div>
                <ModelField
                  catalog={catalogFor(provider)}
                  value={provider.model}
                  onChange={model => update(index, { model })}
                  placeholder={spec?.example_model ?? 'model-name'}
                />
              </div>

              {spec?.needs_base_url && (
                <div className="flex flex-col gap-1.5">
                  <Label>Base URL</Label>
                  <Input
                    type="url"
                    placeholder={spec.default_base_url || 'https://api.example.com/v1'}
                    autoComplete="off"
                    autoCapitalize="none"
                    spellCheck={false}
                    value={provider.base_url}
                    onChange={e => update(index, { base_url: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">
                    Where this endpoint lives. A local server (Ollama, LM Studio,
                    vLLM) needs no API key.
                  </p>
                </div>
              )}

              <div>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => onChange(rows.filter((_, i) => i !== index))}
                >
                  <Trash2Icon />
                  Remove
                </Button>
              </div>
            </div>
          )
        })}

        <div>
          <Button type="button" variant="secondary" onClick={add}>
            <PlusIcon />
            Add provider
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
